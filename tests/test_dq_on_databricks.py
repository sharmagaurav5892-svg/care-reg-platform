"""DQ checks must run on Databricks, where laptop-only libraries (duckdb, deltalake) don't exist.

CI can't start Spark, so these tests fake it: they pretend to be in databricks mode,
block the laptop-only imports, and check the engine takes the Spark path.
"""
import sys

import pandas as pd

from careplatform import lakehouse
from careplatform.dq import engine


class FakeSpark:
    """Just enough of SparkSession for engine._ratio: sql(...).collect()[0]['v']."""
    def __init__(self, value):
        self.value, self.queries = value, []

    def sql(self, q):
        self.queries.append(q)
        return self

    def collect(self):
        return [{"v": self.value}]


class FakeSparkDF:
    def createOrReplaceTempView(self, name):
        self.view = name


def test_ratio_check_uses_spark_not_duckdb_on_databricks(monkeypatch):
    spark = FakeSpark(0.99)
    monkeypatch.setattr(lakehouse, "mode", lambda: "databricks")
    monkeypatch.setattr(lakehouse, "_spark", lambda: spark)
    monkeypatch.setattr(lakehouse, "_spark_df", lambda name, df: FakeSparkDF())
    monkeypatch.setitem(sys.modules, "duckdb", None)          # 'import duckdb' now fails, like on Databricks

    rule = {"table": "silver.chunks", "expression": "token_estimate BETWEEN 20 AND 1200", "threshold": 0.98}
    out = engine._ratio_min(pd.DataFrame({"token_estimate": [100]}), rule)

    assert out.passed and out.observed_value == 0.99
    assert "token_estimate BETWEEN 20 AND 1200" in spark.queries[0]
