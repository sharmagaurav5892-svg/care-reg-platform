"""Look at any lakehouse table from the terminal, with SQL.

    python -m careplatform.show bronze.raw_documents
    python -m careplatform.show ops.run_log --limit 5
    python -m careplatform.show --sql "SELECT status, count(*) FROM ops_run_log GROUP BY 1"

Every Delta table is registered in DuckDB as <layer>_<table>
(e.g. bronze_raw_documents), so you can join them in --sql.
"""
from __future__ import annotations

import argparse

import duckdb
import pandas as pd

from careplatform import config, lakehouse

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)
pd.set_option("display.max_colwidth", 40)


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for t in config.catalog()["tables"]:
        if lakehouse.exists(t["name"]):
            con.register(t["name"].replace(".", "_"), lakehouse.read(t["name"]))
    return con


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("table", nargs="?", help="e.g. bronze.raw_documents")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sql", help="any DuckDB SQL; tables are named layer_table")
    a = p.parse_args()
    con = connect()
    if a.sql:
        print(con.execute(a.sql).df().to_string(index=False))
    elif a.table:
        name = a.table.replace(".", "_")
        print(con.execute(f"SELECT * FROM {name} LIMIT {a.limit}").df().to_string(index=False))
    else:
        print("Tables with data:")
        for t in config.catalog()["tables"]:
            if lakehouse.exists(t["name"]):
                print(f"  {t['name']:28} {len(lakehouse.read(t['name'])):>6} rows")


if __name__ == "__main__":
    main()
