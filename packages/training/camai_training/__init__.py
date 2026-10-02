"""CamAI retrain pipeline — the model-improvement half of the accuracy flywheel.

The edge captures raw frames + detection sidecars (see
``camai_edge.capture``); this package turns them into a YOLO fine-tune dataset,
fine-tunes per-site weights, scores before/after, and promotes a build that is
actually better.

Import surface is deliberately layered:

* :mod:`camai_training.dataset` is PURE (stdlib only) — building a dataset and the
  count-gate reasoning never import torch/ultralytics, so they run anywhere.
* :mod:`camai_training.retrain` / :mod:`camai_training.evaluate` import
  ``ultralytics`` LAZILY (optional ``[train]`` extra); importing this package does
  not require the ML stack.
"""

__version__ = "0.1.0"
