#!/usr/bin/env bash
# Smoke-test the upscale API end to end.
# Usage: ./scripts/test_upscale.sh [IMAGE_URL] [SCALE] [MODEL]
# Env: BASE_URL, OUT, API_TOKEN (sent as Authorization: Bearer if set)
set -euo pipefail

BASE="${BASE_URL:-http://localhost:8000}"
IMAGE_URL="${1:-https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/inputs/0014.jpg}"
SCALE="${2:-4}"
MODEL="${3:-realesrgan-x4plus}"
OUT="${OUT:-out.png}"

AUTH=()
[ -n "${API_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $API_TOKEN")

echo "→ Submitting job for $IMAGE_URL (scale=$SCALE, model=$MODEL)"
JOB_ID=$(curl -fsS -X POST "$BASE/api/upscale" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"image_url\":\"$IMAGE_URL\",\"scale\":$SCALE,\"model\":\"$MODEL\"}" \
  | sed -E 's/.*"id":"([^"]+)".*/\1/')

echo "→ Job id: $JOB_ID"

while :; do
  STATUS=$(curl -fsS "${AUTH[@]}" "$BASE/api/jobs/$JOB_ID" | sed -E 's/.*"status":"([^"]+)".*/\1/')
  echo "   status: $STATUS"
  case "$STATUS" in
    succeeded) break ;;
    failed)    echo "✗ Job failed:"; curl -fsS "${AUTH[@]}" "$BASE/api/jobs/$JOB_ID"; exit 1 ;;
  esac
  sleep 2
done

echo "→ Downloading result to $OUT"
curl -fsS "${AUTH[@]}" "$BASE/api/jobs/$JOB_ID/result" -o "$OUT"
echo "✓ Done: $OUT"
