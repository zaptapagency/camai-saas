"""Detection + tracking.

Wraps Ultralytics YOLOv11 with its built-in tracker (ByteTrack by default,
BoT-SORT optional) so every detection carries a persistent ``track_id`` across
frames. Persistent ids are what make counting, dwell time, and not-double-counting
possible.

The tracker is stateful per stream, so use **one** ``Detector`` instance per
camera and feed it that camera's frames in order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from camai_schema import ObjectClass

# COCO class ids we care about, mapped to our coarse object classes.
# person=0, bicycle=1, car=2, motorcycle=3, bus=5, truck=7.
_COCO_TO_CLASS: dict[int, ObjectClass] = {
    0: ObjectClass.person,
    2: ObjectClass.vehicle,
    3: ObjectClass.vehicle,
    5: ObjectClass.vehicle,
    7: ObjectClass.vehicle,
}
_KEEP_COCO_IDS = sorted(_COCO_TO_CLASS)


@dataclass
class Detection:
    track_id: Optional[int]
    object_class: ObjectClass
    confidence: float
    # Bounding box in pixels.
    x1: float
    y1: float
    x2: float
    y2: float
    # Optional per-detection temperature in Celsius, supplied by a thermal camera
    # (thermal mode). None for ordinary RGB detectors.
    temperature: Optional[float] = None

    @property
    def centroid(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0

    @property
    def foot_point(self) -> tuple[float, float]:
        """Bottom-centre of the box — a better ground position than the centroid
        for people/vehicles, so zone membership matches where the object stands."""
        return (self.x1 + self.x2) / 2.0, self.y2


class Detector:
    def __init__(
        self,
        weights: str = "yolo11n.pt",
        device: str = "auto",
        imgsz: int = 640,
        min_confidence: float = 0.35,
        tracker: str = "bytetrack.yaml",
        class_map: dict[int, str] | None = None,
    ) -> None:
        # Imported lazily so the rest of the package (config, geometry, schema)
        # is importable and unit-testable without the heavy ML stack installed.
        from ultralytics import YOLO

        self._model = YOLO(weights)
        self._device = None if device == "auto" else device
        self._imgsz = imgsz
        self._min_conf = min_confidence
        self._tracker = tracker
        # Default: COCO -> person/vehicle. A custom model (e.g. PPE) supplies its own
        # id -> ObjectClass map via config; we keep only its mapped classes.
        if class_map:
            self._class_map: dict[int, ObjectClass] = {
                int(k): ObjectClass(v) for k, v in class_map.items()
            }
        else:
            self._class_map = dict(_COCO_TO_CLASS)
        self._keep_ids = sorted(self._class_map)

    def track(self, image) -> list[Detection]:
        """Run detection + tracking on one frame; return kept detections."""
        results = self._model.track(
            image,
            persist=True,          # keep tracker state across calls
            tracker=self._tracker,
            classes=self._keep_ids,
            conf=self._min_conf,
            imgsz=self._imgsz,
            device=self._device,
            verbose=False,
        )
        if not results:
            return []
        r = results[0]
        if r.boxes is None or r.boxes.id is None:
            return []

        out: list[Detection] = []
        boxes = r.boxes
        for i in range(len(boxes)):
            model_id = int(boxes.cls[i].item())
            obj_class = self._class_map.get(model_id)
            if obj_class is None:
                continue
            x1, y1, x2, y2 = (float(v) for v in boxes.xyxy[i].tolist())
            out.append(
                Detection(
                    track_id=int(boxes.id[i].item()),
                    object_class=obj_class,
                    confidence=float(boxes.conf[i].item()),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                )
            )
        return out
