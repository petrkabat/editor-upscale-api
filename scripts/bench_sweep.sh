#!/usr/bin/env bash
# Sweep worker counts, benchmark each, and print a scaling summary.
#
# Run this ON THE SERVER (needs docker + the GPU). For every worker count it
# scales the `worker` service, waits for the workers to come up, runs
# scripts/benchmark.py, and appends the result to a CSV. At the end it prints a
# table with speedup and parallel efficiency.
#
# Usage:
#   ./scripts/bench_sweep.sh
#   WORKERS="1 2 4 8" COUNT=200 API_TOKEN=xxx ./scripts/bench_sweep.sh
#
# Env vars:
#   WORKERS    worker counts to test        (default "1 2 3 4 5")
#   COUNT      jobs per run                  (default 100)
#   BASE_URL   API base url                  (default http://localhost:8000)
#   API_TOKEN  bearer token if API needs it  (default empty)
#   IMAGE      custom image url              (default: benchmark's sample)
#   CSV        output csv                    (default bench-<timestamp>.csv)
#   WARMUP     seconds to let workers connect after scaling (default 8)
#   PYTHON     python interpreter            (default python3)
set -euo pipefail

WORKERS="${WORKERS:-1 2 3 4 5}"
COUNT="${COUNT:-100}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN:-}"
IMAGE="${IMAGE:-}"
CSV="${CSV:-bench-$(date +%Y%m%d-%H%M%S).csv}"
WARMUP="${WARMUP:-8}"
PYTHON="${PYTHON:-python3}"

here="$(cd "$(dirname "$0")" && pwd)"

extra=()
[ -n "$IMAGE" ] && extra+=(--image-url "$IMAGE")
[ -n "$API_TOKEN" ] && extra+=(--token "$API_TOKEN")

echo "Sweep: workers=[$WORKERS]  count=$COUNT  base=$BASE_URL"
echo "Output: $CSV"

for n in $WORKERS; do
  echo
  echo "### Scaling to $n worker(s) ..."
  docker compose up -d --scale worker="$n" worker

  # Wait until n worker containers are actually running.
  for _ in $(seq 1 30); do
    running=$(docker compose ps -q worker | wc -l | tr -d ' ')
    [ "$running" -ge "$n" ] && break
    sleep 1
  done
  echo "    $running worker(s) up; warming up ${WARMUP}s ..."
  sleep "$WARMUP"

  "$PYTHON" "$here/benchmark.py" \
    --base-url "$BASE_URL" --count "$COUNT" \
    --label "$n workers" --csv "$CSV" "${extra[@]}"
done

echo
echo "============================ SCALING SUMMARY ============================"
awk -F, 'NR==1 { print "workers,wall_s,img/s,s/img,speedup,efficiency"; next }
  { split($2, a, " "); n = a[1]; if (base == "") base = $7
    printf "%s,%.1f,%.3f,%.2f,%.2fx,%.0f%%\n", n, $6, $7, $8, $7/base, ($7/base)/n*100 }' \
  "$CSV" | column -t -s,
echo "========================================================================"
echo "Raw data: $CSV"
