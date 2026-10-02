"""Opt-in raw-frame + detection capture (the retrain dataset source).

When an operator sets ``CameraConfig.capture_dir`` *and* ``snapshot_seconds > 0``,
each camera periodically writes — at the same cadence as the annotated-snapshot
uploader — the **raw** (un-annotated) frame plus a JSON sidecar of its detections
to a local directory. A later dataset builder turns these pairs into YOLO training
data to fine-tune per-site weights (the data source for the retrain step).

Design notes:
* **Local only.** Nothing here touches the network; the data stays on the edge box
  (training happens where the data is, or it is exported deliberately).
* **Off by default.** No-op unless ``capture_dir`` is set (and the caller gates on
  ``snapshot_seconds`` at the snapshot cadence).
* **Best-effort.** Every filesystem/encode failure is swallowed and logged; nothing
  here ever raises back into the inference loop.
* **cv2-free surface.** :func:`build_sidecar` is pure (no cv2) so the sidecar schema
  is unit-testable without the vision stack. Only :meth:`FrameCapture.write`'s JPEG
  encode imports cv2, and it does so lazily.

On-disk layout (what a dataset builder reads)::

    <capture_dir>/<camera_id>/<ts_ms>.jpg    # the raw frame (JPEG)
    <capture_dir>/<camera_id>/<ts_ms>.json   # its sidecar (see build_sidecar)

where ``<ts_ms>`` is the frame's capture time in integer epoch milliseconds, so the
image and its sidecar share a stem and sort chronologically.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _cls_name(object_class) -> str:
    """The ObjectClass *value* as a string (``"person"``, not ``"ObjectClass.person"``).

    Accepts either an ObjectClass enum member or a plain string, so the sidecar is
    robust to either form of ``Detection.object_class``.
    """
    return str(getattr(object_class, "value", object_class))


def build_sidecar(camera_id, mode, ts, frame_size, detections) -> dict:
    """Build the JSON sidecar describing one raw frame's detections.

    Pure and cv2-free. ``ts`` is epoch seconds (the frame's capture time) and is
    rendered to an ISO-8601 UTC string. ``frame_size`` is ``(width, height)`` in
    pixels. ``detections`` is a list of :class:`camai_edge.detect.Detection`.
    """
    w, h = frame_size
    return {
        "camera_id": camera_id,
        "mode": mode,
        "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "frame_size": [w, h],
        "predicted_count": int(len(detections)),
        "boxes": [
            {
                "cls_name": _cls_name(d.object_class),
                "conf": float(d.confidence),
                "xyxy": [d.x1, d.y1, d.x2, d.y2],
            }
            for d in detections
        ],
    }


class FrameCapture:
    """Writes raw frames + detection sidecars under ``<root_dir>/<camera_id>/``.

    One instance is created per camera pipeline, so writes are naturally isolated
    across the multi-camera threads. No interval gating lives here — the caller
    already gates on the snapshot cadence. A falsy ``root_dir`` disables it: every
    :meth:`write` is a no-op.
    """

    def __init__(self, root_dir: str) -> None:
        # Empty/falsy root_dir => disabled: write() is a no-op.
        self._root_dir = root_dir or ""

    @property
    def enabled(self) -> bool:
        return bool(self._root_dir)

    def write(self, camera_id, mode, image, frame_size, detections, ts) -> bool:
        """Persist the raw frame + its sidecar. Best-effort; never raises.

        Returns True when both files were written, False when disabled or on any
        error. ``ts`` is epoch seconds; the file stem is integer epoch milliseconds.
        """
        if not self.enabled:
            return False
        try:
            import cv2  # lazy: keeps this module importable (and testable) without cv2

            cam_dir = os.path.join(self._root_dir, camera_id)
            os.makedirs(cam_dir, exist_ok=True)

            ts_ms = int(ts * 1000)
            stem = os.path.join(cam_dir, str(ts_ms))

            ok, buf = cv2.imencode(".jpg", image)
            if not ok:
                return False
            with open(stem + ".jpg", "wb") as fh:
                fh.write(buf.tobytes())

            sidecar = build_sidecar(camera_id, mode, ts, frame_size, detections)
            with open(stem + ".json", "w", encoding="utf-8") as fh:
                json.dump(sidecar, fh)
            return True
        except Exception as exc:  # pragma: no cover - needs cv2 / disk
            print(f"[capture] write failed cam={camera_id}: {exc}")
            return False
