"""Task builders shared by the Sesame DAGs.

Python scripts run in the Airflow container (src/ is on PYTHONPATH); Spark jobs
are submitted to the cluster with the driver in the scheduler container.
"""
from airflow.operators.bash import BashOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

SPARK_JOBS = "/opt/spark-jobs"
# Created by airflow-init with 1 slot: Spark jobs run one at a time. Concurrent
# jobs fight over the worker's cores and race on the Iceberg JDBC catalog
# (creating its tables or the same namespace at once).
SPARK_POOL = "spark"


def python_module(task_id, module, args=""):
    """Run `python -m <module> <args>` from src/ (ingesters and mocks)."""
    return BashOperator(task_id=task_id, bash_command=f"python -m {module} {args}".strip())


def spark_job(task_id, job, source):
    return SparkSubmitOperator(
        task_id=task_id,
        conn_id="spark_default",
        application=f"{SPARK_JOBS}/{job}",
        application_args=["--source", source],
        name=task_id.replace("_", "-"),
        pool=SPARK_POOL,
        retries=1,
    )


def bronze(source):
    """Load new landing files of one source into lakehouse.bronze.<table>."""
    return spark_job(f"bronze_{source}", "landing_to_bronze.py", source)


def silver(source):
    """Build the Silver tables fed by one Bronze source (see lakehouse.silver.TABLES)."""
    return spark_job(f"silver_{source}", "bronze_to_silver.py", source)
