"""End-to-end render test (offline): 3-sentence project with an
uploaded synthetic voiceover -> all 11 stages -> valid final MP4.
Media search runs without API keys (generated graphics)."""
import os
import subprocess

from src.export_engine import ExportEngine
from src.project import Project
from src.project_manager import ProjectManager
from src.render_engine import STAGES, RenderEngine
from src.settings import Settings

SCRIPT = ("Stars are giant balls of hot gas that shine for billions of years. "
          "Light from the nearest star takes over four years to reach Earth. "
          "On a clear night you can see thousands of them with your eyes.")


def _voice_wav(path, seconds=9.0):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i",
                    f"sine=frequency=220:duration={seconds}",
                    "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", path],
                   check=True, timeout=60)


def _mini_engine(tmp_path, script, voice_secs=4.0):
    work = str(tmp_path / "work")
    os.makedirs(work, exist_ok=True)
    voice = str(tmp_path / "voice.wav")
    _voice_wav(voice, voice_secs)
    p = Project("Mini")
    p.script = script
    pm = ProjectManager(p, str(tmp_path))
    s = Settings({"output_format": "16:9", "output_resolution": "1080p",
                  "output_fps": 30, "output_codec": "h264",
                  "voice_upload": voice, "captions_enabled": False,
                  "text_overlays": "off"})
    eng = RenderEngine(pm, work, s)
    eng.run(preview=True)
    return eng


def test_regenerate_buttons_work(tmp_path):
    eng = _mini_engine(tmp_path, "A single bright sentence about the ocean.")
    assert len(eng.scene_results) == 1
    sid = eng.scenes[0].id
    assert sid in eng.assets

    asset = eng.regenerate_visual(sid)
    assert asset is not None
    assert eng.assets[sid].id == asset.id
    # regenerated segment is valid and swapped into the concat list
    r = eng.scene_results[0]
    assert r.report.valid, r.report.describe()
    assert os.path.isfile(eng.normalized[0])

    eng.regenerate_text(sid)   # must not crash
    eng.regenerate_sfx(sid)    # must not crash
    eng.regenerate_music()     # must not crash
    assert any(c.kind == "music" for c in eng.timeline.by_track("music"))

    # locked scenes are left alone
    eng.scenes[0].locked = True
    assert eng.regenerate_visual(sid) is None
    print("\nregenerate_* OK")


def test_e2e_3_scene_render_offline(tmp_path):
    work = str(tmp_path / "work")
    os.makedirs(work, exist_ok=True)
    voice = str(tmp_path / "voice.wav")
    _voice_wav(voice)

    p = Project("E2E Stars")
    p.script = SCRIPT
    pm = ProjectManager(p, str(tmp_path))

    s = Settings({"output_format": "16:9", "output_resolution": "1080p",
                  "output_fps": 30, "output_codec": "h264",
                  "voice_upload": voice, "captions_enabled": True,
                  "text_overlays": "off"})
    stages_seen = []
    eng = RenderEngine(pm, work, s,
                       progress_cb=lambda i, name, msg: stages_seen.append(name))
    final = eng.run(preview=True)

    assert os.path.isfile(final), "final MP4 was not produced"
    assert len(set(stages_seen)) == len(STAGES), \
        f"stages seen: {sorted(set(stages_seen))}"
    assert len(eng.scene_results) == 3
    assert all(r.report.valid for r in eng.scene_results)
    assert os.path.isfile(eng.mixed_path)

    problems = ExportEngine(engine=eng.engine).validate_final_mp4(
        final, expect_w=640, expect_h=360, expect_fps=30, expect_audio=True)
    assert problems == [], f"final MP4 problems: {problems}"
    print(f"\nE2E OK: {final} ({len(eng.scene_results)} scenes, "
          f"{len(stages_seen)} stage updates)")
