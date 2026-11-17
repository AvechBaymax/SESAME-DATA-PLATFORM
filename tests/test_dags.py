"""The DAGs import without errors and wire Bronze and Silver as intended (needs Airflow)."""
from pathlib import Path

import pytest

pytest.importorskip("airflow")

DAGS = Path(__file__).resolve().parents[1] / "dags"


@pytest.fixture(scope="module")
def dagbag():
    from airflow.models import DagBag

    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(DAGS))  # the scheduler puts dags/ on sys.path for sesame_tasks
        yield DagBag(dag_folder=str(DAGS), include_examples=False)


def upstream(dag, task_id):
    return set(dag.get_task(task_id).upstream_task_ids)


def test_dags_import_cleanly(dagbag):
    assert dagbag.import_errors == {}
    assert {"sesame_poc_bootstrap", "sesame_ingest_daily"} <= set(dagbag.dags)


def test_bootstrap_builds_silver_after_bronze_and_the_farm_dimensions(dagbag):
    dag = dagbag.dags["sesame_poc_bootstrap"]
    assert upstream(dag, "silver_user_config") == {"bronze_user_config"}
    assert upstream(dag, "silver_field_operation") == {"bronze_field_operation", "silver_user_config"}
    assert upstream(dag, "silver_soilgrids") == {"bronze_soilgrids", "silver_user_config"}
    assert upstream(dag, "silver_nasa_power") == {"bronze_nasa_power"}
    spark_tasks = [t for t in dag.tasks if t.task_id.startswith(("bronze_", "silver_"))]
    assert {t.pool for t in spark_tasks} == {"spark"}


def test_daily_dag_takes_nasa_to_silver(dagbag):
    dag = dagbag.dags["sesame_ingest_daily"]
    assert upstream(dag, "silver_nasa_power") == {"bronze_nasa_power"}
    assert "silver_owm" not in dag.task_ids
