# SESAME-DATA-PLATFORM

## Start the local services

1. Copy `.env.example` to `.env` and fill in `MINIO_ACCESS_KEY` and
   `MINIO_SECRET_KEY`: the credentials of the S3-compatible object storage
   (RustFS; the variable names are kept from the earlier MinIO setup). Use a
   secret of at least 8 characters. On Linux/WSL set `AIRFLOW_UID` to the
   number `id -u` prints, so Airflow can write its logs.
2. From the repository root, create the mounted folders once and start the
   stack. `--env-file .env` matters: Compose otherwise looks for `.env` next to
   the Compose file.

```bash
mkdir -p logs plugins
docker compose --env-file .env -f infrastructure/docker/docker-compose.yml up -d --build
```

The stack: RustFS (object storage), Postgres (Airflow metadata and the Iceberg
catalog), Kafka, a Spark master and worker, and Airflow.

One Dockerfile (`infrastructure/docker/dockerfile`) builds two images that
share the same extra Spark jars in `/opt/spark/extra-jars`:

- `sesame-spark` (target `spark`): Spark master and worker.
- `sesame-airflow` (target `airflow`): Airflow plus the PySpark driver.

The jars are Apache Iceberg 1.12.0 (Spark 3.5 runtime), the PostgreSQL JDBC
driver, `hadoop-aws` 3.3.4 with the AWS SDK bundle (S3A) and the Spark Kafka
source for Structured Streaming, all matching Spark 3.5.4.

`infrastructure/spark/spark-defaults.conf` makes `lakehouse` the default
Spark catalog: an Iceberg **JDBC catalog** stored in the Postgres database
`iceberg_catalog`, with table files under `s3a://lakehouse/warehouse`. S3A
points at `http://rustfs:9000` and reads the credentials from
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, which Compose fills from the
keys above. A table is addressed as `lakehouse.<layer>.<table>`, for example
`lakehouse.silver.fact_weather_daily`.

On start-up:

- Postgres creates the `iceberg_catalog` database on a fresh volume, and
  `airflow-init` creates it on an existing one (`python -m common.init_catalog`).
- `airflow-init` creates the buckets `landing-zone` (raw files) and
  `lakehouse` (Iceberg tables) (`python -m common.init_buckets`).
- `kafka-init` creates the topics `sesame.sensor.raw` and
  `sesame.sensor.raw.dlq`.

Check that Spark can create and update Iceberg tables on RustFS:

```bash
docker exec spark-master /opt/spark/bin/spark-submit \
    --master spark://spark-master:7077 /opt/spark-jobs/smoke_iceberg.py
# -> Iceberg OK: 3 rows, 2 day partitions, 2 snapshots at s3a://lakehouse/warehouse/smoke/sensor_check
```

| Service | URL |
| --- | --- |
| Airflow | http://localhost:8080 (admin / admin) |
| Spark master | http://localhost:8081 |
| RustFS console | http://localhost:9001 |
| Kafka (from the host) | localhost:9092 |

The Postgres user and password (`airflow` / `airflow`) and the Airflow admin
login are local development defaults; production uses separate secrets.

## Mock data for the PoC

Until the growing season starts, sensor and farm data are simulated. Install
the project once from the repository root (copy `.env.example` to `.env`
first), then run the modules with `python -m`:

```bash
python3 -m pip install -r requirements.txt -e ".[dev]"

# Farm/plot/season/device config, planned field operations and reference data -> object storage (landing-zone)
python3 -m mock_iot.user_config_mock
python3 -m mock_iot.mock_fao_db

# Live sensor stream -> Kafka topic sesame.sensor.raw
python3 -m mock_iot.producer --mode live --interval 5

# A whole simulated season, written to a file instead of Kafka
python3 -m mock_iot.producer --mode backfill --planting-date 2025-11-15 \
    --start 2025-11-10 --days 85 --sink jsonl --output data/sensor_season.jsonl
```

The simulator runs a soil water balance (FAO-56 ET0 x Kc by days after
sowing, rain, sprinkler irrigation, drainage), so every reading is consistent
with the others and with the weather. Useful options:

- `--weather nasa` drives a backfill with NASA POWER daily data for the same
  point (falls back to a Binh Thuan climatology where NASA has no data).
- `--drainage poor` makes the plot pond after heavy rain (waterlogging
  scenario: VWC up, soil O2 down).
- `--fault-rate 0.02` injects malformed, out-of-range, missing-field,
  duplicate and late messages for DLQ and data-quality tests. The fault type
  is sent as the Kafka header `x-mock-fault`.
- `--devices N`, `--farm-id`, `--lat/--lon`, `--soil-texture` describe the
  plot; use the same values for `user_config_mock` so the data joins.

API ingesters (need `.env`; `OWM_API_KEY` for OpenWeatherMap) land raw JSON in
object storage, defaulting to the same farm and location as the mocks:

```bash
python3 -m mock_iot.nasa_ingest
python3 -m mock_iot.owm_ingest
python3 -m mock_iot.isric_ingest
```

IDs nest farm -> plot -> season/device: `BINHTHUAN_01` -> `BINHTHUAN_01_P01`
-> `BINHTHUAN_01_P01_2026DX` (DX = Dong Xuan, XH = Xuan He, KH = other) and
`SN_BINHTHUAN_01_P01_01`. The config's `farm`, `plot`, `season` and `devices`
sections map to the Silver dimensions; the plan file lands under
`field_operation/farm_id=.../season_id=.../` with one `planned` row per sowing,
fertilizer, weed-control and harvest operation.

Shared code lives in `src/common/`: `config` (`.env`, object storage/Kafka settings,
no default credentials), `minio_utils` (S3 client for RustFS, `build_key`,
`upload_raw_json`), `http_utils` (`get_json` with retry), plus sesame
parameters from FAO-56 and the Binh Thuan guide (`crop`), soil hydraulics
(`soil`), FAO-56 Penman-Monteith (`et0`) and the farm profile (`farm`).
Object storage credentials are required: copy `.env.example` to `.env` and fill them
in. Run the tests with `python3 -m pytest`.

## Sesame farm monitoring dashboard

The `web/` dashboard reads farm weather and satellite data from a Python API
backed by Google Earth Engine. It uses the configured Cu Chi point at
11.00° N, 106.40° E. ERA5-Land supplies daily temperature, precipitation, and
dew-point estimates; Sentinel-2 supplies NDVI; SoilGrids supplies soil texture
and pH; SRTM supplies elevation. These are satellite/reanalysis estimates,
not live on-field sensor measurements. The field outline is illustrative.

**Local run:** install the root `requirements.txt`, authenticate Earth Engine
once in your user account, then start the dashboard:

```bash
python3 -m pip install -r requirements.txt
earthengine authenticate
cd web
python3 local_server.py
```

The API uses the project ID from the supplied script by default. Set
`GEE_PROJECT_ID` in `web/.env` to override it. The selected Google Cloud
project must have the Earth Engine API enabled and be registered for Earth
Engine.

**Vercel deployment:** import this repository and keep **Root Directory** at
the repository root. `vercel.json` serves the dashboard from `web/` and the
Earth Engine API from `api/`. Add `GEE_PROJECT_ID` and
`GEE_SERVICE_ACCOUNT_JSON` as server-side environment variables in Vercel.
The latter must contain a service-account
JSON key whose account has access to the registered Earth Engine project.
Keep the key private and never commit it. Redeploy after setting the
environment variables. The credential stays in the Python API and is never
sent to the browser. The API caches successful responses for 30 minutes.