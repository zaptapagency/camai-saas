"""Fine-tune a YOLO model on a CamAI dataset. Needs the ``[train]`` extra.

``ultralytics`` is imported LAZILY inside :func:`fine_tune` so this module (and the
whole package) imports fine without torch installed — only actually running a
fine-tune pulls in the ML stack.
"""

from __future__ import annotations

import os


def _load_yolo():
    """Import ``ultralytics.YOLO`` with a clear install hint on failure."""
    try:
        from ultralytics import YOLO  # lazy: heavy (torch) + optional extra
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "ultralytics is required to fine-tune. Install the training extra:\n"
            "    pip install 'camai-training[train]'   (or: pip install ultralytics)"
        ) from exc
    return YOLO


def fine_tune(
    base_weights: str,
    data_yaml: str,
    out_dir: str,
    *,
    epochs: int = 1,
    imgsz: int = 640,
    device: str = "cpu",
) -> str:
    """Fine-tune ``base_weights`` on the dataset described by ``data_yaml``.

    Trains into ``<out_dir>/finetune/`` (``exist_ok=True`` so re-runs overwrite that
    run rather than spawning ``finetune2``) and returns the path to the produced
    ``best.pt``. The default ``device="cpu"`` keeps a smoke-run possible on a box
    without a GPU; pass ``0`` / ``"cuda"`` on a real training host.
    """
    YOLO = _load_yolo()
    model = YOLO(base_weights)
    model.train(
        data=data_yaml,
        epochs=epochs,
        imgsz=imgsz,
        device=device,
        project=out_dir,
        name="finetune",
        exist_ok=True,
    )
    # Ultralytics decides the real save_dir (it may route a relative `project` under
    # its own runs/ root), so trust the trainer's own paths rather than assuming one.
    trainer = getattr(model, "trainer", None)
    best = getattr(trainer, "best", None)
    if best and os.path.exists(str(best)):
        return str(best)
    save_dir = getattr(trainer, "save_dir", None)
    if save_dir:
        cand = os.path.join(str(save_dir), "weights", "best.pt")
        if os.path.exists(cand):
            return cand
    # Fallback to the conventional location.
    return os.path.join(out_dir, "finetune", "weights", "best.pt")


__all__ = ["fine_tune"]
