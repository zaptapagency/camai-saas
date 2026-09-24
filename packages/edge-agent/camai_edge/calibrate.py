"""Calibration wizard — the single biggest driver of real-world accuracy.

Runs a small local web server on the edge box (or an installer's laptop on the
same LAN) that:

* grabs a live snapshot from each configured camera,
* lets you draw entry/exit lines (retail) and zones/spaces (parking, warehouse)
  on that frame in the browser, and
* writes the geometry — as normalized [0,1] coordinates — back into the site's
  YAML config.

This is local-only by design: it never opens an inbound port to the cloud, it just
edits the config the edge agent reads. It's the on-ramp to the fully self-serve
calibration the plan calls for (same UI, later embedded in the cloud dashboard).

Run:
    python -m camai_edge.calibrate --config config.example.yaml [--port 8900]

The web server (FastAPI) is an optional dependency:  pip install "camai-edge[calibrate]"
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from camai_edge.config import CameraConfig, Line, SiteConfig, Zone


class GeometryIn(BaseModel):
    """Request body for saving a camera's calibration geometry.

    Defined at module scope (not inside ``create_app``) so FastAPI can resolve it
    as a request body — with ``from __future__ import annotations`` active, a
    function-local model would be seen as an unresolved string annotation and
    mis-parsed as a query parameter.
    """

    lines: list[dict] = []
    zones: list[dict] = []


# --------------------------------------------------------------------------- #
# Config write-back (pure, unit-tested — no web server or OpenCV needed)
# --------------------------------------------------------------------------- #

def apply_geometry(
    config_path: str | Path,
    camera_id: str,
    lines: list[dict[str, Any]],
    zones: list[dict[str, Any]],
) -> CameraConfig:
    """Validate and persist geometry for one camera into the YAML config.

    Coordinates in ``lines``/``zones`` are normalized [0,1]. Validation goes
    through the same Pydantic models the agent uses, so bad geometry is rejected
    here rather than crashing the pipeline later.

    NOTE: writing re-dumps the YAML and does not preserve comments. That's fine
    for a real site config edited by the wizard; keep hand-written example configs
    separate from live ones.

    Returns the updated ``CameraConfig``.
    """
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    cameras = raw.get("cameras") or []
    entry = next((c for c in cameras if c.get("id") == camera_id), None)
    if entry is None:
        raise KeyError(f"camera {camera_id!r} not found in {path}")

    # Validate through the models, then serialize back to plain dicts.
    validated_lines = [Line.model_validate(l) for l in lines]
    validated_zones = [Zone.model_validate(z) for z in zones]

    entry["lines"] = [l.model_dump(mode="json") for l in validated_lines]
    entry["zones"] = [z.model_dump(mode="json") for z in validated_zones]

    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    # Re-validate the whole file so we never leave it in a broken state.
    site = SiteConfig.load(path)
    return next(c for c in site.cameras if c.id == camera_id)


# --------------------------------------------------------------------------- #
# Web server (needs the [calibrate] extra: fastapi + uvicorn; snapshot needs cv2)
# --------------------------------------------------------------------------- #

def create_app(config_path: str | Path):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, Response

    config_path = Path(config_path)
    app = FastAPI(title="CamAI Calibration Wizard")
    ui_html = (Path(__file__).parent / "calibrate_ui.html").read_text(encoding="utf-8")

    def _load() -> SiteConfig:
        return SiteConfig.load(config_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(ui_html)

    @app.get("/api/cameras")
    def cameras() -> list[dict]:
        site = _load()
        return [
            {
                "id": c.id, "mode": c.mode, "source": c.source,
                "lines": [l.model_dump(mode="json") for l in c.lines],
                "zones": [z.model_dump(mode="json") for z in c.zones],
            }
            for c in site.cameras
        ]

    @app.get("/api/cameras/{camera_id}/snapshot")
    def snapshot(camera_id: str) -> Response:
        import cv2  # local import so the pure write-back path needs no OpenCV

        site = _load()
        cam = next((c for c in site.cameras if c.id == camera_id), None)
        if cam is None:
            raise HTTPException(404, f"camera {camera_id!r} not found")

        src = int(cam.source) if cam.source.isdigit() else cam.source
        cap = cv2.VideoCapture(src)
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok:
            raise HTTPException(502, f"could not read a frame from {cam.source!r}")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise HTTPException(500, "failed to encode snapshot")
        return Response(content=buf.tobytes(), media_type="image/jpeg")

    @app.get("/api/discover")
    def discover(timeout: float = 3.0, username: str = "", password: str = "") -> list[dict]:
        """Scan the LAN for ONVIF cameras so the wizard can list ones to activate.

        Imported lazily so the calibration server (and its snapshot/geometry paths)
        has no hard dependency on the discovery module or its optional onvif-zeep
        backend. Credentials are accepted as query params only for a local,
        installer-driven scan on a trusted LAN; they are used solely to resolve the
        RTSP stream URL and are never persisted here.
        """
        from camai_edge.discovery import discover_cameras

        cands = discover_cameras(timeout=timeout, username=username, password=password)
        return [c.model_dump(mode="json") for c in cands]

    @app.post("/api/cameras/{camera_id}/geometry")
    def save_geometry(camera_id: str, geom: GeometryIn) -> dict:
        try:
            cam = apply_geometry(config_path, camera_id, geom.lines, geom.zones)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except Exception as e:  # validation errors -> 400
            raise HTTPException(400, str(e))
        return {"status": "ok", "camera_id": cam.id,
                "lines": len(cam.lines), "zones": len(cam.zones)}

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="camai-calibrate")
    parser.add_argument("--config", required=True, help="path to the site YAML config")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    import uvicorn

    app = create_app(args.config)
    url = f"http://{args.host}:{args.port}/"
    print(f"[calibrate] editing {args.config}")
    print(f"[calibrate] open {url} to draw lines/zones")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
