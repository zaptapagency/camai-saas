"""Seed one demo account PER vertical, each with its own camera, representative
events, a device heartbeat, and a placeholder annotated snapshot (so the live
camera view has something to show before a real edge agent streams frames).

Tenants: demo-retail, demo-queue, demo-capacity, demo-staffing, demo-safety,
demo-traffic, demo-parking, demo-fire, demo-thermal, demo-proximity, demo-warehouse.

Run (cloud must be up on :8000):
    PYTHONPATH=packages/schema python tools/seed_vertical_accounts.py
"""
from __future__ import annotations

import base64
import json
import random
import urllib.request
from datetime import datetime, timedelta, timezone

from camai_schema import Event, EventBatch, EventType, Heartbeat, Mode, ObjectClass

BASE = "http://localhost:8000"
now = datetime.now(timezone.utc)


def ago(s: float) -> datetime:
    return now - timedelta(seconds=s)


def post(path: str, body: str, tenant: str) -> int:
    req = urllib.request.Request(
        BASE + path, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Device-Tenant": tenant},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception as e:  # best-effort seeder
        print("  ! post failed", path, e)
        return 0


def placeholder_jpeg(title: str, mode: str) -> str:
    """A small annotated 'camera frame' as base64 JPEG. Uses cv2 if available,
    else returns a tiny hardcoded 1x1 JPEG so seeding still works headless."""
    try:
        import cv2
        import numpy as np

        h, w = 270, 480
        img = np.full((h, w, 3), 24, dtype=np.uint8)
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (60, 70, 90), 2)
        # a couple of fake detection boxes + zone
        cv2.rectangle(img, (120, 90), (170, 200), (90, 200, 120), 2)
        cv2.putText(img, "person", (120, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 200, 120), 1)
        cv2.rectangle(img, (260, 110), (310, 205), (90, 200, 120), 2)
        cv2.rectangle(img, (40, 60), (440, 230), (200, 160, 60), 1)
        cv2.putText(img, title, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 245), 2)
        cv2.putText(img, f"mode={mode}  DEMO / synthetic frame", (16, 255),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 160, 180), 1)
        ok, buf = cv2.imencode(".jpg", img)
        if ok:
            return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:
        pass
    # 1x1 black JPEG fallback
    return ("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
            "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAARCAABAAEDASIAAhEBAxEB"
            "/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9"
            "AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
            "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKj"
            "pKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6"
            "/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3"
            "AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2"
            "Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJma"
            "oqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6"
            "/9oADAMBAAIRAxEAPwD3+iiigD//2Q==")


def ev(tenant, site, camera, type_, mode, ts_sec, **payload):
    return Event(tenant_id=tenant, site_id=site, camera_id=camera, type=type_,
                 mode=mode, ts=ago(ts_sec), **payload)


# (mode, camera, predicted_count, event-builder) per vertical
def build(tenant, site, camera, mode):
    e = []
    if mode == Mode.retail:
        for i in range(16): e.append(ev(tenant, site, camera, EventType.entry, mode, 900 - i*40, line_id="door", object_class=ObjectClass.person, track_id=i))
        for i in range(11): e.append(ev(tenant, site, camera, EventType.exit, mode, 850 - i*40, line_id="door", object_class=ObjectClass.person, track_id=100+i))
        for i in range(6): e.append(ev(tenant, site, camera, EventType.dwell, mode, 200 - i*20, zone_id="aisle", dwell_seconds=round(random.uniform(60, 400), 1)))
        for t in range(0, 180, 20): e.append(ev(tenant, site, camera, EventType.occupancy_sample, mode, t, zone_id="aisle", count=random.randint(2, 9)))
        return e, 5
    if mode == Mode.queue:
        for i in range(10): e.append(ev(tenant, site, camera, EventType.dwell, mode, 200 - i*18, zone_id="queue", dwell_seconds=round(random.uniform(20, 220), 1)))
        for t in range(0, 180, 20): e.append(ev(tenant, site, camera, EventType.occupancy_sample, mode, t, zone_id="queue", count=random.randint(1, 6)))
        return e, 4
    if mode == Mode.capacity:
        for i in range(5): e.append(ev(tenant, site, camera, EventType.capacity_breach, mode, 500 - i*80, zone_id="hall", count=random.randint(50, 60), labels=["limit:48"]))
        e.append(ev(tenant, site, camera, EventType.occupancy_sample, mode, 20, zone_id="hall", count=53, labels=["over"]))
        return e, 53
    if mode == Mode.staffing:
        for zid, st in [("grill", "active"), ("prep", "static"), ("expo", "active"), ("dish", None)]:
            head = 0 if st is None else random.randint(1, 2)
            e.append(ev(tenant, site, camera, EventType.occupancy_sample, mode, 20, zone_id=zid, count=head, labels=[st] if st else None))
        return e, 3
    if mode == Mode.safety:
        for i in range(6): e.append(ev(tenant, site, camera, EventType.ppe_violation, mode, 600 - i*70, zone_id="hazard", count=random.randint(1, 2), track_id=i, labels=random.choice([["helmet"], ["vest"], ["helmet", "vest"]])))
        return e, 3
    if mode == Mode.traffic:
        for i in range(20): e.append(ev(tenant, site, camera, EventType.vehicle_crossing, mode, 1000 - i*45, line_id="cordon", object_class=ObjectClass.vehicle, labels=[random.choice(["forward", "reverse"])]))
        return e, 6
    if mode == Mode.parking:
        for s in range(1, 7):
            parked = random.random() < 0.6
            e.append(ev(tenant, site, camera, EventType.vehicle_parked if parked else EventType.vehicle_left, mode, 700 - s*30, zone_id=f"space-{s}", object_class=ObjectClass.vehicle))
            e.append(ev(tenant, site, camera, EventType.occupancy_sample, mode, 20, zone_id=f"space-{s}", count=1 if parked else 0))
        return e, 4
    if mode == Mode.fire:
        for i in range(3): e.append(ev(tenant, site, camera, EventType.hazard_alert, mode, 400 - i*120, zone_id="cookline", count=random.randint(1, 3), labels=random.choice([["smoke"], ["fire", "smoke"]])))
        return e, 1
    if mode == Mode.thermal:
        for i in range(4): e.append(ev(tenant, site, camera, EventType.overheat_alert, mode, 400 - i*90, count=1, track_id=i, labels=[f"{random.uniform(38.5, 41):.1f}C"]))
        return e, 1
    if mode == Mode.proximity:
        for i in range(3): e.append(ev(tenant, site, camera, EventType.proximity_alert, mode, 400 - i*110, count=1, track_id=i, labels=[f"dist:{random.uniform(0.06, 0.13):.2f}"]))
        return e, 2
    if mode == Mode.warehouse:
        for i in range(8): e.append(ev(tenant, site, camera, EventType.count_delta, mode, 700 - i*70, zone_id="bay-A", delta=random.choice([-2, -1, 1, 1, 2])))
        for t in range(0, 120, 20): e.append(ev(tenant, site, camera, EventType.occupancy_sample, mode, t, zone_id="bay-A", count=random.randint(4, 14)))
        return e, 9
    return e, 0


VERTICALS = [
    ("retail", Mode.retail, "cam-entrance"),
    ("queue", Mode.queue, "cam-checkout"),
    ("capacity", Mode.capacity, "cam-hall"),
    ("staffing", Mode.staffing, "cam-kitchen"),
    ("safety", Mode.safety, "cam-dock"),
    ("traffic", Mode.traffic, "cam-street"),
    ("parking", Mode.parking, "cam-lot"),
    ("fire", Mode.fire, "cam-cookline"),
    ("thermal", Mode.thermal, "cam-walkin"),
    ("proximity", Mode.proximity, "cam-warehouse"),
    ("warehouse", Mode.warehouse, "cam-bay"),
]

for key, mode, camera in VERTICALS:
    tenant = f"demo-{key}"
    site = f"{tenant}-site"
    random.seed(sum(map(ord, tenant)))
    events, predicted = build(tenant, site, camera, mode)
    for i in range(0, len(events), 50):
        post("/v1/ingest/events", EventBatch(device_id=f"edge-{camera}", tenant_id=tenant, events=events[i:i+50]).model_dump_json(), tenant)
    hb = Heartbeat(device_id=f"edge-{camera}", tenant_id=tenant, agent_version="0.1.0",
                   uptime_seconds=random.uniform(3600, 50000), cpu_percent=random.uniform(10, 50),
                   gpu_percent=random.uniform(20, 75), gpu_temp_c=random.uniform(45, 65),
                   disk_free_gb=random.uniform(40, 200), stream_fps={camera: round(random.uniform(6, 12), 1)},
                   queued_events=0)
    post("/v1/ingest/heartbeat", hb.model_dump_json(), tenant)
    # placeholder annotated snapshot so the live view has a frame
    snap = {"tenant_id": tenant, "camera_id": camera, "mode": mode.value,
            "ts": now.isoformat(), "predicted_count": predicted,
            "image_b64": placeholder_jpeg(f"{tenant} · {camera}", mode.value)}
    post("/v1/ingest/snapshot", json.dumps(snap), tenant)
    print(f"seeded {tenant}: {len(events)} events, snapshot (predicted={predicted})")

print("done — per-vertical demo accounts seeded")
