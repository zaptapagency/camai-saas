"""Seed one demo account with live data across ALL verticals.

Posts realistic events + device heartbeats to the running cloud ingest API so the
dashboard for a single tenant shows every vertical populated at once. This is a
DEMO seeder: the person-based verticals (retail/capacity/queue/staffing) also get
real data from the edge agent running on the sample video; the model-dependent
ones (safety/traffic/fire/thermal/proximity/warehouse/parking) are seeded here
because COCO/YOLO can't produce those classes from the sample clip.

Run (from repo root):
    PYTHONPATH=packages/schema python tools/seed_demo_account.py
"""
from __future__ import annotations

import json
import random
import urllib.request
from datetime import datetime, timedelta, timezone

import sys

from camai_schema import Event, EventBatch, EventType, Heartbeat, Mode, ObjectClass

BASE = "http://localhost:8000"
TENANT = sys.argv[1] if len(sys.argv) > 1 else "demo-tenant"
SITE = f"{TENANT}-site-01"
random.seed(sum(map(ord, TENANT)))  # stable but distinct numbers per tenant

now = datetime.now(timezone.utc)
def ago(sec: float) -> datetime:
    return now - timedelta(seconds=sec)

def post(path: str, body: str) -> int:
    req = urllib.request.Request(
        BASE + path, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Device-Tenant": TENANT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status

events: list[Event] = []
def ev(camera_id: str, type_: EventType, mode: Mode, ts_sec: float, **payload) -> None:
    events.append(Event(tenant_id=TENANT, site_id=SITE, camera_id=camera_id,
                        type=type_, mode=mode, ts=ago(ts_sec), **payload))

# --- RETAIL: entrance line + browse dwell + per-zone occupancy ---------------
for i in range(22):
    ev("cam-entrance", EventType.entry, Mode.retail, 1500 - i*60,
       line_id="door", object_class=ObjectClass.person, track_id=100 + i)
for i in range(16):
    ev("cam-entrance", EventType.exit, Mode.retail, 1400 - i*60,
       line_id="door", object_class=ObjectClass.person, track_id=200 + i)
for i in range(8):
    ev("cam-entrance", EventType.dwell, Mode.retail, 300 - i*20,
       zone_id="aisle-1", dwell_seconds=round(random.uniform(40, 420), 1))
for t in range(0, 240, 20):
    ev("cam-entrance", EventType.occupancy_sample, Mode.retail, t,
       zone_id="aisle-1", count=random.randint(2, 11))

# --- PARKING: per-space states -----------------------------------------------
for s in range(1, 7):
    parked = random.random() < 0.6
    ev("cam-lot", EventType.vehicle_parked if parked else EventType.vehicle_left,
       Mode.parking, 900 - s*30, zone_id=f"space-{s}", object_class=ObjectClass.vehicle)
    ev("cam-lot", EventType.occupancy_sample, Mode.parking, 60,
       zone_id=f"space-{s}", count=1 if parked else 0)

# --- QUEUE: wait-time dwell + live length ------------------------------------
for i in range(10):
    ev("cam-checkout", EventType.dwell, Mode.queue, 260 - i*20,
       zone_id="queue", dwell_seconds=round(random.uniform(25, 240), 1))
for t in range(0, 240, 20):
    ev("cam-checkout", EventType.occupancy_sample, Mode.queue, t,
       zone_id="queue", count=random.randint(1, 7))

# --- STAFFING: station coverage + activity -----------------------------------
stations = [("grill", "active"), ("prep", "static"), ("expo", "active"),
            ("dish", None), ("bar", "static")]
for zid, state in stations:
    head = 0 if state is None else random.randint(1, 2)
    labels = [state] if state else None
    ev("cam-kitchen", EventType.occupancy_sample, Mode.staffing, 30,
       zone_id=zid, count=head, labels=labels)

# --- SAFETY: PPE violations --------------------------------------------------
for i in range(5):
    ev("cam-dock", EventType.ppe_violation, Mode.safety, 700 - i*80,
       zone_id="hazard", count=random.randint(1, 2), track_id=300 + i,
       labels=random.choice([["helmet"], ["vest"], ["helmet", "vest"]]))

# --- TRAFFIC: directional vehicle crossings ----------------------------------
for i in range(18):
    ev("cam-street", EventType.vehicle_crossing, Mode.traffic, 1200 - i*55,
       line_id="cordon", object_class=ObjectClass.vehicle,
       labels=[random.choice(["forward", "reverse"])])

# --- CAPACITY: breaches (Tier 1) ---------------------------------------------
for i in range(4):
    ev("cam-hall", EventType.capacity_breach, Mode.capacity, 600 - i*90,
       zone_id="main-hall", count=random.randint(49, 62), labels=["limit:48"])
ev("cam-hall", EventType.occupancy_sample, Mode.capacity, 30, zone_id="main-hall",
   count=52, labels=["over"])

# --- PROXIMITY: forklift near-miss (Tier 2) ----------------------------------
for i in range(3):
    ev("cam-warehouse", EventType.proximity_alert, Mode.proximity, 500 - i*120,
       count=1, track_id=400 + i, labels=[f"dist:{random.uniform(0.06,0.13):.2f}"])

# --- FIRE: hazard alerts (Tier 3) --------------------------------------------
for i in range(2):
    ev("cam-cookline", EventType.hazard_alert, Mode.fire, 450 - i*150,
       zone_id="cookline", count=random.randint(1, 3),
       labels=random.choice([["smoke"], ["fire", "smoke"]]))

# --- THERMAL: overheat/fever (Tier 4) ----------------------------------------
for i in range(3):
    ev("cam-walkin", EventType.overheat_alert, Mode.thermal, 400 - i*100,
       count=1, track_id=500 + i, labels=[f"{random.uniform(38.5,41.0):.1f}C"])

# --- WAREHOUSE: net count delta ----------------------------------------------
for i in range(6):
    ev("cam-bay", EventType.count_delta, Mode.warehouse, 800 - i*90,
       zone_id="bay-A", delta=random.choice([-2, -1, 1, 1, 2]))

# ---- send events in batches -------------------------------------------------
BATCH = 50
sent = 0
for i in range(0, len(events), BATCH):
    chunk = events[i:i + BATCH]
    batch = EventBatch(device_id="seed-device", tenant_id=TENANT, events=chunk)
    post("/v1/ingest/events", batch.model_dump_json())
    sent += len(chunk)

# ---- device heartbeats so Device Health populates ---------------------------
cams = ["cam-entrance", "cam-lot", "cam-checkout", "cam-kitchen", "cam-dock",
        "cam-street", "cam-hall", "cam-warehouse", "cam-cookline", "cam-walkin", "cam-bay"]
for n, cam in enumerate(cams):
    hb = Heartbeat(device_id=f"edge-{cam}", tenant_id=TENANT,
                   agent_version="0.1.0", uptime_seconds=random.uniform(3600, 90000),
                   cpu_percent=random.uniform(8, 55), gpu_percent=random.uniform(20, 80),
                   gpu_temp_c=random.uniform(45, 68), disk_free_gb=random.uniform(30, 220),
                   stream_fps={cam: round(random.uniform(6, 12), 1)}, queued_events=random.randint(0, 5))
    post("/v1/ingest/heartbeat", hb.model_dump_json())

print(f"seeded {sent} events across all verticals + {len(cams)} device heartbeats "
      f"for tenant '{TENANT}'")
