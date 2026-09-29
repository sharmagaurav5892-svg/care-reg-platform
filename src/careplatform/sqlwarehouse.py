"""Table access through a Databricks SQL warehouse: the "sql" lakehouse mode (ADR-013).

Used by the Databricks App, which has no Spark. The app runs as its own service
principal; the SDK's Config() picks up its credentials (DATABRICKS_CLIENT_ID /
DATABRICKS_CLIENT_SECRET, injected by Databricks), and the warehouse id comes from
the app resource "sql-warehouse" (env DATABRICKS_WAREHOUSE_ID). No keys in code.

What this mode may do, on purpose, and nothing more (least functionality):

    read      SELECT the catalog's columns from a table
    append    INSERT new rows (the call logs), with bound parameters, never string-built values
    exists    SHOW TABLES, so a missing table and a missing permission are not confused

It never creates, overwrites or merges tables: those belong to the pipelines. The app's
grants (infra/grants_app.sql) match: SELECT on what it reads, MODIFY on what it logs to.
"""
from __future__ import annotations

import os

import pandas as pd
import pyarrow as pa


def _connect():
    """Open a connection as the current identity (the app's service principal in an app)."""
    from databricks import sql
    from databricks.sdk.core import Config

    cfg = Config()
    warehouse = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not warehouse:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set. In the app it comes from the "
                           "'sql-warehouse' resource (resources/app.yml).")
    host = cfg.host.replace("https://", "").rstrip("/")
    return sql.connect(server_hostname=host, http_path=f"/sql/1.0/warehouses/{warehouse}",
                       credentials_provider=lambda: cfg.authenticate)


def _quote(full_name: str) -> str:
    """care_reg_dev.silver.chunks -> `care_reg_dev`.`silver`.`chunks`"""
    return ".".join(f"`{p}`" for p in full_name.split("."))


def table_exists(full_name: str) -> bool:
    catalog, schema, table = full_name.split(".")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SHOW TABLES IN `{catalog}`.`{schema}` LIKE '{table}'")
        return len(cur.fetchall()) > 0


def read(full_name: str, schema: pa.Schema) -> pd.DataFrame:
    cols = ", ".join(f"`{c}`" for c in schema.names)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM {_quote(full_name)}")
        return cur.fetchall_arrow().to_pandas()


def insert(full_name: str, table: pa.Table) -> int:
    """INSERT the rows of an Arrow table (already conformed to the catalog schema)."""
    rows = table.to_pylist()
    if not rows:
        return 0
    cols = table.schema.names
    params: dict = {}
    values = []
    for i, row in enumerate(rows):
        names = []
        for c in cols:
            key = f"r{i}_{c}"
            params[key] = row[c]
            names.append(f":{key}")
        values.append("(" + ", ".join(names) + ")")
    sql_text = (f"INSERT INTO {_quote(full_name)} ({', '.join(f'`{c}`' for c in cols)}) "
                f"VALUES {', '.join(values)}")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql_text, params)
    return len(rows)
