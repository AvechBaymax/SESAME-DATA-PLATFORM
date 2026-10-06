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