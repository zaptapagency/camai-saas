"""Score weights on a dataset's val split and compare two scorings.

``evaluate`` imports ``ultralytics`` LAZILY (optional ``[train]`` extra); importing
this module never needs torch. :func:`compare` is pure and always importable.
"""

from __future__ import annotations


def _load_yolo():
    try:
        from ultralytics import YOLO  # lazy: heavy (torch) + optional extra
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "ultralytics is required to evaluate. Install the training extra:\n"
            "    pip install 'camai-training[train]'   (or: pip install ultralytics)"
        ) from exc
    return YOLO


def evaluate(weights: str, data_yaml: str) -> dict:
    """Run detection validation and return the headline mAP metrics.

    Validates on the ``val:`` split declared in ``data_yaml`` and pulls the two
    numbers that matter for a promote decision off the Ultralytics results object:
    ``map50`` (mAP@0.5, the forgiving "did it find the object" number) and ``map``
    (mAP@0.5:0.95, the stricter localization number).
    """
    YOLO = _load_yolo()
    results = YOLO(weights).val(data=data_yaml)
    return {
        "map50": float(results.box.map50),
        "map": float(results.box.map),
    }


def compare(base_metrics: dict, new_metrics: dict) -> dict:
    """Diff two :func:`evaluate` results into a promote decision (pure).

    ``improved`` keys off mAP@0.5 moving in the right direction — a strictly higher
    ``map50`` is the gate the CLI uses to decide whether to promote the new weights.
    """
    base = float(base_metrics.get("map50", 0.0))
    new = float(new_metrics.get("map50", 0.0))
    delta = new - base
    return {
        "map50_delta": delta,
        "map_delta": float(new_metrics.get("map", 0.0)) - float(base_metrics.get("map", 0.0)),
        "improved": delta > 0,
    }


__all__ = ["evaluate", "compare"]
