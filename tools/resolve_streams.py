"""Resolve public live-camera page URLs into direct stream URLs the edge can open,
and write a ready-to-run edge config for the `demo-master` tenant.

Why this exists: OpenCV (the edge ingest path) opens a direct RTSP/HLS URL, not a
YouTube/watch page, and a resolved HLS URL carries an `expire` param — it dies
after a few hours. A 3-hour live demo therefore needs its stream URLs resolved at
start and refreshed periodically. This script does both.

Input: a cams spec (see tools/demo_master_cams.example.yaml) listing each camera's
mode, zones/lines, and a `page_url` (a YouTube live page, or a direct RTSP/HLS URL
to pass through). Output: packages/edge-agent/demo-master.yaml with resolved
`source:` URLs, ready for `camai-edge --config`.

Only person/vehicle verticals belong here — those run on the default COCO model
against public cams. Model-gated verticals (weapon/fire/thermal/proximity/
abandoned_object) can't run on a public feed and are seeded as demo data instead.

Usage:
    python tools/resolve_streams.py                 # resolve once
    python tools/resolve_streams.py --watch 2700    # re-resolve every 45 min
    python tools/resolve_streams.py --spec my.yaml --out edge.yaml

Requires `yt-dlp` on PATH for YouTube pages (pip install yt-dlp). Direct
rtsp://, http(s) .m3u8 and file sources are passed through unchanged.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO / "tools" / "demo_master_cams.example.yaml"
DEFAULT_OUT = REPO / "packages" / "edge-agent" / "demo-master.yaml"


def resolve_source(page_url: str) -> str:
    """Return a direct stream URL the edge can open. Pass through non-YouTube URLs."""
    low = page_url.lower()
    if low.startswith(("rtsp://", "rtmp://")) or ".m3u8" in low or low.startswith("file:"):
        return page_url
    if "youtube.com" in low or "youtu.be" in low:
        out = subprocess.run(
            ["yt-dlp", "-g", "--extractor-args",
             "youtube:player_client=android", page_url],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            raise RuntimeError(f"yt-dlp failed for {page_url}: {out.stderr.strip()}")
        # -g may print video+audio URLs; the first is the media stream.
        return out.stdout.strip().splitlines()[0]
    return page_url  # best effort: hand it to OpenCV as-is


def build_config(spec: dict) -> dict:
    cams = []
    for cam in spec.get("cameras", []):
        try:
            source = resolve_source(cam["page_url"])
        except Exception as e:  # keep going; one dead cam shouldn't sink the demo
            print(f"  ! {cam['id']}: {e}", file=sys.stderr)
            continue
        entry = {k: v for k, v in cam.items() if k != "page_url"}
        entry["source"] = source
        cams.append(entry)
        print(f"  ok {cam['id']} ({cam.get('mode')}) -> resolved")
    return {
        "tenant_id": spec.get("tenant_id", "demo-master"),
        "site_id": spec.get("site_id", "demo-master-site"),
        "device_id": spec.get("device_id", "edge-demo-master"),
        "detector": spec.get("detector", {"weights": "yolo11n.pt", "device": "auto", "imgsz": 640}),
        "cloud": spec.get("cloud", {
            "ingest_url": "http://localhost:8000",
            "flush_interval_seconds": 5,
            "heartbeat_interval_seconds": 15,
        }),
        "cameras": cams,
    }


def write_once(spec_path: Path, out_path: Path) -> None:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    config = build_config(spec)
    out_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    print(f"wrote {out_path} ({len(config['cameras'])} live cameras)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--watch", type=float, default=0.0,
                    help="re-resolve every N seconds (0 = once). Use ~2700 for a 3h demo.")
    args = ap.parse_args()

    write_once(args.spec, args.out)
    while args.watch > 0:
        time.sleep(args.watch)
        print("re-resolving stream URLs (they expire)…")
        try:
            write_once(args.spec, args.out)
        except Exception as e:
            print(f"  ! refresh failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
