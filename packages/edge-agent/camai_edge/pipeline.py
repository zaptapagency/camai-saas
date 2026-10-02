"""Per-camera pipeline: ingest -> detect+track -> count -> enqueue.

One instance runs one camera stream. Multiple cameras run as multiple pipelines
(one thread each) sharing a single event queue and cloud-sync worker.
"""

from __future__ import annotations

import threading
import time

from camai_edge.capture import FrameCapture
from camai_edge.config import CameraConfig, DetectorConfig
from camai_edge.counting import make_counter
from camai_edge.detect import Detector
from camai_edge.events import EventQueue
from camai_edge.ingest import frames
from camai_edge.snapshots import SnapshotUploader
from camai_edge.sync import CloudSync


class CameraPipeline:
    def __init__(
        self,
        tenant_id: str,
        site_id: str,
        camera: CameraConfig,
        detector_cfg: DetectorConfig,
        queue: EventQueue,
        sync: CloudSync | None = None,
        *,
        show: bool = False,
        loop: bool = False,
        ingest_url: str = "",
    ) -> None:
        self.camera = camera
        self._queue = queue
        self._sync = sync
        self._show = show
        self._loop = loop
        self._stop = threading.Event()

        # Opt-in annotated-snapshot uploader. Own instance per pipeline ⇒ its
        # per-camera last-sent state is isolated across the camera threads. A no-op
        # when ingest_url is empty or the camera leaves snapshot_seconds at 0.
        self._snapshots = SnapshotUploader(ingest_url, tenant_id, site_id)

        # Opt-in retrain capture: write raw frames + detection sidecars locally, at
        # the snapshot cadence, for building a fine-tune dataset. Own instance per
        # pipeline ⇒ per-camera isolation. No-op unless capture_dir is set; data
        # never leaves the box.
        self._capture = FrameCapture(camera.capture_dir)
        # Epoch seconds of the last captured frame, so capture follows the snapshot
        # cadence (snapshot_seconds) independently of whether the cloud uploader is
        # enabled — an offline box can still build a dataset.
        self._last_capture_ts: float | None = None

        self._detector = Detector(
            weights=detector_cfg.weights,
            device=detector_cfg.device,
            imgsz=detector_cfg.imgsz,
            min_confidence=camera.min_confidence,
            class_map=detector_cfg.classes,
        )
        self._counter = make_counter(tenant_id, site_id, camera)

        # rolling FPS estimate for heartbeats/health
        self._fps = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        cam = self.camera
        print(f"[{cam.id}] starting mode={cam.mode} source={cam.source}")
        last = time.time()

        for frame in frames(cam.source, cam.target_fps, loop=self._loop):
            if self._stop.is_set():
                break

            detections = self._detector.track(frame.image)
            h, w = frame.image.shape[:2]
            events = self._counter.update(detections, (w, h), frame.ts)

            for ev in events:
                self._queue.put(ev)
                print(f"[{cam.id}] {ev.type} "
                      f"{ev.zone_id or ev.line_id or ''} "
                      f"{'count=' + str(ev.count) if ev.count is not None else ''}"
                      f"{'delta=' + str(ev.delta) if ev.delta is not None else ''}".strip())

            # rolling FPS (exponential moving average)
            now = time.time()
            inst = 1.0 / max(now - last, 1e-6)
            self._fps = 0.9 * self._fps + 0.1 * inst if self._fps else inst
            last = now
            if self._sync is not None:
                self._sync.stream_fps[cam.id] = round(self._fps, 1)

            # Opt-in accuracy flywheel: every snapshot_seconds, upload one drawn
            # frame. predicted_count is this frame's detection count (mode-agnostic
            # and robust); the uploader gates on the interval and is best-effort.
            if cam.snapshot_seconds > 0 and self._snapshots.enabled:
                self._snapshots.maybe_upload(
                    cam.id, cam.mode.value, frame.image, (w, h),
                    len(detections), self._counter, frame.ts, cam.snapshot_seconds,
                )

            # Opt-in retrain capture: at the SAME cadence/gate as the snapshot
            # upload, also persist the RAW (un-annotated) frame + its detections
            # locally for a fine-tune dataset. Uses frame.image (never the annotated
            # copy). Best-effort; no-op when capture_dir is "".
            if cam.snapshot_seconds > 0 and self._capture.enabled:
                cap_last = self._last_capture_ts
                if cap_last is None or (frame.ts - cap_last) >= cam.snapshot_seconds:
                    self._last_capture_ts = frame.ts
                    self._capture.write(
                        cam.id, cam.mode.value, frame.image, (w, h),
                        detections, frame.ts,
                    )

            if self._show:
                self._render(frame.image, detections, (w, h))

        print(f"[{cam.id}] stopped")

    def _render(self, image, detections, frame_size):  # pragma: no cover
        import cv2

        for d in detections:
            cv2.rectangle(image, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)),
                          (255, 180, 0), 1)
            if d.track_id is not None:
                cv2.putText(image, f"{d.object_class.value}#{d.track_id}",
                            (int(d.x1), int(d.y1) - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 180, 0), 1)
        self._counter.draw(image, frame_size)
        cv2.putText(image, f"{self._fps:.1f} fps", (10, frame_size[1] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.imshow(f"CamAI [{self.camera.id}]", image)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            self._stop.set()
            cv2.destroyAllWindows()
