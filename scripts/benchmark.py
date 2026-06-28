#!/usr/bin/env python3
"""Throughput benchmark for the upscale API.

Submits N jobs (same image is fine), waits for them all to finish, and reports
throughput and the effective time per image. The headline metric — effective
seconds/image = wall-clock / N — is what improves as you add workers/GPUs.

Usage:
    python scripts/benchmark.py --count 100 --label "1 worker, 1x4070"
    BASE_URL=https://upscale.example.com API_TOKEN=... \\
        python scripts/benchmark.py --count 100 --csv bench.csv --label "2 workers"

Run the same command after changing your deployment (e.g.
`docker compose up -d --scale worker=2`) and compare the numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

DEFAULT_IMAGE = (
    "https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/inputs/0014.jpg"
)
TERMINAL = {"succeeded", "failed"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    p.add_argument("--count", type=int, default=100, help="number of jobs to submit")
    p.add_argument("--image-url", default=DEFAULT_IMAGE)
    p.add_argument("--scale", type=int, default=4, choices=(2, 3, 4))
    p.add_argument("--model", default="realesrgan-x4plus")
    p.add_argument("--token", default=os.environ.get("API_TOKEN", ""))
    p.add_argument("--concurrency", type=int, default=20, help="parallel HTTP requests")
    p.add_argument("--poll-interval", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=3600, help="overall wait limit (s)")
    p.add_argument("--label", default="", help="note stored in CSV (e.g. config)")
    p.add_argument("--csv", default="", help="append one result row to this file")
    return p.parse_args()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def _submit_all(
    client: httpx.AsyncClient, args: argparse.Namespace
) -> list[str]:
    sem = asyncio.Semaphore(args.concurrency)
    body = {"image_url": args.image_url, "scale": args.scale, "model": args.model}

    async def submit() -> str | None:
        async with sem:
            try:
                r = await client.post("/api/upscale", json=body)
                r.raise_for_status()
                return r.json()["id"]
            except httpx.HTTPError as exc:
                print(f"  submit failed: {exc}")
                return None

    ids = await asyncio.gather(*(submit() for _ in range(args.count)))
    return [i for i in ids if i]


async def _fetch(client: httpx.AsyncClient, job_id: str) -> dict:
    r = await client.get(f"/api/jobs/{job_id}")
    r.raise_for_status()
    return r.json()


async def _wait_all(
    client: httpx.AsyncClient, ids: list[str], args: argparse.Namespace
) -> dict[str, dict]:
    sem = asyncio.Semaphore(args.concurrency)
    final: dict[str, dict] = {}
    pending = set(ids)
    deadline = time.monotonic() + args.timeout

    async def fetch(job_id: str) -> dict:
        async with sem:
            return await _fetch(client, job_id)

    while pending and time.monotonic() < deadline:
        jobs = await asyncio.gather(*(fetch(i) for i in pending))
        for job in jobs:
            if job["status"] in TERMINAL:
                final[job["id"]] = job
                pending.discard(job["id"])
        done = len(final)
        print(f"  {done}/{len(ids)} done...", end="\r", flush=True)
        if pending:
            await asyncio.sleep(args.poll_interval)
    print()
    return final


def _report(
    final: dict[str, dict], submitted: int, args: argparse.Namespace
) -> None:
    succeeded = [j for j in final.values() if j["status"] == "succeeded"]
    failed = [j for j in final.values() if j["status"] == "failed"]
    n = len(final)
    if not succeeded:
        print("No jobs succeeded — nothing to measure.")
        if failed:
            print(f"First error: {failed[0].get('error')}")
        return

    # Server-side wall clock: first job created -> last job finished.
    created = [_parse_ts(j["created_at"]) for j in final.values()]
    updated = [_parse_ts(j["updated_at"]) for j in final.values()]
    wall = (max(updated) - min(created)).total_seconds()

    # Per-job latency (queue wait + processing), for reference only.
    latencies = [
        (_parse_ts(j["updated_at"]) - _parse_ts(j["created_at"])).total_seconds()
        for j in final.values()
    ]
    latencies.sort()
    mean_lat = sum(latencies) / len(latencies)

    throughput = len(succeeded) / wall if wall > 0 else 0.0
    per_image = wall / len(succeeded) if succeeded else 0.0

    print("=" * 52)
    if args.label:
        print(f"Label:               {args.label}")
    print(f"Jobs submitted:      {submitted}")
    print(f"Completed:           {n}  ({len(succeeded)} ok, {len(failed)} failed)")
    print(f"Wall clock:          {wall:.1f} s")
    print(f"Throughput:          {throughput:.3f} img/s")
    print(f"Effective per image: {per_image:.2f} s   <-- parallelism metric")
    print(
        f"Per-job latency:     min {latencies[0]:.1f}s / "
        f"mean {mean_lat:.1f}s / max {latencies[-1]:.1f}s  (incl. queue wait)"
    )
    print("=" * 52)
    if failed:
        print(f"Example error: {failed[0].get('error')}")

    if args.csv:
        path = Path(args.csv)
        new = not path.exists()
        with path.open("a") as fh:
            if new:
                fh.write(
                    "timestamp,label,count,succeeded,failed,"
                    "wall_s,throughput_img_s,sec_per_image\n"
                )
            fh.write(
                f"{datetime.now().isoformat()},{args.label},{submitted},"
                f"{len(succeeded)},{len(failed)},{wall:.1f},"
                f"{throughput:.3f},{per_image:.2f}\n"
            )
        print(f"Appended result to {path}")


async def main() -> None:
    args = parse_args()
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    print(f"Submitting {args.count} jobs to {args.base_url} ...")
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=30
    ) as client:
        t0 = time.monotonic()
        ids = await _submit_all(client, args)
        print(f"Submitted {len(ids)} jobs in {time.monotonic() - t0:.1f} s")
        if not ids:
            return
        final = await _wait_all(client, ids, args)

    _report(final, submitted=len(ids), args=args)


if __name__ == "__main__":
    asyncio.run(main())
