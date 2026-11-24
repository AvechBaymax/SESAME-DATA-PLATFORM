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


def test_bootstrap_ingests_every_demo_farm(dagbag):
    from common.farm import DEMO_FARMS

    dag = dagbag.dags["sesame_poc_bootstrap"]
    for farm in DEMO_FARMS:
        for step in ("upload_user_config", "ingest_soilgrids", "ingest_nasa_season"):
            assert f"{farm.farm_id}.{step}" in dag.task_ids
        assert f"--lat {farm.lat} --lon {farm.lon}" in dag.get_task(f"{farm.farm_id}.ingest_nasa_season").bash_command
    nasa_inputs = upstream(dag, "bronze_nasa_power")
    assert nasa_inputs == {f"{f.farm_id}.ingest_nasa_season" for f in DEMO_FARMS}
    # one farm's failed API call must not block the others
    assert dag.get_task("bronze_nasa_power").trigger_rule == "all_done"


def test_bootstrap_builds_silver_after_bronze_and_the_farm_dimensions(dagbag):
    dag = dagbag.dags["sesame_poc_bootstrap"]
    assert upstream(dag, "silver_user_config") == {"bronze_user_config"}
    assert upstream(dag, "silver_field_operation") == {"bronze_field_operation", "silver_user_config"}
    assert upstream(dag, "silver_soilgrids") == {"bronze_soilgrids", "silver_user_config"}
    assert upstream(dag, "silver_nasa_power") == {"bronze_nasa_power"}
    spark_tasks = [t for t in dag.tasks if t.task_id.startswith(("bronze_", "silver_"))]
    assert {t.pool for t in spark_tasks} == {"spark"}


def test_gold_runs_after_every_silver_task(dagbag):
    dag = dagbag.dags["sesame_poc_bootstrap"]
    silver_tasks = {t.task_id for t in dag.tasks if t.task_id.startswith("silver_")}
    assert upstream(dag, "gold_build") == silver_tasks
    assert dag.get_task("gold_build").trigger_rule == "all_done"
    daily = dagbag.dags["sesame_ingest_daily"]
    assert upstream(daily, "gold_build") == {"silver_nasa_power"}
    spark_tasks = [t for t in dag.tasks if t.task_id.startswith(("bronze_", "silver_", "gold_"))]
    assert all(t.conf["spark.sql.shuffle.partitions"] == "4" for t in spark_tasks)


def test_serving_export_runs_after_gold(dagbag):
    for dag_id in ("sesame_poc_bootstrap", "sesame_ingest_daily"):
        assert upstream(dagbag.dags[dag_id], "serving_export") == {"gold_build"}


def test_daily_dag_takes_nasa_to_silver(dagbag):
    dag = dagbag.dags["sesame_ingest_daily"]
    assert upstream(dag, "silver_nasa_power") == {"bronze_nasa_power"}
    assert "silver_owm" not in dag.task_ids
    assert len(upstream(dag, "bronze_nasa_power")) == len(upstream(dag, "bronze_owm")) >= 3
