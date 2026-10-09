"""Supervisor that keeps a live camera streaming indefinitely.

Runs the edge agent for a cam spec in a loop: resolve a fresh stream URL, run the
agent, and when it exits (stream drop, HLS URL expiry ~hours, transient error)
re-resolve and restart after a short backoff. Launch it DETACHED (e.g. PowerShell
`Start-Process`) so it outlives any single foreground/background command.

    python tools/keep_live_cam.py --spec packages/edge-agent/demo-run.cams.yaml

Stop it by killing this process (it's the python running keep_live_cam.py).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def log(msg: str) -> None:
    print(f"[keep {datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", type=Path, default=REPO / "packages/edge-agent/demo-run.cams.yaml",
                    help="cam spec to re-resolve each restart (YouTube/HLS sources)")
    ap.add_argument("--out", type=Path, default=REPO / "packages/edge-agent/demo-run.yaml")
    ap.add_argument("--config", type=Path, default=None,
                    help="run this ready config directly, skipping resolve (e.g. image-poll cams)")
    ap.add_argument("--queue", default=os.path.join(os.environ.get("TEMP", "/tmp"), "camai-demo-queue.db"))
    ap.add_argument("--backoff", type=float, default=10.0, help="seconds between restarts")
    args = ap.parse_args()

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "packages/schema"), str(REPO / "packages/edge-agent")])

    config = args.config or args.out
    log(f"supervising {config.name} (pid {os.getpid()}, resolve={args.config is None})")
    while True:
        try:
            # Stream sources (YouTube/HLS) need a fresh URL each restart; a ready
            # config (image-poll, direct RTSP) is run as-is.
            if args.config is None:
                subprocess.run([sys.executable, str(REPO / "tools/resolve_streams.py"),
                                "--spec", str(args.spec), "--out", str(args.out)],
                               cwd=str(REPO), check=False)
            log("edge agent starting")
            rc = subprocess.run([sys.executable, "-m", "camai_edge.main",
                                 "--config", str(config), "--queue", args.queue],
                                cwd=str(REPO), env=env).returncode
            log(f"edge agent exited ({rc}); restarting in {args.backoff:.0f}s")
        except KeyboardInterrupt:
            log("stopped")
            return
        except Exception as e:
            log(f"supervisor error: {e}")
        time.sleep(args.backoff)


if __name__ == "__main__":
    main()
