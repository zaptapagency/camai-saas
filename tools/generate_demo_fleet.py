"""Generate a large demo FLEET for the `demo-master` tenant — N cameras spread
across every vertical — and (a) seed representative events + heartbeats so the
dashboard immediately shows the whole fleet, and (b) emit a live-camera spec for
the verticals that can actually run on a public cam.

Why it works this way:
* **Live inference doesn't scale to 100 feeds** (one GPU box handles ~4–8 streams)
  and public stream URLs rot/expire — so the fleet is *seeded* to look complete,
  while a runnable subset is wired to a small pool of real public cams.
* **Only person/vehicle verticals can run on a public cam** with the default model.
  The model-gated ones (PPE/fire/thermal/proximity/weapon/abandoned-object) are
  seeded as clearly-simulated cameras — they need a trained model or sensor.

Usage (cloud must be up on :8000):
    PYTHONPATH=packages/schema python tools/generate_demo_fleet.py            # 100 cams
    PYTHONPATH=packages/schema python tools/generate_demo_fleet.py --count 60
    PYTHONPATH=packages/schema python tools/generate_demo_fleet.py --no-seed  # just the spec

Outputs:
* posts events + heartbeats for N cameras to the cloud (unless --no-seed)
* writes packages/edge-agent/demo-master-fleet.cams.yaml — a spec for
  tools/resolve_streams.py (fill tools/public_cams.example.yaml first).
"""

from __future__ import annotations

import argparse
import json
import random
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from camai_schema import Event, EventBatch, EventType, Heartbeat, Mode, ObjectClass

REPO = Path(__file__).resolve().parents[1]
DEFAULT_POOL = REPO / "tools" / "public_cams.example.yaml"
DEFAULT_OUT = REPO / "packages" / "edge-agent" / "demo-master-fleet.cams.yaml"

# vertical -> (live_capable, scene for a public cam, geometry kind)
VERTICALS = [
    ("retail",           True,  "crosswalk",    "line"),
    ("queue",            True,  "crosswalk",    "zone"),
    ("capacity",         True,  "plaza",        "zone"),
    ("crowd_density",    True,  "plaza",        "zone"),
    ("staffing",         True,  "crosswalk",    "zone"),
    ("parking",          True,  "parking",      "zone"),
    ("traffic",          True,  "intersection", "line"),
    ("drive_thru",       True,  "intersection", "zone"),
    ("loitering",        True,  "plaza",        "zone"),
    ("intrusion",        True,  "plaza",        "zone"),
    ("tailgating",       True,  "crosswalk",    "line"),
    ("wrong_way",        True,  "intersection", "line"),
    ("fall",             True,  "plaza",        None),
    ("safety",           False, None,           None),
    ("fire",             False, None,           None),
    ("thermal",          False, None,           None),
    ("proximity",        False, None,           None),
    ("weapon",           False, None,           None),
    ("abandoned_object", False, None,           None),
]

now = datetime.now(timezone.utc)


def ago(s: float) -> datetime:
    return now - timedelta(seconds=s)


def emit(tenant: str, site: str, cam: str, mode: Mode, rnd: random.Random) -> list[Event]:
    """A small, representative event burst for one camera of a given vertical."""
    def ev(type_, **p):
        return Event(tenant_id=tenant, site_id=site, camera_id=cam, type=type_, mode=mode, **p)

    e: list[Event] = []
    if mode == Mode.retail:
        for i in range(rnd.randint(8, 18)):
            e.append(ev(EventType.entry, ts=ago(900 - i * 40), line_id="door", object_class=ObjectClass.person, track_id=i))
        for i in range(rnd.randint(4, 12)):
            e.append(ev(EventType.exit, ts=ago(850 - i * 40), line_id="door", object_class=ObjectClass.person, track_id=100 + i))
        for i in range(rnd.randint(3, 6)):
            e.append(ev(EventType.dwell, ts=ago(200 - i * 20), zone_id="aisle", dwell_seconds=round(rnd.uniform(40, 420), 1)))
    elif mode == Mode.queue:
        for i in range(rnd.randint(4, 10)):
            e.append(ev(EventType.dwell, ts=ago(200 - i * 18), zone_id="queue", dwell_seconds=round(rnd.uniform(20, 240), 1)))
        e.append(ev(EventType.occupancy_sample, ts=ago(20), zone_id="queue", count=rnd.randint(1, 7)))
    elif mode == Mode.capacity:
        for i in range(rnd.randint(1, 5)):
            e.append(ev(EventType.capacity_breach, ts=ago(500 - i * 80), zone_id="hall", count=rnd.randint(49, 62), labels=["limit:48"]))
        e.append(ev(EventType.occupancy_sample, ts=ago(20), zone_id="hall", count=rnd.randint(40, 60), labels=["over"]))
    elif mode == Mode.crowd_density:
        for i in range(rnd.randint(1, 5)):
            e.append(ev(EventType.crowd_alert, ts=ago(400 - i * 90), zone_id="atrium", count=rnd.randint(28, 48), labels=["threshold:25"]))
        e.append(ev(EventType.occupancy_sample, ts=ago(20), zone_id="atrium", count=rnd.randint(25, 45), labels=["over"]))
    elif mode == Mode.staffing:
        for zid, st in [("grill", "active"), ("prep", "static"), ("expo", "active"), ("dish", None)]:
            head = 0 if st is None else rnd.randint(1, 2)
            e.append(ev(EventType.occupancy_sample, ts=ago(20), zone_id=zid, count=head, labels=[st] if st else None))
    elif mode == Mode.parking:
        for s in range(1, rnd.randint(5, 9)):
            parked = rnd.random() < 0.6
            e.append(ev(EventType.vehicle_parked if parked else EventType.vehicle_left, ts=ago(700 - s * 30), zone_id=f"space-{s}", object_class=ObjectClass.vehicle))
            e.append(ev(EventType.occupancy_sample, ts=ago(20), zone_id=f"space-{s}", count=1 if parked else 0))
    elif mode == Mode.traffic:
        for i in range(rnd.randint(10, 24)):
            e.append(ev(EventType.vehicle_crossing, ts=ago(1000 - i * 40), line_id="cordon", object_class=ObjectClass.vehicle, labels=[rnd.choice(["forward", "reverse"])]))
    elif mode == Mode.drive_thru:
        for i in range(rnd.randint(5, 12)):
            e.append(ev(EventType.dwell, ts=ago(300 - i * 22), zone_id="lane", dwell_seconds=round(rnd.uniform(90, 330), 1), object_class=ObjectClass.vehicle, track_id=600 + i))
    elif mode == Mode.loitering:
        for i in range(rnd.randint(2, 6)):
            e.append(ev(EventType.loitering_alert, ts=ago(500 - i * 90), zone_id="plaza", track_id=700 + i, count=1, labels=[f"{rnd.randint(30, 120)}s"]))
    elif mode == Mode.intrusion:
        for i in range(rnd.randint(1, 5)):
            e.append(ev(EventType.intrusion_alert, ts=ago(450 - i * 100), zone_id="restricted", track_id=800 + i, count=1))
    elif mode == Mode.tailgating:
        for i in range(rnd.randint(2, 6)):
            e.append(ev(EventType.tailgating_alert, ts=ago(500 - i * 80), line_id="door", count=2, labels=["2_together"]))
    elif mode == Mode.wrong_way:
        for i in range(rnd.randint(2, 7)):
            e.append(ev(EventType.wrong_way_alert, ts=ago(500 - i * 70), line_id="lane", object_class=ObjectClass.vehicle, track_id=1100 + i, count=1, labels=["wrong_way"]))
    elif mode == Mode.fall:
        for i in range(rnd.randint(1, 4)):
            e.append(ev(EventType.fall_alert, ts=ago(500 - i * 95), object_class=ObjectClass.person, track_id=900 + i, count=1, labels=[f"{rnd.randint(3, 25)}s"]))
    elif mode == Mode.safety:
        for i in range(rnd.randint(2, 7)):
            e.append(ev(EventType.ppe_violation, ts=ago(600 - i * 70), zone_id="hazard", count=rnd.randint(1, 2), track_id=i, labels=rnd.choice([["helmet"], ["vest"], ["helmet", "vest"]])))
    elif mode == Mode.fire:
        for i in range(rnd.randint(1, 3)):
            e.append(ev(EventType.hazard_alert, ts=ago(400 - i * 120), zone_id="cookline", count=rnd.randint(1, 3), labels=rnd.choice([["smoke"], ["fire", "smoke"]])))
    elif mode == Mode.thermal:
        for i in range(rnd.randint(1, 4)):
            e.append(ev(EventType.overheat_alert, ts=ago(400 - i * 90), count=1, track_id=i, labels=[f"{rnd.uniform(38.5, 41):.1f}C"]))
    elif mode == Mode.proximity:
        for i in range(rnd.randint(1, 4)):
            e.append(ev(EventType.proximity_alert, ts=ago(400 - i * 110), count=1, track_id=i, labels=[f"dist:{rnd.uniform(0.06, 0.13):.2f}"]))
    elif mode == Mode.weapon:
        for i in range(rnd.randint(1, 3)):
            e.append(ev(EventType.weapon_alert, ts=ago(400 - i * 130), zone_id="lobby", count=1, labels=rnd.choice([["knife"], ["gun"]])))
    elif mode == Mode.abandoned_object:
        for i in range(rnd.randint(1, 4)):
            e.append(ev(EventType.abandoned_object_alert, ts=ago(450 - i * 100), zone_id="hall", object_class=ObjectClass.bag, track_id=1000 + i, count=1, labels=[f"{rnd.randint(20, 90)}s"]))
    return e


def geometry(kind: str | None) -> dict:
    """A simple zones/lines block per geometry kind, for the live-cam spec."""
    if kind == "line":
        return {"lines": [{"id": "cordon", "a": {"x": 0.10, "y": 0.55}, "b": {"x": 0.90, "y": 0.55}}]}
    if kind == "zone":
        return {"zones": [{"id": "area", "capacity": 25,
                           "polygon": [{"x": 0.08, "y": 0.30}, {"x": 0.92, "y": 0.30},
                                       {"x": 0.92, "y": 0.96}, {"x": 0.08, "y": 0.96}]}]}
    return {}  # fall: whole-frame


def allocate(count: int) -> list[tuple[str, bool, str | None, str | None]]:
    """Round-robin N cameras across the verticals (roughly equal per vertical)."""
    out = []
    for i in range(count):
        out.append(VERTICALS[i % len(VERTICALS)])
    return out


def post(base: str, path: str, body: str, tenant: str) -> None:
    req = urllib.request.Request(
        base + path, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Device-Tenant": tenant},
        method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:  # best-effort seeder
        print(f"  ! post {path} failed: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--tenant", default="demo-master")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-seed", action="store_true", help="only write the live-cam spec")
    args = ap.parse_args()

    tenant, site = args.tenant, f"{args.tenant}-site"
    pool = yaml.safe_load(args.pool.read_text(encoding="utf-8")).get("scenes", {})
    scene_idx: dict[str, int] = {}

    plan = allocate(args.count)
    per_vertical = [0] * len(VERTICALS)
    all_events: list[Event] = []
    cameras_meta: list[tuple[str, Mode]] = []
    live_cams: list[dict] = []

    for i, (name, live, scene, kind) in enumerate(plan):
        vi = next(k for k, v in enumerate(VERTICALS) if v[0] == name)
        per_vertical[vi] += 1
        mode = Mode(name)
        cam = f"cam-{name.replace('_', '-')}-{per_vertical[vi]:02d}"
        rnd = random.Random(f"{tenant}:{cam}")
        all_events.extend(emit(tenant, site, cam, mode, rnd))
        cameras_meta.append((cam, mode))

        if live and scene:
            urls = pool.get(scene) or [f"REPLACE_WITH_LIVE_{scene.upper()}_CAM"]
            k = scene_idx.get(scene, 0); scene_idx[scene] = k + 1
            entry = {"id": cam, "page_url": urls[k % len(urls)], "mode": name,
                     "target_fps": 2 if kind != "line" else 3, "snapshot_seconds": 3}
            entry.update(geometry(kind))
            live_cams.append(entry)

    # Write the live-cam spec for resolve_streams.py.
    spec = {"tenant_id": tenant, "site_id": site, "device_id": f"edge-{tenant}",
            "detector": {"weights": "yolo11n.pt", "device": "auto", "imgsz": 640},
            "cloud": {"ingest_url": args.base, "flush_interval_seconds": 5, "heartbeat_interval_seconds": 15},
            "cameras": live_cams}
    args.out.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    live_n = sum(1 for _, l, _, _ in plan if l)
    print(f"planned {args.count} cameras across {len(VERTICALS)} verticals "
          f"({live_n} live-capable, {args.count - live_n} simulated)")
    print(f"wrote live-cam spec -> {args.out} ({len(live_cams)} cameras)")

    if args.no_seed:
        return

    # Seed events in batches + one heartbeat per camera.
    for j in range(0, len(all_events), 50):
        batch = EventBatch(device_id=f"edge-{tenant}", tenant_id=tenant, events=all_events[j:j + 50])
        post(args.base, "/v1/ingest/events", batch.model_dump_json(), tenant)
    for cam, _mode in cameras_meta:
        rnd = random.Random(f"hb:{cam}")
        hb = Heartbeat(device_id=f"edge-{cam}", tenant_id=tenant, agent_version="0.1.0",
                       uptime_seconds=rnd.uniform(3600, 90000), cpu_percent=rnd.uniform(8, 55),
                       gpu_percent=rnd.uniform(20, 80), gpu_temp_c=rnd.uniform(45, 68),
                       disk_free_gb=rnd.uniform(30, 220), stream_fps={cam: round(rnd.uniform(6, 12), 1)},
                       queued_events=rnd.randint(0, 5))
        post(args.base, "/v1/ingest/heartbeat", hb.model_dump_json(), tenant)

    print(f"seeded {len(all_events)} events + {len(cameras_meta)} camera heartbeats "
          f"for tenant '{tenant}'")


if __name__ == "__main__":
    main()
