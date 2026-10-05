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