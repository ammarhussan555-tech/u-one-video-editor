"""Regression tests for the 59%-freeze (Uzair 2026-10-04).

Root cause: a YouTube candidate whose yt-dlp extraction stalls (bot-check
page) fires no progress events, so the old progress-hook-only deadline never
fired and the scene worker hung forever -> generate_all() never returned.

Fixes under test:
1. _ytdlp_download has a hard wall-clock bound (daemon thread + join
   timeout) and returns None instead of hanging.
2. SceneGenerator.generate_all has a per-scene watchdog: stalled scenes are
   force-finished with the fallback card so the render always terminates.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import media_search
from src.scene_generator import (SCENE_WATCHDOG_S, SceneGenerator, SceneResult,
                                 SceneSpec)


# -- 1. yt-dlp extraction hang ---------------------------------------------

class _HungYDL:
    """YoutubeDL whose download() blocks forever (bot-check stall)."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download(self, urls):
        threading.Event().wait()  # hang forever


class _FakeYtDlp:
    YoutubeDL = _HungYDL


def test_ytdlp_extraction_hang_returns_none(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", _FakeYtDlp)
    monkeypatch.setattr(media_search.asset_cache, "lookup",
                        lambda url, kind="": None)
    monkeypatch.setattr(
        media_search.asset_cache, "cached_path_for",
        lambda url, kind="", ext=".mp4": tmp_path / ("dl" + ext))
    monkeypatch.setattr(media_search.asset_cache, "store",
                        lambda *a, **k: None)
    start = time.time()
    res = media_search._ytdlp_download("https://youtube.com/watch?v=x",
                                       "video", timeout_s=2)
    elapsed = time.time() - start
    assert res is None
    assert elapsed < 20, f"took {elapsed:.1f}s - hang not bounded"


# -- 2. generate_all watchdog -----------------------------------------------

def _mk_gen(tmp_path, monkeypatch):
    gen = SceneGenerator(
        seg_dir=str(tmp_path / "seg"), assets_dir=str(tmp_path / "assets"),
        width=1280, height=720, fps=30,
        search_fn=lambda spec, attempt: None, max_workers=2)
    os.makedirs(gen.seg_dir, exist_ok=True)
    return gen


def test_generate_all_watchdog_force_finishes_stalled_scene(tmp_path,
                                                            monkeypatch):
    gen = _mk_gen(tmp_path, monkeypatch)
    gen.scene_watchdog_s = 3  # test-only: fail fast
    hang = threading.Event()

    def fake_generate_one(spec):
        if spec.index == 1:
            hang.wait(30)  # simulate the stuck scene worker
            return SceneResult(spec=spec, segment_path="seg1.mp4",
                               asset_id="late")
        return SceneResult(spec=spec, segment_path="seg0.mp4",
                           asset_id="ok0")

    def fake_fallback(spec, engine, reason):
        assert spec.index == 1
        assert "watchdog" in reason
        hang.set()  # release the stuck worker so the test can exit
        return SceneResult(spec=spec, segment_path="seg1.mp4",
                           asset_id="fallback", recovered=True)

    monkeypatch.setattr(gen, "generate_one", fake_generate_one)
    monkeypatch.setattr(gen, "_fallback_result", fake_fallback)
    seen = []
    specs = [SceneSpec(index=0, scene_id=0, text="a", duration=1.0),
             SceneSpec(index=1, scene_id=1, text="b", duration=1.0)]
    start = time.time()
    out = gen.generate_all(specs,
                           progress_cb=lambda dn, total, idx: seen.append(
                               (dn, total)))
    elapsed = time.time() - start
    assert [r.asset_id for r in out] == ["ok0", "fallback"]
    assert seen[-1] == (2, 2), f"progress never completed: {seen}"
    assert elapsed < 60, f"took {elapsed:.1f}s - watchdog did not fire"


def test_watchdog_default_is_sane():
    assert SCENE_WATCHDOG_S >= 1800  # never cut a legit slow scene short


# -- 3. YouTube search hang -----------------------------------------------

class _HungSearchYDL:
    """YoutubeDL whose extract_info() blocks forever (bot-check stall)."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, query, download=False):
        threading.Event().wait()  # hang forever


class _FakeSearchYtDlp:
    YoutubeDL = _HungSearchYDL


def test_youtube_search_hang_returns_empty(tmp_path, monkeypatch):
    from src.media_search import YouTubeProvider
    monkeypatch.setitem(sys.modules, "yt_dlp", _FakeSearchYtDlp)
    prov = YouTubeProvider()
    start = time.time()
    res = prov._search_ids("geopolitics news", num=2, timeout_s=2)
    elapsed = time.time() - start
    assert res == []
    assert elapsed < 20, f"took {elapsed:.1f}s - search hang not bounded"


def test_search_all_survives_hung_provider(tmp_path, monkeypatch):
    from src.media_search import MediaFinder
    finder = MediaFinder(str(tmp_path / "assets"))

    class HungProvider:
        name = "Hung"
        first_query_only = False

        def search(self, query, kind="video", per_page=4):
            threading.Event().wait()  # hang forever
            return []

    class QuickProvider:
        name = "Quick"
        first_query_only = False

        def search(self, query, kind="video", per_page=4):
            return [{"url": "https://example.com/a.mp4", "source": "Quick",
                     "license": "x", "w": 640, "h": 360, "dur": 5.0,
                     "tags": "news", "query": query}]

    monkeypatch.setattr(finder, "providers", [HungProvider(), QuickProvider()])
    start = time.time()
    out = finder._search_all("news", "video")
    elapsed = time.time() - start
    # quick provider's results come back; the hung one is abandoned
    assert any(c.get("source") == "Quick" for c in out)
    assert elapsed < 70, f"took {elapsed:.1f}s - executor waited on hang"


# -- 4. media preference: video_only / image_only --------------------------

def test_kind_order_only_modes(tmp_path):
    from src.media_search import MediaFinder
    for pref, expected in [("video_only", ["video"]),
                           ("image_only", ["image"]),
                           ("video_first", ["video", "image"]),
                           ("image_first", ["image", "video"]),
                           ("ai_auto", ["video", "image"]),
                           ("whatever", ["video", "image"])]:
        f = MediaFinder(str(tmp_path / "a"), preference=pref)
        assert f._kind_order() == expected, pref
