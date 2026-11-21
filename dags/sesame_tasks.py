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
# PoC data is small: 4 shuffle partitions instead of Spark's 200 keep each job
# from scheduling hundreds of near-empty tasks on the worker's spare core.
SPARK_CONF = {"spark.sql.shuffle.partitions": "4"}


def python_module(task_id, module, args=""):
    """Run `python -m <module> <args>` from src/ (ingesters and mocks)."""
    return BashOperator(task_id=task_id, bash_command=f"python -m {module} {args}".strip())


def spark_job(task_id, job, args=(), trigger_rule="all_success"):
    return SparkSubmitOperator(
        task_id=task_id,
        trigger_rule=trigger_rule,
        conn_id="spark_default",
        application=f"{SPARK_JOBS}/{job}",
        application_args=list(args),
        conf=SPARK_CONF,
        name=task_id.replace("_", "-"),
        pool=SPARK_POOL,
        retries=1,
    )


def bronze(source, trigger_rule="all_success"):
    """Load new landing files of one source into lakehouse.bronze.<table>.

    Fed by one ingest task per farm, pass trigger_rule="all_done": Bronze then
    loads the farms that worked even when one farm's API call failed (that task
    stays red, so the DAG run is still marked failed).
    """
    return spark_job(f"bronze_{source}", "landing_to_bronze.py", ["--source", source], trigger_rule)


def silver(source):
    """Build the Silver tables fed by one Bronze source (see lakehouse.silver.TABLES)."""
    return spark_job(f"silver_{source}", "bronze_to_silver.py", ["--source", source])


def gold():
    """Recompute the Gold tables (water balance, season summary, regional view) from Silver.

    Runs even when an upstream task failed (all_done): Gold then reflects the
    Silver tables that did update, and the failed task stays red.
    """
    return spark_job("gold_build", "silver_to_gold.py", trigger_rule="all_done")
