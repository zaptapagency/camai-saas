"""Outbound-only sync to the cloud ingest API.

Runs in a background thread so inference is never blocked on the network. Drains
the local queue in batches, and only acks (deletes) events the cloud confirmed.
The connection is outbound-only and mTLS-authenticated with the per-device
certificate — no inbound ports are opened on the customer network.

If no ``ingest_url`` is configured the agent runs fully offline and events simply
accumulate in (and can be inspected from) the local queue.
"""

from __future__ import annotations

import threading
import time

import httpx

from camai_schema import EventBatch, Heartbeat, IngestAck

from camai_edge.config import CloudConfig
from camai_edge.events import EventQueue


class CloudSync:
    def __init__(
        self,
        cloud: CloudConfig,
        queue: EventQueue,
        *,
        device_id: str,
        tenant_id: str,
        agent_version: str,
    ) -> None:
        self._cloud = cloud
        self._queue = queue
        self._device_id = device_id
        self._tenant_id = tenant_id
        self._agent_version = agent_version
        self._started = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = self._build_client()
        # Latest measured FPS per camera, updated by the pipeline for heartbeats.
        self.stream_fps: dict[str, float] = {}

    def _build_client(self) -> httpx.Client | None:
        if not self._cloud.ingest_url:
            return None
        cert = None
        if self._cloud.device_cert and self._cloud.device_key:
            cert = (self._cloud.device_cert, self._cloud.device_key)
        verify = self._cloud.ca_bundle or True
        return httpx.Client(
            base_url=self._cloud.ingest_url.rstrip("/"),
            cert=cert,
            verify=verify,
            timeout=10.0,
        )

    def start(self) -> None:
        if self._client is None:
            return  # offline mode
        self._thread = threading.Thread(target=self._run, name="cloud-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._client:
            self._client.close()

    def _run(self) -> None:
        last_heartbeat = 0.0
        while not self._stop.is_set():
            try:
                self._flush_once()
                now = time.time()
                if now - last_heartbeat >= self._cloud.heartbeat_interval_seconds:
                    self._send_heartbeat()
                    last_heartbeat = now
            except Exception as exc:  # never let the sync thread die
                print(f"[sync] transient error, will retry: {exc}")
            self._stop.wait(self._cloud.flush_interval_seconds)

    def _flush_once(self) -> None:
        assert self._client is not None
        events = self._queue.peek(limit=500)
        if not events:
            return
        batch = EventBatch(
            device_id=self._device_id, tenant_id=self._tenant_id, events=events
        )
        resp = self._client.post("/v1/ingest/events", content=batch.model_dump_json(),
                                 headers={"content-type": "application/json"})
        resp.raise_for_status()
        ack = IngestAck.model_validate(resp.json())
        # Accepted + duplicates are both safe to drop from the local queue.
        if ack.accepted or ack.duplicates:
            self._queue.ack([e.event_id for e in events])

    def _send_heartbeat(self) -> None:
        assert self._client is not None
        hb = Heartbeat(
            device_id=self._device_id,
            tenant_id=self._tenant_id,
            agent_version=self._agent_version,
            uptime_seconds=time.time() - self._started,
            stream_fps=dict(self.stream_fps),
            queued_events=self._queue.depth(),
        )
        resp = self._client.post("/v1/ingest/heartbeat", content=hb.model_dump_json(),
                                 headers={"content-type": "application/json"})
        resp.raise_for_status()
