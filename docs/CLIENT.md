# Client integration guide

How to talk to the Real-ESRGAN Upscale API from your own app.

- **Base URL:** `https://upscale.fotokalendare.cz` (prod) — all paths below are
  under `/api`.
- **Content type:** JSON requests/responses, except the result image which is
  raw bytes.
- **Auth:** optional bearer token. If the server has `API_TOKEN` set, send
  `Authorization: Bearer <token>` on every `/api` call except `/api/health`;
  otherwise the API is open. A missing/invalid token returns `401`.

## How it works (lifecycle)

```
POST /api/upscale  ──▶  job created, status "queued"  ──▶  returns { id }
                                     │
                          worker picks it up
                                     ▼
                              status "processing"
                                     ▼
                    ┌──────────────────────────────┐
                    ▼                                ▼
              "succeeded"                        "failed"
        result image available              error message set
```

A job moves through these `status` values:

| status | meaning |
| --- | --- |
| `queued` | accepted, waiting for a worker |
| `processing` | worker is downloading + upscaling |
| `succeeded` | done, result image is ready |
| `failed` | something went wrong, see `error` |

**Getting the result:** poll `GET /api/jobs/{id}` until `status` is
`succeeded`/`failed`, then download the image from the `result` URL.

---

## Endpoints

### `POST /api/upscale` — submit a job

Request body:

| field | type | required | default | notes |
| --- | --- | --- | --- | --- |
| `image_url` | string (URL) | yes | — | publicly reachable source image |
| `scale` | int | no | `4` | one of `2`, `3`, `4` |
| `model` | string | no | `realesrgan-x4plus` | see model list below |

```json
{
  "image_url": "https://example.com/photo.jpg",
  "scale": 4,
  "model": "realesrgan-x4plus"
}
```

Responses:

- `202 Accepted`
  ```json
  { "id": "9f1c2a...", "status": "queued" }
  ```
- `422 Unprocessable Entity` — validation failed (bad/missing `image_url`,
  unsupported `scale` or `model`).

### `GET /api/jobs/{id}` — job status

- `200 OK`
  ```json
  {
    "id": "9f1c2a...",
    "status": "queued",
    "scale": 4,
    "model": "realesrgan-x4plus",
    "image_url": "https://example.com/photo.jpg",
    "result": null,
    "error": null,
    "queue_position": 3,
    "created_at": "2026-06-28T10:00:00+00:00",
    "updated_at": "2026-06-28T10:00:00+00:00"
  }
  ```
  - `result` is `null` until `status == "succeeded"`, then it's the URL of the
    result image (absolute when the server has `PUBLIC_BASE_URL` set).
  - `error` is `null` unless `status == "failed"`.
  - `queue_position` is the number of jobs ahead in the queue, present only
    while `status == "queued"` (`0` = next to run); `null` otherwise. Use it to
    show progress / estimated wait while polling.
- `404 Not Found` — unknown id.

### `GET /api/jobs/{id}/result` — download the image

- `200 OK` — raw image bytes with the correct `Content-Type`
  (e.g. `image/png`). Open it in a browser or stream to a file.
- `404 Not Found` — unknown id.
- `409 Conflict` — job not finished yet (still `queued`/`processing`).
- `410 Gone` — job succeeded but the file was already cleaned up
  (see retention / `RESULT_TTL_HOURS`).

### `GET /api/health` — liveness

- `200 OK` → `{ "status": "ok" }`

---

## Supported models & scales

- `scale`: `2`, `3`, `4`
- `model`:
  - `realesrgan-x4plus` — general photos (default)
  - `realesrnet-x4plus` — general, less aggressive
  - `realesrgan-x4plus-anime` — anime / illustrations
  - `realesr-general-x4v3` — general, lightweight

---

## Example clients

> If the server requires auth, add `Authorization: Bearer <token>` to every
> request (shown below; drop it if the API is open).

### curl (polling)

```bash
BASE=https://upscale.fotokalendare.cz
TOKEN=your-api-token   # omit the -H lines below if the API is open

# 1) submit
ID=$(curl -s -X POST "$BASE/api/upscale" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"image_url":"https://example.com/photo.jpg","scale":4,"model":"realesrgan-x4plus"}' \
  | sed -E 's/.*"id":"([^"]+)".*/\1/')

# 2) poll until done
while :; do
  S=$(curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/jobs/$ID" \
    | sed -E 's/.*"status":"([^"]+)".*/\1/')
  [ "$S" = succeeded ] && break
  [ "$S" = failed ] && { echo "failed"; exit 1; }
  sleep 2
done

# 3) download
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/jobs/$ID/result" -o out.png
```

### Python (polling)

```python
import time, httpx

BASE = "https://upscale.fotokalendare.cz"
TOKEN = "your-api-token"  # set to None if the API is open

def upscale(image_url: str, scale: int = 4, model: str = "realesrgan-x4plus") -> bytes:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    with httpx.Client(base_url=BASE, timeout=30, headers=headers) as c:
        job = c.post("/api/upscale", json={
            "image_url": image_url, "scale": scale, "model": model,
        }).raise_for_status().json()
        jid = job["id"]

        while True:
            j = c.get(f"/api/jobs/{jid}").raise_for_status().json()
            if j["status"] == "succeeded":
                return c.get(f"/api/jobs/{jid}/result").raise_for_status().content
            if j["status"] == "failed":
                raise RuntimeError(j["error"])
            if j["status"] == "queued":
                print(f"waiting, {j['queue_position']} job(s) ahead")
            time.sleep(2)

open("out.png", "wb").write(upscale("https://example.com/photo.jpg"))
```

### JavaScript / TypeScript (polling)

```ts
const BASE = "https://upscale.fotokalendare.cz";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const TOKEN = "your-api-token"; // set to "" if the API is open
const headers: Record<string, string> = TOKEN
  ? { Authorization: `Bearer ${TOKEN}` }
  : {};

async function upscale(imageUrl: string, scale = 4, model = "realesrgan-x4plus") {
  const submit = await fetch(`${BASE}/api/upscale`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ image_url: imageUrl, scale, model }),
  });
  const { id } = await submit.json();

  while (true) {
    const job = await (await fetch(`${BASE}/api/jobs/${id}`, { headers })).json();
    if (job.status === "succeeded") return job.result; // URL of the image
    if (job.status === "failed") throw new Error(job.error);
    await sleep(2000);
  }
}
```

---

## Notes & limits

- **Input must be publicly downloadable** by the worker; there's a size cap
  (`MAX_IMAGE_BYTES`, default 50 MB) and a download timeout.
- **Output format** is PNG (`/result` returns `image/png`).
- **Retention:** finished jobs and their result files are removed after
  `RESULT_TTL_HOURS` (default 24h). Fetch results before they expire, or the
  result endpoint returns `410`.
- **Idempotency:** each `POST /api/upscale` creates a new job; the API doesn't
  dedupe identical requests.
