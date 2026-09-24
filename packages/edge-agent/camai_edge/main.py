"""CamAI edge-agent entrypoint.

    python -m camai_edge.main --config config.example.yaml [--show] [--loop]

Loads a site config, starts the cloud-sync worker, and runs one pipeline per
enabled camera. Cameras run in background threads; the main thread owns the
optional debug window (OpenCV must render on the main thread) and Ctrl-C.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from camai_edge import __version__
from camai_edge.config import SiteConfig
from camai_edge.events import EventQueue
from camai_edge.pipeline import CameraPipeline
from camai_edge.sync import CloudSync


def _resolve_identity(cfg: SiteConfig, config_path: str) -> str:
    """Resolve the device's cryptographic identity, returning the device_id to use.

    Best-effort: minting certs needs the ``cryptography`` package (the
    ``camai-edge[identity]`` extra). If it's absent we fall back to the static
    ``device_id`` from the config so the agent still runs — mTLS just isn't active.
    When identity IS available, its cert/key paths populate any unset
    CloudConfig.device_cert/device_key so the outbound clients use them.
    """
    try:
        from camai_edge.identity import ensure_identity
    except Exception:  # pragma: no cover - import guard
        return cfg.device_id
    try:
        idir = Path(config_path).resolve().parent / cfg.identity.dir
        identity = ensure_identity(idir, cfg.identity)
    except Exception as e:
        print(f"[main] device identity unavailable ({e}); using config "
              f"device_id={cfg.device_id!r}. Install camai-edge[identity] for mTLS.")
        return cfg.device_id

    if not cfg.cloud.device_cert:
        cfg.cloud.device_cert = str(identity.cert_path)
    if not cfg.cloud.device_key:
        cfg.cloud.device_key = str(identity.key_path)
    print(f"[main] device identity {identity.device_id} (dir: {identity.dir})")
    return identity.device_id


def _register(cfg: SiteConfig, config_path: str, token: str) -> None:
    """Zero-touch registration: POST our CSR + enrollment token to the cloud.

    Best-effort and idempotent on the cloud side; a failure here never stops the
    agent (it just means the device isn't bound yet).
    """
    try:
        import httpx

        from camai_edge.identity import ensure_identity
        identity = ensure_identity(Path(config_path).resolve().parent / cfg.identity.dir,
                                   cfg.identity)
        url = cfg.cloud.ingest_url.rstrip("/") + "/v1/devices/register"
        resp = httpx.post(url, json={"enrollment_token": token,
                                     "csr": identity.csr_pem()}, timeout=10.0)
        print(f"[main] zero-touch register: HTTP {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[main] registration failed (continuing): {e}")


def _start_fleet(cfg: SiteConfig, device_id: str):
    """Start the outbound-only fleet poller if enabled in config; else return None.

    The desired-config / pinned-release payloads are surfaced via callbacks. Actually
    applying them (reconfiguring the pipeline, performing an OTA) is a supervisor
    concern kept out of the inference process, so here we log the intent — the hook
    a systemd/service wrapper would act on.
    """
    if not cfg.fleet_enabled:
        return None
    try:
        from camai_edge.fleet import FleetClient, FleetConfig
    except Exception as e:  # pragma: no cover - import guard
        print(f"[fleet] unavailable ({e}); skipping")
        return None

    def on_config(desired: dict) -> None:
        print(f"[fleet] new desired config v{desired.get('config_version')} "
              f"— apply via supervised restart")

    def on_release(desired: dict) -> None:
        print(f"[fleet] pinned release agent={desired.get('agent_version')} "
              f"model={desired.get('model_version')} — OTA is a supervisor step")

    client = FleetClient(
        FleetConfig(enabled=True, poll_interval_seconds=cfg.fleet_poll_seconds),
        cfg.cloud,
        device_id=device_id, tenant_id=cfg.tenant_id, agent_version=__version__,
        model_version=cfg.detector.weights,
        on_config=on_config, on_release=on_release,
    )
    client.start()
    print(f"[fleet] polling desired state every {cfg.fleet_poll_seconds}s")
    return client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="camai-edge")
    parser.add_argument("--config", required=True, help="path to site YAML config")
    parser.add_argument("--show", action="store_true",
                        help="open a debug window per camera (single-camera dev use)")
    parser.add_argument("--loop", action="store_true",
                        help="loop file sources (handy for demos)")
    parser.add_argument("--queue", default="event-queue.db", help="local queue db path")
    parser.add_argument("--enroll-token", default="",
                        help="one-time enrollment token for zero-touch registration")
    args = parser.parse_args(argv)

    cfg = SiteConfig.load(args.config)
    cameras = [c for c in cfg.cameras if c.enabled]
    if not cameras:
        print("no enabled cameras in config", file=sys.stderr)
        return 1

    # Resolve the device identity first: its device_id is authoritative and its
    # cert/key feed the outbound (ingest + fleet) clients.
    device_id = _resolve_identity(cfg, args.config)
    if args.enroll_token and cfg.cloud.ingest_url:
        _register(cfg, args.config, args.enroll_token)

    queue = EventQueue(args.queue)
    sync = CloudSync(
        cfg.cloud, queue,
        device_id=device_id, tenant_id=cfg.tenant_id, agent_version=__version__,
    )
    sync.start()
    if not cfg.cloud.ingest_url:
        print("[main] no ingest_url configured -> running offline; "
              "events buffer in the local queue")

    # Fleet client: pulls desired config + pinned release outbound-only (opt-in).
    fleet_client = _start_fleet(cfg, device_id)

    pipelines = [
        CameraPipeline(
            cfg.tenant_id, cfg.site_id, cam, cfg.detector, queue, sync,
            show=args.show, loop=args.loop,
        )
        for cam in cameras
    ]

    stop_all = lambda *_: [p.stop() for p in pipelines]
    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    if args.show and len(pipelines) == 1:
        # Single-camera dev mode: run on the main thread so imshow works.
        pipelines[0].run()
    else:
        if args.show:
            print("[main] --show ignored with multiple cameras (needs main thread)")
        threads = [threading.Thread(target=p.run, name=p.camera.id) for p in pipelines]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            stop_all()
        for t in threads:
            t.join()

    if fleet_client is not None:
        fleet_client.stop()
    sync.stop()
    queue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
