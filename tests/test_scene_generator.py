"""Scene generator tests: invalid scenes are detected, recovered via
retry, and only the guaranteed fallback card is used as a last resort."""
import os
import threading

from src.scene_generator import SceneGenerator, SceneSpec
from src.scene_validator import validate_segment


class StubAsset:
    def __init__(self, aid, kind, local_path):
        self.id = aid
        self.kind = kind
        self.local_path = local_path


def _gen(workdir, media_dir, search_fn, **kw):
    seg_dir = os.path.join(workdir, "segments")
    assets_dir = os.path.join(workdir, "assets")
    os.makedirs(seg_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    return SceneGenerator(
        seg_dir=seg_dir, assets_dir=assets_dir,
        width=640, height=360, fps=30,
        search_fn=search_fn, cancel_event=threading.Event(),
        max_workers=2, cache=None, **kw)


def test_retry_recovers_black_scene(workdir, media_dir, engine):
    black = StubAsset("black1", "video", os.path.join(media_dir, "black.mp4"))
    good = StubAsset("good1", "image", os.path.join(media_dir, "still.png"))

    def search(spec, attempt):
        return black if attempt == 0 else good

    gen = _gen(workdir, media_dir, search)
    res = gen.generate_one(SceneSpec(index=0, scene_id=0, text="t", duration=2.0))
    assert res.report.valid, res.report.describe()
    assert res.attempts == 2
    assert res.recovered


def test_exhausted_retries_use_fallback_card(workdir, media_dir, engine):
    black = StubAsset("blackX", "video", os.path.join(media_dir, "black.mp4"))

    def search(spec, attempt):
        return black  # always broken

    gen = _gen(workdir, media_dir, search)
    res = gen.generate_one(SceneSpec(index=0, scene_id=5, text="t", duration=2.0))
    assert res.report.valid, res.report.describe()
    assert res.recovered
    assert res.asset_id == "fallback"
    # the fallback card itself must be bright and valid
    rep = validate_segment(res.segment_path, engine=engine,
                           expect_w=640, expect_h=360)
    assert rep.valid, rep.describe()
    assert rep.metrics["black_ratio"] < 0.2, "fallback card must not be black"


def test_fallback_card_is_not_black_or_frozen(workdir, media_dir, engine):
    gen = _gen(workdir, media_dir, lambda s, a: None)
    card = gen.fallback_card(engine)
    rep = validate_segment(card, engine=engine, expect_w=640, expect_h=360,
                           min_duration=1.0)
    assert rep.valid, rep.describe()
    assert rep.metrics["black_ratio"] < 0.2
    assert rep.metrics["frozen_ratio"] < 0.5
