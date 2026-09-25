"""Read and write Delta tables, with the schema taken from the catalog.

Why the schema comes from config/catalog.yaml:
    The catalog is not just documentation. When a pipeline writes a table,
    the column names and types are built from the catalog entry. If the code
    tries to write a column the catalog doesn't know about, the write fails.
    So the catalog and the physical table can never drift apart.

Local mode writes to ./data/lakehouse/<layer>/<table>.
Fabric mode writes to the OneLake path in settings.yaml (see ADR-001).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

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


def _root() -> str:
    s = config.settings()["lakehouse"]
    if s["mode"] == "fabric":
        if not s.get("fabric_root"):
            raise RuntimeError("lakehouse.mode is fabric but fabric_root is not set")
        return s["fabric_root"].rstrip("/")
    root = Path(s["local_root"])
    if not root.is_absolute():
        root = config.REPO_ROOT / root
    return str(root)


def table_path(name: str) -> str:
    """'bronze.raw_documents' -> '<root>/bronze/raw_documents'"""
    layer, table = name.split(".", 1)
    return f"{_root()}/{layer}/{table}"


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


def to_arrow(name: str, df: pd.DataFrame) -> pa.Table:
    """Conform a DataFrame to the catalog schema. Unknown or missing columns fail loudly."""
    schema = schema_for(name)
    extra = set(df.columns) - set(schema.names)
    missing = set(schema.names) - set(df.columns)
    if extra:
        raise ValueError(f"{name}: columns not in catalog: {sorted(extra)}")
    if missing:
        raise ValueError(f"{name}: columns missing from data: {sorted(missing)}")
    df = df[schema.names]
    for f in schema:
        if f.type == pa.timestamp("us", tz="UTC"):
            df[f.name] = pd.to_datetime(df[f.name], utc=True)
    return pa.Table.from_pandas(df, schema=schema, preserve_index=False)


def exists(name: str) -> bool:
    try:
        DeltaTable(table_path(name))
        return True
    except Exception:
        return False


def read(name: str) -> pd.DataFrame:
    """Whole table as pandas. Returns an empty frame with the right columns if it doesn't exist yet."""
    if not exists(name):
        return schema_for(name).empty_table().to_pandas()
    return DeltaTable(table_path(name)).to_pandas()


def append(name: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    write_deltalake(table_path(name), to_arrow(name, df), mode="append")
    return len(df)


def upsert(name: str, df: pd.DataFrame) -> int:
    """Insert new rows, update rows whose primary key already exists."""
    if df.empty:
        return 0
    data = to_arrow(name, df)
    if not exists(name):
        write_deltalake(table_path(name), data, mode="append")
        return len(df)
    pk = table_spec(name)["primary_key"]
    predicate = " AND ".join(f"t.{k} = s.{k}" for k in pk)
    (
        DeltaTable(table_path(name))
        .merge(source=data, predicate=predicate, source_alias="s", target_alias="t")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )
    return len(df)
