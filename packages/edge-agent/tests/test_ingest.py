"""Tests for frame ingest — specifically the still-image (snapshot) poll path."""

import itertools
from contextlib import contextmanager

import cv2
import numpy as np

from camai_edge import ingest
from camai_edge.ingest import frames, is_image_source


def test_is_image_source_detection():
    assert is_image_source("https://cwwp2.dot.ca.gov/.../cam.jpg")
    assert is_image_source("https://host/cam.JPEG?foo=1")
    assert is_image_source("image:https://host/snapshot")   # explicit scheme
    assert is_image_source("http://host/frame.png")
    assert not is_image_source("https://host/stream/playlist.m3u8")
    assert not is_image_source("rtsp://host/stream")
    assert not is_image_source("0")  # webcam index


def _jpeg_bytes(w=64, h=48) -> bytes:
    img = np.full((h, w, 3), 120, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_image_frames_polls_and_decodes(monkeypatch):
    jpeg = _jpeg_bytes()

    @contextmanager
    def fake_urlopen(req, timeout=0):
        class _R:
            def read(self_inner):
                return jpeg
        yield _R()

    monkeypatch.setattr(ingest.urllib.request, "urlopen", fake_urlopen)

    out = list(itertools.islice(frames("https://host/cam.jpg", target_fps=1000), 3))
    assert len(out) == 3
    assert out[0].image.shape == (48, 64, 3)
    assert [f.index for f in out] == [0, 1, 2]


def test_image_frames_skips_failed_fetch(monkeypatch):
    jpeg = _jpeg_bytes()
    calls = {"n": 0}

    @contextmanager
    def flaky_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient network error")

        class _R:
            def read(self_inner):
                return jpeg
        yield _R()

    monkeypatch.setattr(ingest.urllib.request, "urlopen", flaky_urlopen)

    # First poll fails (no frame), subsequent polls succeed — the generator must
    # not raise, just skip the bad one.
    out = list(itertools.islice(frames("https://host/cam.jpg", target_fps=1000), 1))
    assert len(out) == 1
    assert calls["n"] >= 2
