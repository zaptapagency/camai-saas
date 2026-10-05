"""Import REAL public traffic cameras (Caltrans CWWP2) into a demo-master edge config.

Caltrans publishes per-district CCTV catalogs as keyless JSON, each camera carrying a
direct HLS stream (…/playlist.m3u8) that OpenCV opens without yt-dlp. Summed across the
12 districts that's hundreds of genuine, currently-live public cameras — real freeway
and arterial scenes with real vehicles/people.

These are traffic scenes, so cameras are assigned the verticals that honestly run on
them with the default model: `traffic` (directional vehicle counts) and a slice of
`wrong_way`. The output is a normal edge config you run with `camai-edge --config`.

    # write the full catalog (every district, all cameras with a stream):
    python tools/import_caltrans_cams.py --out packages/edge-agent/demo-master-caltrans.yaml

    # a small runnable subset for a CPU box (one GPU ≈ 4-8 streams, CPU far fewer):
    python tools/import_caltrans_cams.py --max 3 --out packages/edge-agent/caltrans-run.yaml

NOTE: this writes config only — it does NOT fabricate events. A camera shows up in the
dashboard once the edge agent actually runs it and reports. Running hundreds at once
needs a real edge FLEET (many boxes); on one machine run a handful.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

import yaml

DISTRICTS = list(range(1, 13))  # Caltrans districts d1..d12
URL = "https://cwwp2.dot.ca.gov/data/d{n}/cctv/cctvStatusD{n:02d}.json"


def slug(s: str, n: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (s or "cam").lower()).strip("-")[:28] or "cam"
    return f"{base}-{n:04d}"


def fetch_district(n: int) -> list[dict]:
    try:
        with urllib.request.urlopen(URL.format(n=n), timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  ! district {n}: {e}")
        return []
    cams = []
    for row in data.get("data", []):
        c = row.get("cctv", {})
        hls = (c.get("imageData", {}) or {}).get("streamingVideoURL", "")
        if not hls or ".m3u8" not in hls:
            continue
        loc = c.get("location", {})
        cams.append({"name": loc.get("locationName", ""), "hls": hls,
                     "lat": loc.get("latitude"), "lng": loc.get("longitude")})
    return cams


def build(cams: list[dict], base_url: str) -> dict:
    out = []
    for i, cam in enumerate(cams):
        # Freeway scenes: mostly traffic volume, a slice watched for wrong-way.
        mode = "wrong_way" if i % 5 == 4 else "traffic"
        out.append({
            "id": slug(cam["name"], i),
            "source": cam["hls"],
            "mode": mode,
            "target_fps": 2,
            "min_confidence": 0.35,
            "snapshot_seconds": 5,
            "lines": [{"id": "cordon", "a": {"x": 0.08, "y": 0.55}, "b": {"x": 0.92, "y": 0.55}}],
        })
    return {
        "tenant_id": "demo-master",
        "site_id": "demo-master-caltrans",
        "device_id": "edge-caltrans",
        "detector": {"weights": "yolo11n.pt", "device": "auto", "imgsz": 640},
        "cloud": {"ingest_url": base_url, "flush_interval_seconds": 5, "heartbeat_interval_seconds": 15},
        "cameras": out,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--districts", type=int, nargs="*", default=DISTRICTS)
    ap.add_argument("--max", type=int, default=0, help="cap cameras written (0 = all)")
    ap.add_argument("--base", default="http://localhost:8000")
    args = ap.parse_args()

    all_cams: list[dict] = []
    for n in args.districts:
        got = fetch_district(n)
        print(f"  district d{n}: {len(got)} cameras with a live stream")
        all_cams.extend(got)
        if args.max and len(all_cams) >= args.max:
            break
    if args.max:
        all_cams = all_cams[: args.max]

    config = build(all_cams, args.base)
    args.out.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    n_traffic = sum(1 for c in config["cameras"] if c["mode"] == "traffic")
    print(f"wrote {args.out} — {len(config['cameras'])} REAL Caltrans cameras "
          f"({n_traffic} traffic, {len(config['cameras']) - n_traffic} wrong-way)")
    print("run a subset:  PYTHONPATH=packages/schema;packages/edge-agent "
          f"python -m camai_edge.main --config {args.out}")


if __name__ == "__main__":
    main()
