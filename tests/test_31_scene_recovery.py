"""MANDATORY test (user requirement §28): reproduce the 31-scene
failure scenario with the same 13 broken positions, prove that every
broken scene is detected, recovered, and that the final concatenation
is a valid MP4.

Broken positions mirror the real failure report:
  seg_001, seg_003, seg_004, seg_005, seg_012, seg_013, seg_015,
  seg_019, seg_020, seg_023, seg_024, seg_026, seg_028  (1-based)
"""
import os
import threading

from src.media_probe import probe
from src.scene_generator import SceneGenerator, SceneSpec
from src.scene_validator import validate_segment

# 0-based scene indexes of the 13 deliberately broken scenes
BROKEN = {0, 2, 3, 4, 11, 12, 14, 18, 19, 22, 23, 25, 27}
assert len(BROKEN) == 13

BROKEN_KINDS = ["black", "empty", "corrupt", "oneframe"]


class StubAsset:
    def __init__(self, aid, kind, local_path):
        self.id = aid
        self.kind = kind
        self.local_path = local_path


def _broken_asset(media_dir, idx, kind):
    names = {"black": "black.mp4", "empty": "empty.mp4",
             "corrupt": "corrupt.mp4", "oneframe": "oneframe.mp4"}
    return StubAsset(f"broken-{idx}-{kind}", "video",
                     os.path.join(media_dir, names[kind]))


def test_31_scene_broken_recovery(workdir, media_dir, engine, tmp_path):
    seg_dir = os.path.join(workdir, "segments")
    assets_dir = os.path.join(workdir, "assets")
    os.makedirs(seg_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    still = os.path.join(media_dir, "still.png")

    def search(spec, attempt):
        i = spec.index
        if i in BROKEN:
            kind = BROKEN_KINDS[i % len(BROKEN_KINDS)]
            # positions 11 and 22 recover on retry (good asset appears);
            # all other broken positions stay broken through all 3 attempts
            if i in {11, 22} and attempt >= 1:
                return StubAsset(f"good-{i}", "image", still)
            return _broken_asset(media_dir, i, kind)
        return StubAsset(f"good-{i}", "image", still)

    gen = SceneGenerator(seg_dir=seg_dir, assets_dir=assets_dir,
                         width=640, height=360, fps=30,
                         search_fn=search, cancel_event=threading.Event(),
                         max_workers=4, cache=None)
    specs = [SceneSpec(index=i, scene_id=i, text=f"Scene {i} text",
                       duration=3.0) for i in range(31)]
    results = gen.generate_all(specs)
    assert len(results) == 31

    # 1) every single scene must be valid after recovery
    for r in results:
        assert r.report is not None and r.report.valid, \
            f"scene {r.spec.index} invalid: {r.report.describe() if r.report else 'no report'}"

    # 2) exactly the 13 broken scenes were recovered
    recovered = {r.spec.index for r in results if r.recovered}
    assert recovered == BROKEN, \
        f"recovered={sorted(recovered)} expected={sorted(BROKEN)}"
    fallback_used = {r.spec.index for r in results
                     if r.asset_id == "fallback"}
    assert fallback_used == BROKEN - {11, 22}, \
        f"fallback used at {sorted(fallback_used)}"

    # 3) no final segment is black/blank
    for r in results:
        ratio = r.report.metrics.get("black_ratio", 1.0)
        assert ratio < 0.5, \
            f"scene {r.spec.index} is black ({ratio:.1%}) - the original bug!"

    # 4) concatenation of all 31 segments is a valid MP4
    concat_list = os.path.join(workdir, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"file '{r.segment_path}'\n")
    final = os.path.join(workdir, "final_31.mp4")
    engine.run(["-f", "concat", "-safe", "0", "-i", concat_list,
                "-c", "copy", final],
               stage="test_31_concat", timeout=300)
    info = probe(final, engine.ffprobe)
    assert not info.probe_error and info.has_video
    expected = 31 * 3.0
    assert abs(info.duration - expected) < 5.0, \
        f"duration {info.duration} != ~{expected}"
    print(f"\n31/31 scenes valid, 13 recovered, "
          f"final MP4: {info.video.width}x{info.video.height}, "
          f"{info.duration:.1f}s")
