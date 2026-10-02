"""Opt-in annotated-snapshot uploader (the accuracy flywheel).

When an operator sets ``CameraConfig.snapshot_seconds > 0``, each camera
periodically sends a single *drawn* frame — the raw image with the counter's
detection/zone overlay burned on — to the cloud for a live-view tile and for
human labelling. It is **off by default** (snapshot_seconds == 0): no imagery
leaves the site unless the operator turns it on.

Design notes:
* Outbound-only, best-effort. Every failure (encode, draw, network) is swallowed
  and logged; nothing here ever raises back into the inference loop.
* No new dependencies: JPEG encoding uses the already-present OpenCV (imported
  lazily so importing this module stays cv2-free and unit-testable), and the POST
  uses :mod:`urllib.request` from the stdlib.
* The overlay is drawn onto a *copy* of the frame so the live pipeline's image is
  never mutated.

Cloud contract (built in parallel): ``POST {ingest_url}/v1/ingest/snapshot`` with
JSON body ``{tenant_id, camera_id, mode, ts, predicted_count, image_b64}`` where
``ts`` is ISO-8601 UTC and ``image_b64`` is base64 of a JPEG. The site is derived
cloud-side from the authenticated device, so it is not part of the body.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from datetime import datetime, timezone

_SNAPSHOT_PATH = "/v1/ingest/snapshot"
_TIMEOUT_SECONDS = 5.0


def _payload(
    tenant_id: str,
    camera_id: str,
    mode: str,
    predicted_count: int,
    ts: str,
    image_b64: str,
) -> dict:
    """Build the exact JSON body the cloud ingest endpoint expects.

    Pure and cv2-free so it can be unit-tested without the ML/vision stack. ``ts``
    is already an ISO-8601 UTC string and ``image_b64`` an already-encoded JPEG.
    """
    return {
        "tenant_id": tenant_id,
        "camera_id": camera_id,
        "mode": mode,
        "ts": ts,
        "predicted_count": predicted_count,
        "image_b64": image_b64,
    }


class SnapshotUploader:
    """Periodically POSTs an annotated frame per camera to the cloud.

    One instance is created per camera pipeline, so per-camera state (the last-sent
    timestamp) is naturally isolated across the multi-camera threads. Interval
    gating lives here via :meth:`maybe_upload`; :meth:`upload` always sends.
    """

    def __init__(self, ingest_url: str, tenant_id: str, site_id: str) -> None:
        # Empty ingest_url => disabled (offline site): every method is a no-op.
        self._ingest_url = (ingest_url or "").rstrip("/")
        self._tenant_id = tenant_id
        self._site_id = site_id
        # camera_id -> epoch seconds of the last attempted send.
        self._last_sent: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._ingest_url)

    def maybe_upload(
        self,
        camera_id: str,
        mode: str,
        image,
        frame_size: tuple[int, int],
        predicted_count: int,
        counter,
        ts: float,
        interval: float,
    ) -> bool:
        """Send at most once per ``interval`` seconds per camera.

        ``ts`` is epoch seconds (the frame's capture time). Returns True only when a
        POST was attempted and the cloud accepted it. No-op (returns False) when
        disabled or when the interval has not elapsed. The last-sent clock is
        advanced on the *decision* to send, not on success, so a slow or failing
        endpoint can't make us try every frame.
        """
        if not self.enabled or interval <= 0:
            return False
        last = self._last_sent.get(camera_id)
        if last is not None and (ts - last) < interval:
            return False
        self._last_sent[camera_id] = ts
        return self.upload(camera_id, mode, image, frame_size, predicted_count, counter, ts)

    def upload(
        self,
        camera_id: str,
        mode: str,
        image,
        frame_size: tuple[int, int],
        predicted_count: int,
        counter,
        ts: float,
    ) -> bool:
        """Draw overlays, encode a JPEG, and POST it. Best-effort; never raises.

        ``ts`` is epoch seconds and is converted here to ISO-8601 UTC for the body.
        """
        if not self.enabled:
            return False
        try:
            image_b64 = self._encode(image, frame_size, counter)
        except Exception as exc:  # pragma: no cover - needs cv2
            print(f"[snapshot] encode failed cam={camera_id} site={self._site_id}: {exc}")
            return False
        if image_b64 is None:
            return False
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        payload = _payload(self._tenant_id, camera_id, mode, predicted_count, ts_iso, image_b64)
        return self._post(payload, camera_id)

    def _encode(self, image, frame_size: tuple[int, int], counter) -> str | None:  # pragma: no cover - needs cv2
        """Burn the counter overlay onto a copy of the frame and JPEG+base64 it."""
        import cv2  # lazy: keeps this module importable (and testable) without cv2

        annotated = image.copy()
        try:
            counter.draw(annotated, frame_size)
        except Exception as exc:
            # A broken overlay must not cost us the frame — send the raw copy.
            print(f"[snapshot] overlay draw failed: {exc}")
        ok, buf = cv2.imencode(".jpg", annotated)
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("ascii")

    def _post(self, payload: dict, camera_id: str) -> bool:
        """POST the JSON body; swallow and log any network error."""
        url = self._ingest_url + _SNAPSHOT_PATH
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                resp.read()
            return True
        except Exception as exc:  # pragma: no cover - needs a live endpoint
            print(f"[snapshot] upload failed cam={camera_id} site={self._site_id}: {exc}")
            return False
