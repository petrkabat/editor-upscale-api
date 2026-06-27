#!/usr/bin/env bash
# Smoke-test the upscale API end to end.
# Usage: ./scripts/test_upscale.sh [IMAGE_URL] [SCALE] [MODEL]
set -euo pipefail

BASE="${BASE_URL:-http://localhost:8000}"
IMAGE_URL="${1:-https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/inputs/0014.jpg}"
SCALE="${2:-4}"
MODEL="${3:-realesrgan-x4plus}"
OUT="${OUT:-out.png}"

echo "→ Submitting job for $IMAGE_URL (scale=$SCALE, model=$MODEL)"
JOB_ID=$(curl -fsS -X POST "$BASE/api/upscale" \
  -H 'Content-Type: application/json' \
  -d "{\"image_url\":\"$IMAGE_URL\",\"scale\":$SCALE,\"model\":\"$MODEL\"}" \
  | sed -E 's/.*"id":"([^"]+)".*/\1/')

echo "→ Job id: $JOB_ID"

while :; do
  STATUS=$(curl -fsS "$BASE/api/jobs/$JOB_ID" | sed -E 's/.*"status":"([^"]+)".*/\1/')
  echo "   status: $STATUS"
  case "$STATUS" in
    succeeded) break ;;
    failed)    echo "✗ Job failed:"; curl -fsS "$BASE/api/jobs/$JOB_ID"; exit 1 ;;
  esac
  sleep 2
done

echo "→ Downloading result to $OUT"
curl -fsS "$BASE/api/jobs/$JOB_ID/result" -o "$OUT"
echo "✓ Done: $OUT"
