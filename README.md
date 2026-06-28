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
  "model": "realesrgan-x4plus",
  "webhook_url": "https://your-app.example.com/hooks/upscale"
}
```

- `scale` — one of `2`, `3`, `4` (default `4`)
- `model` — one of `realesrgan-x4plus`, `realesrnet-x4plus`,
  `realesrgan-x4plus-anime`, `realesr-general-x4v3` (default `realesrgan-x4plus`)
- `webhook_url` — optional; when set, the finished job is POSTed here
  (see [Webhooks](#webhooks))

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

## Prerequisites (Linux + NVIDIA GPU)

The GPU worker runs on a **Linux host with an NVIDIA GPU** (a recent driver,
Docker Engine, and the NVIDIA Container Toolkit). macOS/Windows have no NVIDIA
GPU passthrough — for those, see [Local development](#local-development) and run
the worker on CPU.

### 1. NVIDIA driver

Make sure a driver is installed and `nvidia-smi` works:

```bash
nvidia-smi
```

If not, install it (Ubuntu/Debian):

```bash
sudo ubuntu-drivers autoinstall   # or: sudo apt install -y nvidia-driver-550
sudo reboot
```

### 2. Docker Engine + Compose plugin

```bash
# Official convenience script (Ubuntu/Debian/most distros)
curl -fsSL https://get.docker.com | sudo sh

# Run docker without sudo (log out/in afterwards)
sudo usermod -aG docker "$USER"

# Verify (Compose v2 ships as the `docker compose` plugin)
docker --version
docker compose version
```

### 3. NVIDIA Container Toolkit

This lets containers use the GPU. On Ubuntu/Debian:

```bash
# Add the NVIDIA repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Install and wire it into Docker
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Other distros (RHEL/Fedora/SUSE) and details:
<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>

### 4. Verify GPU access from a container

```bash
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

If that prints your GPU, the stack below will work.

## Run with Docker Compose (GPU)

Requires the prerequisites above (Docker, NVIDIA Container Toolkit, NVIDIA GPU).

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

## Expose publicly with Cloudflare Tunnel

Two optional `cloudflared` services are wired up behind Compose profiles, so
neither starts unless you ask for it.

### Quick tunnel (no account, throwaway URL)

```bash
make up                       # API must be running first
make tunnel-quick             # starts cloudflared-quick
make tunnel-url               # prints the https://<random>.trycloudflare.com URL
```

The public URL serves the API directly, e.g.
`https://<random>.trycloudflare.com/api/health`.

### Named tunnel (your own domain, persistent)

1. In the Cloudflare **Zero Trust → Networks → Tunnels** dashboard create a
   tunnel and add a public hostname (e.g. `upscale.example.com`) routed to
   `http://api:8000`.
2. Copy the tunnel **token** into `.env`:
   ```env
   CLOUDFLARE_TUNNEL_TOKEN=eyJh...
   ```
3. Start it:
   ```bash
   make tunnel                 # docker compose --profile tunnel up -d cloudflared
   ```

Both services share the Compose network, so they reach the API at `api:8000`.

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
  worker.py     arq worker: download → Real-ESRGAN → save → update status → webhook
  upscaler.py   Real-ESRGAN inference wrapper (lazy ML imports)
  webhooks.py   Signed webhook delivery (Standard Webhooks / WaveSpeed scheme)
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
| `DELETE_INPUT_AFTER` | `true` | Delete the downloaded input once a job finishes |
| `RESULT_TTL_HOURS` | `24` | Auto-remove finished jobs + results after N hours (0 = keep forever) |
| `WEBHOOK_SECRET` | `` | HMAC key for signing webhooks (empty = unsigned) |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | Per-attempt webhook HTTP timeout |
| `WEBHOOK_MAX_RETRIES` | `3` | Webhook delivery attempts before giving up |
| `PUBLIC_BASE_URL` | `` | Base URL for absolute `result` links in webhooks |

## Webhooks

Instead of polling, pass a `webhook_url` per request. When the job finishes
(succeeded **or** failed) the worker POSTs the result to that URL. Delivery is
best-effort with retries and never fails the job.

The payload mirrors `GET /api/jobs/{id}`:

```json
{
  "id": "9f1c...",
  "status": "succeeded",
  "model": "realesrgan-x4plus",
  "scale": 4,
  "image_url": "https://example.com/image.jpg",
  "result": "https://upscale.example.com/api/jobs/9f1c.../result",
  "error": null,
  "created_at": "..."
}
```

`result` is absolute when `PUBLIC_BASE_URL` is set, otherwise a relative path.

### Signature verification

Following the [Standard Webhooks](https://www.standardwebhooks.com/) scheme
(same as WaveSpeed), each delivery carries three headers:

| Header | Meaning |
| --- | --- |
| `webhook-id` | unique id of this delivery |
| `webhook-timestamp` | unix seconds when sent |
| `webhook-signature` | `v1,<base64(HMAC_SHA256)>` |

The signature is `HMAC_SHA256(key, "{webhook-id}.{webhook-timestamp}.{body}")`,
where `key` is `WEBHOOK_SECRET` with any `whsec_` prefix stripped (used as-is,
not base64-decoded). Verify on the receiver:

```python
import base64, hashlib, hmac, time

def verify(secret, headers, body: bytes) -> bool:
    wid, ts, sig = headers["webhook-id"], headers["webhook-timestamp"], headers["webhook-signature"]
    if abs(time.time() - int(ts)) > 300:          # reject stale (>5 min)
        return False
    key = secret.removeprefix("whsec_").encode()
    expected = "v1," + base64.b64encode(
        hmac.new(key, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return any(hmac.compare_digest(s, expected) for s in sig.split())
```

Set the secret via `WEBHOOK_SECRET` (leave empty to send unsigned webhooks).

## Cleanup

The worker keeps storage bounded automatically:

- **Inputs** are deleted as soon as a job finishes (`DELETE_INPUT_AFTER`).
- **Results + job rows** older than `RESULT_TTL_HOURS` are removed by an
  hourly cleanup task. It runs as a built-in **arq cron job inside the worker**
  (no extra container or system cron needed) and is coordinated via Redis, so it
  runs once even when multiple workers are scaled up. Set `RESULT_TTL_HOURS=0`
  to disable and keep everything.
