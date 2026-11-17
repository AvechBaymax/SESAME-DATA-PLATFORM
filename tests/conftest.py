"""Shared fixtures. One local Spark session serves every Spark test module: a
JVM loads extra jars (Iceberg) only once, so modules cannot each build their own."""
import os
import shutil
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")
ICEBERG_JAR = os.getenv("ICEBERG_SPARK_JAR", "")


@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    pytest.importorskip("pyspark")
    if shutil.which("java") is None:
        pytest.skip("Java is required for Spark")
    from pyspark.sql import SparkSession

    os.environ["PYSPARK_PYTHON"] = sys.executable  # executors import lakehouse from src/
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [SRC, os.getenv("PYTHONPATH")]))
    builder = (SparkSession.builder.master("local[2]").appName("sesame-tests")
               .config("spark.sql.session.timeZone", "UTC")
               .config("spark.sql.shuffle.partitions", "2")
               .config("spark.ui.enabled", "false"))
    if ICEBERG_JAR:
        builder = (builder.config("spark.jars", ICEBERG_JAR)
                   .config("spark.sql.extensions",
                           "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
                   .config("spark.sql.catalog.test", "org.apache.iceberg.spark.SparkCatalog")
                   .config("spark.sql.catalog.test.type", "hadoop")
                   .config("spark.sql.catalog.test.warehouse", str(tmp_path_factory.mktemp("warehouse"))))
    session = builder.getOrCreate()
    yield session
    session.stop()
