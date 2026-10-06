"""Frame ingest.

Pulls frames from an RTSP stream, a local video file, or a webcam via OpenCV and
throttles to the camera's ``target_fps``. Counting does not need full video rate,
so we sample frames — this is what lets one edge box hold several streams.

The source is deliberately dumb: it yields frames and a monotonic capture
timestamp. Everything smart (detection, tracking, counting) happens downstream.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np


@dataclass
class Frame:
    image: "cv2.typing.MatLike"
    ts: float          # wall-clock capture time (epoch seconds)
    index: int         # sampled-frame counter (not raw frame number)


_IMAGE_EXTS = (".jpg", ".jpeg", ".png")
_IMAGE_SCHEME = "image:"
_HTTP_UA = "Mozilla/5.0 (CamAI edge agent)"


def is_image_source(source: str) -> bool:
    """True when the source is a periodically-updated still image (JPEG endpoint),
    not a video stream — e.g. a DOT traffic-camera snapshot URL. Detected by an
    explicit ``image:`` prefix or an image file extension on the path."""
    s = source.lower()
    if s.startswith(_IMAGE_SCHEME):
        return True
    return s.split("?", 1)[0].rstrip("/").endswith(_IMAGE_EXTS)


def _image_url(source: str) -> str:
    return source[len(_IMAGE_SCHEME):] if source.lower().startswith(_IMAGE_SCHEME) else source


def _image_frames(url: str, target_fps: float, *, timeout: float = 15.0) -> Iterator[Frame]:
    """Poll a still-image URL at ``target_fps`` and yield decoded frames.

    Many public cameras (DOT snapshots) expose only a JPEG that updates every few
    seconds rather than a video stream. We fetch it on a cadence (cache-busted),
    decode with OpenCV, and feed it to the same pipeline. A failed fetch/decode is
    skipped — one bad poll must not kill a long-running camera.
    """
    interval = 1.0 / target_fps if target_fps > 0 else 0.0
    index = 0
    while True:
        start = time.time()
        bust = ("&" if "?" in url else "?") + f"t={int(start * 1000)}"
        image = None
        try:
            req = urllib.request.Request(url + bust, headers={"User-Agent": _HTTP_UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                buf = r.read()
            image = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            image = None
        if image is not None:
            yield Frame(image=image, ts=time.time(), index=index)
            index += 1
        elapsed = time.time() - start
        if elapsed < interval:
            time.sleep(interval - elapsed)


def _open(source: str) -> cv2.VideoCapture:
    # A bare integer string means a local webcam index.
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
    else:
        # For RTSP prefer TCP transport — UDP drops frames on lossy links.
        if source.lower().startswith("rtsp://"):
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        else:
            cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video source: {source!r}")
    return cap


def frames(source: str, target_fps: float, *, loop: bool = False) -> Iterator[Frame]:
    """Yield frames throttled to ``target_fps``.

    Args:
        source: RTSP/HLS url, file path, webcam index string, or a still-image
            URL (JPEG snapshot endpoint, auto-detected — see ``is_image_source``).
        target_fps: how many frames per second to actually process.
        loop: restart when a file ends (useful for demoing against a clip).
    """
    if is_image_source(source):
        yield from _image_frames(_image_url(source), target_fps)
        return

    cap = _open(source)
    min_interval = 1.0 / target_fps
    index = 0
    last_emit = 0.0
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                if loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break

            now = time.time()
            # Drop frames that arrive faster than we intend to process.
            if now - last_emit < min_interval:
                continue
            last_emit = now
            yield Frame(image=image, ts=now, index=index)
            index += 1
    finally:
        cap.release()
