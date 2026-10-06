"""Keep the seeded demo fleet showing 'online' by refreshing heartbeats.

The dashboard marks a camera offline after 90s without a heartbeat. Seeded cameras
send ONE heartbeat at seed time, so they go offline ~90s later. This re-posts a
fresh heartbeat for every device every INTERVAL seconds so a demo fleet keeps
reading online.

This is SIMULATED liveness for the demo-master tenant (those cameras aren't really
streaming) — a dashboard affordance consistent with the seeded demo data, not real
inference. Run it DETACHED (PowerShell Start-Process) so it outlives a single
command; stop it by killing this process.

    python tools/keep_fleet_online.py --tenant demo-master --interval 60
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def get_devices(base: str, tenant: str) -> list[dict]:
    req = urllib.request.Request(f"{base}/v1/tenants/{tenant}/devices")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def post_heartbeat(base: str, tenant: str, dev: dict) -> None:
    hb = {
        "device_id": dev["device_id"],
        "tenant_id": tenant,
        "agent_version": dev.get("agent_version", "0.1.0"),
        "uptime_seconds": float(dev.get("uptime_seconds", 3600.0)) + 60.0,
        "cpu_percent": dev.get("cpu_percent"),
        "gpu_percent": dev.get("gpu_percent"),
        "gpu_temp_c": dev.get("gpu_temp_c"),
        "disk_free_gb": dev.get("disk_free_gb"),
        "stream_fps": dev.get("stream_fps", {}),
        "queued_events": dev.get("queued_events", 0) or 0,
    }
    req = urllib.request.Request(
        f"{base}/v1/ingest/heartbeat", data=json.dumps(hb).encode(),
        headers={"Content-Type": "application/json", "X-Device-Tenant": tenant},
        method="POST")
    urllib.request.urlopen(req, timeout=20).read()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--tenant", default="demo-master")
    ap.add_argument("--interval", type=float, default=60.0)
    args = ap.parse_args()

    print(f"[fleet] keeping {args.tenant} online every {args.interval:.0f}s", flush=True)
    while True:
        try:
            devs = get_devices(args.base, args.tenant)

            def _one(d):
                try:
                    post_heartbeat(args.base, args.tenant, d)
                    return True
                except Exception:
                    return False

            t0 = time.time()
            with ThreadPoolExecutor(max_workers=24) as ex:
                n = sum(ex.map(_one, devs))
            print(f"[fleet] refreshed {n}/{len(devs)} heartbeats in {time.time()-t0:.1f}s", flush=True)
        except Exception as e:
            print(f"[fleet] error: {e}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
