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
catalog), Kafka, a Spark master and worker, the sensor stream
(`spark-streaming`) and Airflow.

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
| Sensor stream (Spark UI, Structured Streaming tab) | http://localhost:4040 |

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
in. Run the tests with `python3 -m pytest`. The Spark tests need Java (they
are skipped without it); the Iceberg MERGE test also needs
`ICEBERG_SPARK_JAR` set to an `iceberg-spark-runtime-3.5_2.12` jar, and the DAG
tests run only where Airflow is installed.

## Run the pipeline (PoC)

Spark jobs live in `src/jobs/`, shared Spark code in `src/lakehouse/`, and the
Airflow DAGs in `dags/`.

The PoC monitors four demo farms spread over Binh Thuan, listed once in
`DEMO_FARMS` (`src/common/farm.py`). Each sits in a different NASA POWER
weather cell (about 0.5° x 0.625°, so farms closer together would get the same
NASA weather) and differs in soil, drainage or sowing date. All are sown in
the Dong Xuan 2025-26 season:

| Farm | Area | Soil, drainage | Sown | Sensors | Scenario |
| --- | --- | --- | --- | --- | --- |
| `BINHTHUAN_01` (11.08, 108.12) | Ham Thuan Bac | loamy sand, good | 15/11/2025 | 2 | reference plot |
| `BINHTHUAN_02` (11.28, 108.50) | Bac Binh | loamy sand, **poor** | 25/11/2025 | 2 | ponds after heavy rain |
| `BINHTHUAN_03` (11.10, 107.68) | Tanh Linh | sandy loam, good | 20/12/2025 | 2 | sown late in the window |
| `BINHTHUAN_04` (10.72, 107.72) | Ham Tan | sand, good | 05/12/2025 | 1 | dries out fast |

The points are illustrative. Add a farm to `DEMO_FARMS` and both DAGs pick it up.

| DAG | Schedule | What it does |
| --- | --- | --- |
| `sesame_poc_bootstrap` | manual | Per farm (one task group each): uploads the farm/season config and field plan, fetches SoilGrids and the NASA POWER weather of the season (01/11/2025 to 15/03/2026). Uploads the reference data once, loads everything into Bronze, builds Silver, then Gold |
| `sesame_ingest_daily` | 23:00 UTC (06:00 VN) | Per farm: NASA POWER (window D-14 to D-7) into Bronze and Silver; OpenWeatherMap (skipped without `OWM_API_KEY`) into Bronze; then Gold again, with the sensor readings streamed in since |

The Spark tasks are one per source, not per farm: each loads every farm. A
Bronze task waits for all farms' ingest tasks and then loads whatever landed,
so one farm's failed API call does not hold up the others (that task stays
red until it is cleared and re-run).

Trigger the bootstrap from the Airflow UI, or run the same steps by hand
(shown for one farm; repeat with each farm's values from the table above):

```bash
python -m mock_iot.user_config_mock --farm-id BINHTHUAN_02 --lat 11.28 --lon 108.50 \
    --area 8000 --soil-texture loamy_sand --drainage poor --planting-date 2025-11-25 --devices 2
python -m mock_iot.mock_fao_db
python -m mock_iot.isric_ingest --farm-id BINHTHUAN_02 --lat 11.28 --lon 108.50
python -m mock_iot.nasa_ingest --farm-id BINHTHUAN_02 --lat 11.28 --lon 108.50 \
    --start 20251101 --end 20260315
docker exec spark-master /opt/spark/bin/spark-submit \
    --master spark://spark-master:7077 /opt/spark-jobs/landing_to_bronze.py --source all
docker exec spark-master /opt/spark/bin/spark-submit \
    --master spark://spark-master:7077 /opt/spark-jobs/bronze_to_silver.py --source all
```

`landing_to_bronze.py` loads only files it has not loaded before, so it can be
re-run safely. `bronze_to_silver.py` parses the Bronze payloads into the Silver
tables of `docs/data-model.md` (sections 7 and 8), keeps the newest landing
file's row per key, checks the data (section 10; a failed check stops the job
before writing) and MERGEs into `lakehouse.silver.<table>`, so it can be
re-run too. Look at the result:

```bash
docker exec spark-master /opt/spark/bin/spark-sql --master spark://spark-master:7077 -e "
  SHOW TABLES IN lakehouse.silver;
  SELECT farm_id, min(date), max(date), count(*) FROM lakehouse.silver.fact_weather_daily GROUP BY 1;" 2>/dev/null
```

When NASA POWER or SoilGrids cannot be reached, write payloads with the same
structure (marked `"mock": true`) for every demo farm instead:

```bash
python -m mock_iot.mock_api_responses nasa --all-farms --start 20251101 --end 20260315
python -m mock_iot.mock_api_responses soilgrids --all-farms
```

## Sensor stream (PoC)

The `spark-streaming` service runs `src/jobs/sensor_stream.py` from the moment
the stack starts: Kafka `sesame.sensor.raw` -> `lakehouse.bronze.sensor_raw`
-> `lakehouse.silver.fact_sensor_reading`, with rejected messages in
`lakehouse.silver.quarantine_sensor` and unreadable ones also on the topic
`sesame.sensor.raw.dlq` (see `docs/data-model.md`, sections 6, 8 and 10). It
takes one of the Spark worker's two cores; the Airflow jobs use the other.
Build the Silver dimensions first (the bootstrap DAG), so readings can be
checked against `dim_device`.

Feed it with the simulated sensors of every demo farm, either a whole season
at once or live readings:

```bash
# A whole Dong Xuan season (10/11/2025 to 10/03/2026, about 81,000 messages),
# 2 % of them with injected faults; --weather nasa drives it with NASA POWER
docker exec airflow-scheduler python -m mock_iot.producer --all-farms --mode backfill \
    --weather nasa --fault-rate 0.02
# The same season with field incidents, so the alerts have something to catch:
# BINHTHUAN_01 pump broken at flowering, a local storm on BINHTHUAN_02 (poor
# drainage), BINHTHUAN_04's sensor silent for 2 days (src/mock_iot/scenarios.py)
docker exec airflow-scheduler python -m mock_iot.producer --all-farms --mode backfill \
    --weather nasa --fault-rate 0.02 --scenario demo
# Live: one reading per device every 5 seconds, stamped now (Ctrl+C stops it)
docker exec -it airflow-scheduler python -m mock_iot.producer --all-farms --mode live --interval 5
```

(From the host venv the same commands work with `python -m ...`, using
`localhost:9092`.) The stream logs one line per micro-batch:

```bash
docker logs -f spark-streaming 2>&1 | grep sensor_silver
# [sensor_silver] batch 2: 49999 message(s), 49010 reading(s), 595 quarantined {...}, 407 to DLQ
docker exec spark-master /opt/spark/bin/spark-sql --master spark://spark-master:7077 -e "
  SELECT farm_id, count(*), min(ts), max(ts) FROM lakehouse.silver.fact_sensor_reading GROUP BY 1;
  SELECT mock_fault, dq_rule, count(*) FROM lakehouse.silver.quarantine_sensor GROUP BY 1, 2;" 2>/dev/null
```

Restarting the service resumes from its checkpoint (volume
`spark_checkpoints`); a replayed message is not written twice. To start the
stream over, for example after its settings or the message schema changed,
reset it (deletes the sensor tables, checkpoints and the messages in the
sensor topics; asks first), then send the readings again:

```bash
bash infrastructure/scripts/reset_sensor_stream.sh
```

## Gold: water balance per season (PoC)

`src/jobs/silver_to_gold.py` (task `gold_build`, last in both DAGs) turns
Silver into the monitoring tables (`docs/data-model.md`, section 9):

- `lakehouse.gold.feat_season_daily`: per season and day, the FAO-56 water
  balance: ET0 from NASA POWER, Kc and growth stage by days after sowing, the
  growing root zone's available water, rain (plot gauges, else NASA),
  irrigation from the sensors, depletion and the stress coefficient Ks, plus
  soil moisture, soil O₂, leaf wetness, degree days and daily stress flags.
- `lakehouse.gold.agg_season_summary`: per season: rain, irrigation, crop
  water need (ETc) and use (ETa), stress, waterlogging and heat days, rain in
  the harvest window.
- `lakehouse.gold.agg_region_daily`: per province and day: how many farms are
  active, water-stressed, waterlogged or heat-stressed, and their averages.
- `lakehouse.gold.alert_event`: alerts, one per rule broken on consecutive
  days: drought at flowering, water stress, waterlogging, heavy rain at
  flowering or after harvest, heat, missing sensor data, low battery, and
  fertiliser / weeding / harvest reminders, each with a Vietnamese message.

Each run recomputes the tables from Silver. Run it by hand after new sensor
data, or let the daily DAG do it:

```bash
docker exec spark-master /opt/spark/bin/spark-submit --master spark://spark-master:7077 \
    --conf spark.sql.shuffle.partitions=4 /opt/spark-jobs/silver_to_gold.py
docker exec spark-master /opt/spark/bin/spark-sql --master spark://spark-master:7077 -e "
  SELECT season_id, status, round(precip_total_mm) rain, round(irrigation_total_mm) irrigation,
         round(etc_total_mm) etc, round(eta_etc_ratio, 2) eta_etc, water_stress_days, waterlog_days
  FROM lakehouse.gold.agg_season_summary ORDER BY 1;
  SELECT farm_id, rule, severity, start_date, days, message
  FROM lakehouse.gold.alert_event WHERE rule <> 'operation_due' ORDER BY farm_id, start_date;" 2>/dev/null
```

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
