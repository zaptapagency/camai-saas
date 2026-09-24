"""Frame ingest.

Pulls frames from an RTSP stream, a local video file, or a webcam via OpenCV and
throttles to the camera's ``target_fps``. Counting does not need full video rate,
so we sample frames — this is what lets one edge box hold several streams.

The source is deliberately dumb: it yields frames and a monotonic capture
timestamp. Everything smart (detection, tracking, counting) happens downstream.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator

import cv2


@dataclass
class Frame:
    image: "cv2.typing.MatLike"
    ts: float          # wall-clock capture time (epoch seconds)
    index: int         # sampled-frame counter (not raw frame number)


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
        source: RTSP url, file path, or webcam index string.
        target_fps: how many frames per second to actually process.
        loop: restart when a file ends (useful for demoing against a clip).
    """
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
