"""Read and write lakehouse tables, with the schema taken from the catalog.

Why the schema comes from config/catalog.yaml:
    The catalog is not just documentation. When a pipeline writes a table,
    the column names and types are built from the catalog entry. If the code
    tries to write a column the catalog doesn't know about, the write fails.
    So the catalog and the physical table can never drift apart.

Two backends, same functions (ADR-001, ADR-007):

    local       Delta tables in ./data/lakehouse/<layer>/<table>, written with
                the `deltalake` library. Raw files in ./data/landing.

    databricks  Unity Catalog tables <catalog>.<layer>.<table>, written with
                Spark. Raw files in the Unity Catalog volume
                /Volumes/<catalog>/bronze/landing. When a table is first
                created, its description, column comments and tags are
                published from catalog.yaml into Unity Catalog.

The pipelines only ever call read(), append(), upsert(), exists() and
landing_root(). They never know which backend they're on.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pyarrow as pa

from careplatform import config

# catalog type -> Arrow type
_TYPES = {
    "string": pa.string(),
    "int": pa.int32(),
    "long": pa.int64(),
    "double": pa.float64(),
    "boolean": pa.bool_(),
    "timestamp": pa.timestamp("us", tz="UTC"),
    "array<string>": pa.list_(pa.string()),
    "array<float>": pa.list_(pa.float32()),
}

# catalog type -> Spark SQL type
_SPARK_TYPES = {
    "string": "STRING", "int": "INT", "long": "BIGINT", "double": "DOUBLE",
    "boolean": "BOOLEAN", "timestamp": "TIMESTAMP",
    "array<string>": "ARRAY<STRING>", "array<float>": "ARRAY<FLOAT>",
}


# ---------------- where are we? ----------------

def mode() -> str:
    return os.getenv("CAREPLATFORM_LAKEHOUSE_MODE") or config.settings()["lakehouse"]["mode"]


def uc_catalog() -> str:
    return os.getenv("CAREPLATFORM_CATALOG") or config.settings()["lakehouse"]["databricks_catalog"]


def uc_name(name: str) -> str:
    """'bronze.raw_documents' -> 'care_reg_dev.bronze.raw_documents'"""
    return f"{uc_catalog()}.{name}"


def _local_root() -> Path:
    s = config.settings()["lakehouse"]
    if mode() == "fabric":
        if not s.get("fabric_root"):
            raise RuntimeError("lakehouse.mode is fabric but fabric_root is not set")
        return Path(s["fabric_root"].rstrip("/"))
    root = Path(s["local_root"])
    return root if root.is_absolute() else config.REPO_ROOT / root


def table_path(name: str) -> str:
    """Local mode only: 'bronze.raw_documents' -> '<root>/bronze/raw_documents'"""
    layer, table = name.split(".", 1)
    return f"{_local_root()}/{layer}/{table}"


def landing_root() -> Path:
    """Where raw downloaded files are kept."""
    if mode() == "databricks":
        return Path(f"/Volumes/{uc_catalog()}/bronze/landing")
    root = Path(config.settings()["lakehouse"]["landing_root"])
    return root if root.is_absolute() else config.REPO_ROOT / root


# ---------------- schema from the catalog ----------------

def table_spec(name: str) -> dict:
    for t in config.catalog()["tables"]:
        if t["name"] == name:
            return t
    raise KeyError(f"{name} is not in config/catalog.yaml. Add it there first.")


def schema_for(name: str) -> pa.Schema:
    spec = table_spec(name)
    return pa.schema(
        [pa.field(c["name"], _TYPES[c["type"]], nullable=c["nullable"]) for c in spec["columns"]]
    )


def spark_ddl(name: str, constraints: bool = True) -> str:
    """Column list for Spark, e.g. 'doc_id STRING NOT NULL, page_no INT'.

    constraints=True is used for CREATE TABLE (Delta then enforces NOT NULL on
    every write). The in-memory DataFrame uses constraints=False; the table
    does the enforcing.
    """
    cols = []
    for c in table_spec(name)["columns"]:
        null = " NOT NULL" if constraints and not c["nullable"] else ""
        cols.append(f"{c['name']} {_SPARK_TYPES[c['type']]}{null}")
    return ", ".join(cols)


def to_arrow(name: str, df: pd.DataFrame) -> pa.Table:
    """Conform a DataFrame to the catalog schema. Unknown or missing columns fail loudly."""
    schema = schema_for(name)
    extra = set(df.columns) - set(schema.names)
    missing = set(schema.names) - set(df.columns)
    if extra:
        raise ValueError(f"{name}: columns not in catalog: {sorted(extra)}")
    if missing:
        raise ValueError(f"{name}: columns missing from data: {sorted(missing)}")
    df = df[schema.names].copy()
    for f in schema:
        if f.type == pa.timestamp("us", tz="UTC"):
            df[f.name] = pd.to_datetime(df[f.name], utc=True)
    return pa.Table.from_pandas(df, schema=schema, preserve_index=False)


def _sql_str(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def governance_statements(name: str) -> list[str]:
    """SQL that publishes a table's catalog entry into Unity Catalog (ADR-003)."""
    t = table_spec(name)
    owners = config.catalog()["domains"][t["domain"]]
    full = uc_name(name)
    stmts = [f"COMMENT ON TABLE {full} IS {_sql_str(t['description'])}"]
    for c in t["columns"]:
        stmts.append(f"ALTER TABLE {full} ALTER COLUMN {c['name']} COMMENT {_sql_str(c['description'])}")
    tags = {
        "layer": t["layer"],
        "domain": t["domain"],
        "classification": t["classification"],
        "retention_days": str(t["retention_days"]),
        "data_owner": owners["data_owner"],
        "data_steward": owners["data_steward"],
    }
    tag_sql = ", ".join(f"{_sql_str(k)} = {_sql_str(v)}" for k, v in tags.items())
    stmts.append(f"ALTER TABLE {full} SET TAGS ({tag_sql})")
    return stmts


# ---------------- Spark helpers (databricks mode) ----------------

def _spark():
    from pyspark.sql import SparkSession
    return SparkSession.builder.getOrCreate()


def _spark_df(name: str, df: pd.DataFrame):
    # via plain Python rows: keeps NULLs in integer columns as NULL (pandas would turn them into NaN floats)
    rows = to_arrow(name, df).to_pylist()
    return _spark().createDataFrame(rows, schema=spark_ddl(name, constraints=False))


def _publish_governance(name: str) -> None:
    spark = _spark()
    for stmt in governance_statements(name):
        try:
            spark.sql(stmt)
        except Exception as e:  # tags can be unavailable on some tiers; never fail a load over metadata
            print(f"[governance] skipped: {stmt[:80]}... ({type(e).__name__})")


def _uc_create(name: str) -> None:
    _spark().sql(f"CREATE TABLE IF NOT EXISTS {uc_name(name)} ({spark_ddl(name)}) USING DELTA")
    _publish_governance(name)


# ---------------- the API the pipelines use ----------------

def exists(name: str) -> bool:
    if mode() == "databricks":
        return _spark().catalog.tableExists(uc_name(name))
    try:
        from deltalake import DeltaTable
        DeltaTable(table_path(name))
        return True
    except Exception:
        return False


def read(name: str) -> pd.DataFrame:
    """Whole table as pandas. Returns an empty frame with the right columns if it doesn't exist yet."""
    if not exists(name):
        return schema_for(name).empty_table().to_pandas()
    if mode() == "databricks":
        return _spark().table(uc_name(name)).toPandas()
    from deltalake import DeltaTable
    return DeltaTable(table_path(name)).to_pandas()


def append(name: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    if mode() == "databricks":
        if not exists(name):
            _uc_create(name)
        _spark_df(name, df).write.mode("append").saveAsTable(uc_name(name))
        return len(df)
    from deltalake import write_deltalake
    write_deltalake(table_path(name), to_arrow(name, df), mode="append")
    return len(df)


def upsert(name: str, df: pd.DataFrame) -> int:
    """Insert new rows, update rows whose primary key already exists."""
    if df.empty:
        return 0
    pk = table_spec(name)["primary_key"]
    predicate = " AND ".join(f"t.{k} = s.{k}" for k in pk)

    if mode() == "databricks":
        if not exists(name):
            _uc_create(name)
        from delta.tables import DeltaTable
        (
            DeltaTable.forName(_spark(), uc_name(name)).alias("t")
            .merge(_spark_df(name, df).alias("s"), predicate)
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute()
        )
        return len(df)

    from deltalake import DeltaTable, write_deltalake
    data = to_arrow(name, df)
    if not exists(name):
        write_deltalake(table_path(name), data, mode="append")
        return len(df)
    (
        DeltaTable(table_path(name))
        .merge(source=data, predicate=predicate, source_alias="s", target_alias="t")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )
    return len(df)
