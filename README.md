# Real-ESRGAN Upscale API

A self-hosted image upscaling service built around [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN).

The **API** accepts jobs and stores their metadata; it never runs the model
itself. One or more **GPU workers** pick jobs off a Redis queue, run inference,
and write results back. Job state lives in SQLite; results are served directly
over HTTP.

```
client ──POST /api/upscale──▶ API ──┐
                                     │ create job (SQLite, "queued")
                                     │ enqueue (Redis / arq)
                                     ▼
                                  Redis queue
                                     │
                        ┌────────────┴────────────┐
                        ▼                          ▼
                    worker #1                  worker #2   (GPU)
                  download → Real-ESRGAN → save → update status
```

## Stack

- **Python 3.11**, **FastAPI**, **uvicorn**
- **Redis** + **arq** for the async job queue
- **SQLite** (via `aiosqlite`, WAL mode) for job metadata
- **Docker Compose** with **NVIDIA GPU** workers
- **Real-ESRGAN** / **torch** in the worker only

## API

Base path: `/api`

### `POST /api/upscale`

Request:

```json
{
  "image_url": "https://example.com/image.jpg",
  "scale": 4,
  "model": "realesrgan-x4plus"
}
```

- `scale` — one of `2`, `3`, `4` (default `4`)
- `model` — one of `realesrgan-x4plus`, `realesrnet-x4plus`,
  `realesrgan-x4plus-anime`, `realesr-general-x4v3` (default `realesrgan-x4plus`)

Response (`202 Accepted`):

```json
{ "id": "9f1c...", "status": "queued" }
```

### `GET /api/jobs/{id}`

```json
{
  "id": "9f1c...",
  "status": "succeeded",
  "scale": 4,
  "model": "realesrgan-x4plus",
  "image_url": "https://example.com/image.jpg",
  "result": "/api/jobs/9f1c.../result",
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}
```

`status` is one of `queued`, `processing`, `succeeded`, `failed`.
`result` is populated only when the job has succeeded.

### `GET /api/jobs/{id}/result`

Returns the upscaled image bytes with the correct `Content-Type`.
Returns `409` if the result is not ready and `404`/`410` if it is missing.

## Run with Docker Compose (GPU)

Requires Docker, the NVIDIA Container Toolkit, and an NVIDIA GPU on the host.

```bash
cp .env.example .env        # optional
make build                  # build api + worker images
make up                     # start redis + api + worker
# or run multiple workers:
make up-scale               # docker compose up -d --scale worker=3
```

The API is then available at <http://localhost:8000/api>.

> The first job for a given model downloads its weights to the shared
> `/data/weights` volume, so the first run is slower.

### Try it

```bash
# Submit a job
curl -s -X POST http://localhost:8000/api/upscale \
  -H 'Content-Type: application/json' \
  -d '{"image_url":"https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/inputs/0014.jpg","scale":4,"model":"realesrgan-x4plus"}'
# => {"id":"<job_id>","status":"queued"}

# Poll status
curl -s http://localhost:8000/api/jobs/<job_id>

# Download the result once status == "succeeded"
curl -s http://localhost:8000/api/jobs/<job_id>/result -o out.png
```

## Local development

The API and tests need only the lightweight dependencies (no torch/CUDA).

```bash
python -m venv .venv && source .venv/bin/activate
make dev                    # install + dev/test deps

# Run the API against a local Redis (e.g. `docker run -p 6379:6379 redis`)
make run-api

# Run a worker locally (requires the worker ML deps + a GPU/CPU build of torch)
pip install -r requirements-worker.txt
make run-worker
```

## Tests

The test suite covers the API endpoints. It uses a temporary SQLite database
and a fake queue, so neither Redis nor the ML stack is required.

```bash
make dev
make test
```

## Project layout

```
upscale_api/
  api.py        FastAPI app + endpoints (/api)
  worker.py     arq worker: download → Real-ESRGAN → save → update status
  upscaler.py   Real-ESRGAN inference wrapper (lazy ML imports)
  db.py         SQLite job store (aiosqlite, WAL)
  queue.py      arq / Redis helpers
  schemas.py    Pydantic request/response models + JobStatus
  models.py     Supported Real-ESRGAN model registry
  config.py     Settings (env-driven)
tests/          pytest API tests
docker/         API + GPU worker Dockerfiles
docker-compose.yml
Makefile
```

## Configuration

All settings are environment variables (see `.env.example`). Key ones:

| Variable | Default | Description |
| --- | --- | --- |
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | Redis connection |
| `DATABASE_PATH` | `data/jobs.db` | SQLite job store |
| `OUTPUT_DIR` | `data/outputs` | Where results are written |
| `WEIGHTS_DIR` | `data/weights` | Cached model weights |
| `USE_GPU` | `true` | Use CUDA + fp16 in the worker |
| `TILE_SIZE` | `0` | Tile size for low-VRAM inference (0 = off) |
| `MAX_IMAGE_BYTES` | `52428800` | Reject inputs larger than this |
```
