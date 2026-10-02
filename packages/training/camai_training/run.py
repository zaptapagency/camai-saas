"""CLI that orchestrates the CamAI retrain pipeline end to end.

    fetch accuracy (optional count-gate) -> build_dataset -> fine_tune
        -> evaluate base vs new -> compare -> promote if improved

The dataset build and the ``--no-train`` plan path are PURE (stdlib only); the
ultralytics-backed steps import lazily, so ``--no-train`` runs with nothing but the
schema installed. Run from the repo root, e.g.::

    camai-retrain --capture-root /data/capture --no-train
    camai-retrain --capture-root /data/capture --tenant acme --cloud-url http://localhost:8000
    camai-retrain --capture-root /data/capture --epochs 3 --device 0
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from camai_training import dataset as dataset_mod


def _fetch_accuracy(cloud_url: str, tenant: str, *, timeout: float = 10.0) -> dict | None:
    """GET the cloud accuracy report for ``tenant`` (the count-gate signal).

    Best-effort: a failure here must not sink a training run (training on all
    cameras is the safe fallback), so any error is reported and swallowed.
    """
    url = f"{cloud_url.rstrip('/')}/v1/tenants/{tenant}/accuracy"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - operator-supplied URL
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[retrain] could not fetch accuracy from {url}: {exc}", file=sys.stderr)
        return None


def _print_count_gate(accuracy: dict, trusted: set | None) -> None:
    """Explain what the accuracy report means for trusting the captured labels."""
    overall = accuracy.get("overall") or {}
    print("[retrain] count-gate (cloud accuracy):")
    print(
        f"    overall: n={overall.get('n')} mae={overall.get('mae')} "
        f"mean_pct_error={overall.get('mean_pct_error')}"
    )
    for m in accuracy.get("per_mode") or []:
        print(
            f"    mode={m.get('mode')}: n={m.get('n')} mae={m.get('mae')} "
            f"mean_pct_error={m.get('mean_pct_error')}"
        )
    if trusted is None:
        print("    => trusted cameras: ALL (per-mode signal can't be mapped to cameras)")
    elif not trusted:
        print("    => trusted cameras: NONE — count-gate fired; route frames to manual labelling")
    else:
        print(f"    => trusted cameras: {sorted(trusted)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="camai-retrain",
        description="CamAI retrain pipeline: capture -> dataset -> fine-tune -> eval -> promote.",
    )
    p.add_argument("--capture-root", required=True, help="Edge capture dir: <root>/<camera_id>/<ts_ms>.{jpg,json}")
    p.add_argument("--base-weights", default="yolo11n.pt", help="Weights to fine-tune from (default: yolo11n.pt)")
    p.add_argument("--out", default="./retrain_out", help="Output dir for dataset + runs (default: ./retrain_out)")
    p.add_argument("--epochs", type=int, default=1, help="Fine-tune epochs (default: 1)")
    p.add_argument("--imgsz", type=int, default=640, help="Training image size (default: 640)")
    p.add_argument("--device", default="cpu", help="Torch device: cpu | 0 | cuda (default: cpu)")
    p.add_argument("--min-conf", type=float, default=0.35, help="Min detection conf to keep a box (default: 0.35)")
    p.add_argument("--val-frac", type=float, default=0.2, help="Fraction of frames held out for val (default: 0.2)")
    p.add_argument("--tenant", default=None, help="Tenant id for the cloud count-gate (needs --cloud-url)")
    p.add_argument("--cloud-url", default=None, help="Cloud base URL, e.g. http://localhost:8000 (count-gate)")
    p.add_argument("--max-pct-error", type=float, default=20.0, help="Count-gate per-mode error bar (default: 20%%)")
    p.add_argument("--promote-dir", default=None, help="Dir to stage the promoted model into (default: <out>/promoted)")
    p.add_argument("--no-train", action="store_true", help="Build dataset + print the plan only; skip fine-tuning")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import os

    # 1) Optional count-gate from the cloud accuracy report.
    include_cameras = None
    if args.cloud_url and args.tenant:
        accuracy = _fetch_accuracy(args.cloud_url, args.tenant)
        if accuracy is not None:
            trusted = dataset_mod.trusted_cameras_from_accuracy(accuracy, max_pct_error=args.max_pct_error)
            _print_count_gate(accuracy, trusted)
            # None => include all; empty set => trust nothing (filter to no cameras).
            if trusted is not None:
                include_cameras = trusted
    elif args.cloud_url or args.tenant:
        print("[retrain] --tenant and --cloud-url must be given together for the count-gate", file=sys.stderr)

    # 2) Build the YOLO dataset (pure).
    summary = dataset_mod.build_dataset(
        args.capture_root,
        args.out,
        include_cameras=include_cameras,
        min_conf=args.min_conf,
        val_frac=args.val_frac,
    )
    print(
        f"[retrain] dataset: {summary['images']} images "
        f"(train={summary['train']}, val={summary['val']}) "
        f"from cameras {summary['cameras']}"
    )
    print(f"[retrain] data.yaml: {summary['data_yaml']}")

    if summary["images"] == 0:
        print("[retrain] no usable frames found — nothing to train on. Stopping.", file=sys.stderr)
        return 1

    # 3) --no-train: print the exact command a human would run, then stop.
    if args.no_train:
        print("\n[retrain] --no-train set: dataset built, skipping fine-tune. To train, run:")
        print(
            f"    camai-retrain --capture-root {args.capture_root} "
            f"--base-weights {args.base_weights} --out {args.out} "
            f"--epochs {args.epochs} --device {args.device}"
        )
        print(
            "  or directly: "
            f"yolo detect train data={summary['data_yaml']} model={args.base_weights} "
            f"epochs={args.epochs} imgsz={args.imgsz} device={args.device}"
        )
        return 0

    # 4) Fine-tune (lazy ultralytics import happens here).
    from camai_training import retrain, evaluate, promote

    data_yaml = summary["data_yaml"]
    print(f"\n[retrain] fine-tuning {args.base_weights} for {args.epochs} epoch(s) on {args.device} ...")
    new_weights = retrain.fine_tune(
        args.base_weights, data_yaml, args.out,
        epochs=args.epochs, imgsz=args.imgsz, device=args.device,
    )
    print(f"[retrain] fine-tuned weights: {new_weights}")

    # 5) Evaluate base vs new on the SAME val split, then compare.
    print("[retrain] evaluating base weights ...")
    base_metrics = evaluate.evaluate(args.base_weights, data_yaml)
    print("[retrain] evaluating fine-tuned weights ...")
    new_metrics = evaluate.evaluate(new_weights, data_yaml)
    cmp = evaluate.compare(base_metrics, new_metrics)

    print("\n[retrain] ===== before / after =====")
    print(f"    base: map50={base_metrics['map50']:.4f} map={base_metrics['map']:.4f}")
    print(f"    new : map50={new_metrics['map50']:.4f} map={new_metrics['map']:.4f}")
    print(f"    delta map50={cmp['map50_delta']:+.4f}  improved={cmp['improved']}")

    # 6) Promote only if better.
    if cmp["improved"]:
        promote_dir = args.promote_dir or os.path.join(args.out, "promoted")
        staged = promote.promote(new_weights, promote_dir)
        print(f"[retrain] PROMOTED (staged): {staged}")
    else:
        print("[retrain] not promoted: fine-tune did not improve map50. Keeping current model.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
