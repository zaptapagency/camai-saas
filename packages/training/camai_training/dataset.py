"""Build a YOLO fine-tune dataset from the edge capture directory. PURE.

This module is stdlib-only (``json`` + ``shutil`` + ``hashlib`` + ``os``). It never
imports torch, ultralytics or cv2 — the images are *copied*, not decoded — so
building a dataset and the count-gate reasoning run in CI under the plain test
venv, no ML stack required. The heavy fine-tune/eval lives in
:mod:`camai_training.retrain` / :mod:`camai_training.evaluate` behind lazy imports.

What it reads
-------------
The edge writes (see ``camai_edge.capture``)::

    <capture_root>/<camera_id>/<ts_ms>.jpg     # raw frame
    <capture_root>/<camera_id>/<ts_ms>.json    # sidecar

where the sidecar is::

    {"camera_id","mode","ts","frame_size":[w,h],"predicted_count":int,
     "boxes":[{"cls_name":str,"conf":float,"xyxy":[x1,y1,x2,y2]}]}

What it writes
--------------
A standard Ultralytics dataset under ``out_dir``::

    images/train/<...>.jpg   labels/train/<...>.txt
    images/val/<...>.jpg     labels/val/<...>.txt
    data.yaml                # train/val paths, nc, names

Each label line is ``cls cx cy w h`` with the box center/size normalized by the
frame size — the exact format ``YOLO.train`` expects.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Iterable, Optional


# YOLO class ids are positional: the index in this list IS the class id written to
# the label files and declared under ``names`` in data.yaml. Keep it stable —
# reordering would silently relabel every previously built dataset.
CLASS_NAMES = ["person", "vehicle", "forklift", "pallet", "fire", "smoke"]

# Sidecar ``cls_name`` -> YOLO class id. Only these canonical names train; any other
# ``cls_name`` (e.g. a COCO subtype like "car", a PPE item, or "other") is skipped
# rather than guessed at, so a stray class can never corrupt the label space.
NAME_TO_ID: dict[str, int] = {name: i for i, name in enumerate(CLASS_NAMES)}


def _label_lines(boxes: Iterable[dict], frame_w: float, frame_h: float, min_conf: float) -> list[str]:
    """Convert sidecar boxes into normalized YOLO label lines.

    Skips boxes below ``min_conf``, boxes whose ``cls_name`` is not a trainable
    class, and degenerate frames (non-positive width/height). Coordinates are
    clamped into [0, 1] so a detection that spilled a pixel past the frame edge
    still produces a valid label.
    """
    if frame_w <= 0 or frame_h <= 0:
        return []
    lines: list[str] = []
    for box in boxes:
        cls_name = box.get("cls_name")
        cls_id = NAME_TO_ID.get(cls_name)
        if cls_id is None:
            continue
        if float(box.get("conf", 0.0)) < min_conf:
            continue
        xyxy = box.get("xyxy") or []
        if len(xyxy) != 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in xyxy)
        # Normalize to center-x, center-y, width, height in [0, 1].
        cx = ((x1 + x2) / 2.0) / frame_w
        cy = ((y1 + y2) / 2.0) / frame_h
        bw = abs(x2 - x1) / frame_w
        bh = abs(y2 - y1) / frame_h
        if bw <= 0 or bh <= 0:
            continue
        cx = min(1.0, max(0.0, cx))
        cy = min(1.0, max(0.0, cy))
        bw = min(1.0, max(0.0, bw))
        bh = min(1.0, max(0.0, bh))
        lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return lines


def _is_val(key: str, val_frac: float) -> bool:
    """Deterministically route a frame into the val split by a hash of its key.

    Uses a content hash (not Python's salted ``hash()``) so the train/val split is
    identical across processes and runs — a rebuilt dataset must partition the same
    frames the same way, or a before/after eval would compare across shifting sets.
    """
    if val_frac <= 0:
        return False
    if val_frac >= 1:
        return True
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") / float(1 << 32)
    return bucket < val_frac


def build_dataset(
    capture_root: str,
    out_dir: str,
    *,
    include_cameras: Optional[Iterable[str]] = None,
    min_conf: float = 0.35,
    val_frac: float = 0.2,
) -> dict:
    """Turn a capture root into a YOLO dataset under ``out_dir``.

    For every ``<ts>.json`` + ``<ts>.jpg`` pair under each camera directory, the
    sidecar boxes with ``conf >= min_conf`` and a known class become a YOLO label
    file and the frame is copied into the images tree. Frames with no usable box
    are skipped entirely (an empty label file is a "no objects" negative we don't
    want to teach from here). The train/val split is deterministic (see
    :func:`_is_val`). Writes ``out_dir/data.yaml`` and returns a summary dict::

        {"images": N, "train": n, "val": m, "cameras": [...],
         "classes": CLASS_NAMES, "data_yaml": <path>}
    """
    include = set(include_cameras) if include_cameras is not None else None

    img_train = os.path.join(out_dir, "images", "train")
    img_val = os.path.join(out_dir, "images", "val")
    lbl_train = os.path.join(out_dir, "labels", "train")
    lbl_val = os.path.join(out_dir, "labels", "val")
    for d in (img_train, img_val, lbl_train, lbl_val):
        os.makedirs(d, exist_ok=True)

    cameras_used: set[str] = set()
    n_train = 0
    n_val = 0

    camera_dirs = []
    if os.path.isdir(capture_root):
        for name in sorted(os.listdir(capture_root)):
            path = os.path.join(capture_root, name)
            if not os.path.isdir(path):
                continue
            if include is not None and name not in include:
                continue
            camera_dirs.append((name, path))

    for camera_id, cam_path in camera_dirs:
        for fname in sorted(os.listdir(cam_path)):
            if not fname.endswith(".json"):
                continue
            stem = fname[: -len(".json")]
            json_path = os.path.join(cam_path, fname)
            jpg_path = os.path.join(cam_path, stem + ".jpg")
            if not os.path.isfile(jpg_path):
                continue  # no matching frame -> nothing to copy

            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    sidecar = json.load(fh)
            except (OSError, ValueError):
                continue

            frame_size = sidecar.get("frame_size") or [0, 0]
            frame_w = float(frame_size[0]) if len(frame_size) == 2 else 0.0
            frame_h = float(frame_size[1]) if len(frame_size) == 2 else 0.0
            lines = _label_lines(sidecar.get("boxes") or [], frame_w, frame_h, min_conf)
            if not lines:
                continue  # skip frames with no usable boxes

            # Namespace the stem by camera so two cameras' identical ts_ms never collide.
            key = f"{camera_id}/{stem}"
            dst_stem = f"{camera_id}_{stem}"
            if _is_val(key, val_frac):
                img_dir, lbl_dir = img_val, lbl_val
                n_val += 1
            else:
                img_dir, lbl_dir = img_train, lbl_train
                n_train += 1

            shutil.copyfile(jpg_path, os.path.join(img_dir, dst_stem + ".jpg"))
            with open(os.path.join(lbl_dir, dst_stem + ".txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            cameras_used.add(camera_id)

    data_yaml = os.path.join(out_dir, "data.yaml")
    _write_data_yaml(data_yaml, out_dir)

    return {
        "images": n_train + n_val,
        "train": n_train,
        "val": n_val,
        "cameras": sorted(cameras_used),
        "classes": list(CLASS_NAMES),
        "data_yaml": data_yaml,
    }


def _write_data_yaml(path: str, out_dir: str) -> None:
    """Write the Ultralytics ``data.yaml`` by hand (no PyYAML dependency).

    ``path`` is the dataset root so train/val resolve relatively; emitting the
    class ``names`` as a YAML list keeps us byte-for-byte compatible with what
    ``YOLO.train(data=...)`` parses, without importing yaml in this pure module.
    """
    names = "\n".join(f"  {i}: {name}" for i, name in enumerate(CLASS_NAMES))
    content = (
        f"# CamAI fine-tune dataset (generated by camai_training.dataset)\n"
        f"path: {os.path.abspath(out_dir)}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: {len(CLASS_NAMES)}\n"
        f"names:\n{names}\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def trusted_cameras_from_accuracy(accuracy_json: dict, max_pct_error: float = 20.0) -> Optional[set]:
    """The COUNT-GATE: which cameras are trusted enough to self-train from.

    The accuracy flywheel only works if we fine-tune on frames the model *already*
    gets right — the low-error frames become free training labels, while the
    high-error frames are what you route to a human for manual labelling instead of
    letting the model teach itself its own mistakes (confirmation bias in, garbage
    out).

    The honest limitation: the cloud accuracy endpoint reports error *per mode*
    (retail/parking/warehouse/...), not per *camera*. A camera id cannot be mapped
    to a mode from this payload alone, so there is no sound way to turn a per-mode
    trust signal into a per-camera allow-list here. Rather than fake it, this helper
    returns:

    * ``None`` -> "cannot map; include ALL cameras" (the safe default the CLI uses
      when the mapping is ambiguous), whenever the payload is missing/empty, has no
      ``per_mode`` breakdown, or *every* mode is within ``max_pct_error`` (nothing to
      exclude anyway).
    * ``set()`` -> an explicit empty set, meaning "trust nothing", only when EVERY
      mode exceeds ``max_pct_error``. That is the count-gate firing: the model is not
      yet trustworthy anywhere, so none of its detections should become labels.

    The count-gate concept lives here; wiring per-camera modes (which would let this
    return a precise set) belongs with a camera->mode map the cloud would have to
    supply, and is intentionally left as the documented next step.
    """
    if not accuracy_json:
        return None
    per_mode = accuracy_json.get("per_mode")
    if not per_mode:
        return None

    bad = [m for m in per_mode if float(m.get("mean_pct_error", 0.0)) > max_pct_error]
    if not bad:
        # Everything is within tolerance -> nothing to gate out -> include all.
        return None
    if len(bad) == len(per_mode):
        # Every mode is over the error bar: the count-gate fires, trust nothing.
        return set()
    # Mixed: some modes are trustworthy, but we can't attribute a mode to a camera
    # from this payload, so we cannot safely exclude. Document by returning None
    # (include all); a camera->mode map is the upgrade path.
    return None


__all__ = [
    "CLASS_NAMES",
    "NAME_TO_ID",
    "build_dataset",
    "trusted_cameras_from_accuracy",
]
