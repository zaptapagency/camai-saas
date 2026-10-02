"""Stage a fine-tuned model for the fleet to pick up. PURE (stdlib only).

Promotion here is deliberately a *staging* step, not a deploy. Edge boxes are
outbound-only and never accept a push (see ``camai_cloud.fleet`` /
``packages/cloud/app/fleet.py``): the cloud holds each device's
``DesiredRelease`` and the edge *pulls* the model version it is pinned to. So the
right way to ship a new model is:

    1. (here) copy ``best.pt`` to a well-known staged name/location,
    2. upload it to the model store under a version id (e.g. ``yolo11n-retail-v4``),
    3. set that id as ``DesiredRelease.model_version`` for a CANARY device set via
       the cloud fleet API, watch accuracy, then widen to stable.

This module does ONLY step 1 and documents the rest. It never calls the cloud —
keeping it pure and side-effect-contained (a filesystem copy) so the dangerous bit
(actually re-pinning live devices) stays an explicit, human-gated fleet action.
"""

from __future__ import annotations

import os
import shutil


STAGED_NAME = "yolo-finetuned.pt"


def promote(new_weights: str, dest_dir: str) -> str:
    """Copy ``new_weights`` (a ``best.pt``) into ``dest_dir`` as the staged model.

    Returns the staged path. Prints the deployment note so whoever runs the
    pipeline knows the model is staged, NOT yet live, and what the fleet step is.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, STAGED_NAME)
    shutil.copyfile(new_weights, dest)
    print(
        "[promote] staged fine-tuned model at:\n"
        f"    {os.path.abspath(dest)}\n"
        "[promote] NOT deployed yet. Deployment is via the cloud fleet release-pinning:\n"
        "    upload this file to the model store under a version id, then set it as\n"
        "    DesiredRelease.model_version for a CANARY device set (see\n"
        "    packages/cloud/app/fleet.py: resolve_release / select_canary), watch\n"
        "    accuracy, then widen to stable."
    )
    return dest


__all__ = ["promote", "STAGED_NAME"]
