# SESAME-DATA-PLATFORM

## Start the local services

Run Docker Compose with the repository's Compose file:

```bash
docker compose -f infrastructure/docker/docker-compose.yml up -d
```

To run the command from the Compose directory instead:

```bash
cd infrastructure/docker
docker compose up -d
```

The Airflow image is built from `infrastructure/docker/dockerfile`.

## Mock data for the PoC

Until the growing season starts, sensor and farm data are simulated. Install
the project once from the repository root (copy `.env.example` to `.env`
first), then run the modules with `python -m`:

```bash
python3 -m pip install -r requirements.txt -e ".[dev]"

# Farm/plot/season/device config, planned field operations and reference data -> MinIO
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
MinIO, defaulting to the same farm and location as the mocks:

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

Shared code lives in `src/common/`: `config` (`.env`, MinIO/Kafka settings,
no default credentials), `minio_utils` (S3 client, `build_key`,
`upload_raw_json`), `http_utils` (`get_json` with retry), plus sesame
parameters from FAO-56 and the Binh Thuan guide (`crop`), soil hydraulics
(`soil`), FAO-56 Penman-Monteith (`et0`) and the farm profile (`farm`).
MinIO credentials are required: copy `.env.example` to `.env` and fill them
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