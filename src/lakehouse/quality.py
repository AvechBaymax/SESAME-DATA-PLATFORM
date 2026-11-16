"""Data-quality checks run on a Silver DataFrame before it is written.

Three kinds, all counted in Spark:
  - primary key: not NULL and unique
  - row rules (silver.Check): a SQL condition every row must meet (NULL passes)
  - foreign keys: every non-NULL value exists in the referenced Silver table
Errors stop the job before anything is written; warnings are only reported.
"""
from dataclasses import dataclass

from pyspark.sql import functions as F


@dataclass(frozen=True)
class Result:
    table: str
    check: str
    severity: str
    failed: int

    @property
    def ok(self):
        return self.failed == 0

    def __str__(self):
        status = "OK" if self.ok else f"{'FAIL' if self.severity == 'error' else 'WARN'} ({self.failed} row(s))"
        return f"[{self.table}] {self.check}: {status}"


class QualityError(RuntimeError):
    pass


def check_table(spark, table, df, resolve_table):
    """All checks of one Silver table on df. resolve_table(name) -> full Iceberg name."""
    results = []
    key_null = " OR ".join(f"{k} IS NULL" for k in table.keys)
    results.append(Result(table.name, f"key {', '.join(table.keys)} not null", "error",
                          df.where(key_null).count()))
    duplicates = df.groupBy(*table.keys).count().where("count > 1").count()
    results.append(Result(table.name, f"key {', '.join(table.keys)} unique", "error", duplicates))

    for check in table.checks:
        failed = df.where(f"NOT coalesce(({check.condition}), true)").count()
        results.append(Result(table.name, check.name, check.severity, failed))

    for column, ref_table, ref_column in table.references:
        ref_name = resolve_table(ref_table)
        if not spark.catalog.tableExists(ref_name):
            missing = df.where(F.col(column).isNotNull()).count()
        else:
            ref = spark.table(ref_name).select(F.col(ref_column).alias("_ref")).distinct()
            missing = (df.where(F.col(column).isNotNull())
                         .join(ref, F.col(column) == F.col("_ref"), "left_anti").count())
        results.append(Result(table.name, f"{column} exists in {ref_table}", "error", missing))
    return results


def enforce(results):
    """Print every result; raise QualityError when an error-level check failed."""
    for result in results:
        print(result)
    failed = [r for r in results if not r.ok and r.severity == "error"]
    if failed:
        raise QualityError("data-quality checks failed: " + "; ".join(str(r) for r in failed))
