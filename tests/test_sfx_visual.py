"""Real-SFX + visual-driven placement tests (2026-10-02).

  * assets/sfx/ holds 5 real PD/CC0 recordings (SOURCES.md); the library
    prefers them over numpy synthesis, with synthesis as fallback.
  * place_sfx drives placement from VISUAL clip tags first (explosion ->
    boom, crowd -> crowd bed, photo -> shutter ...), narration keywords
    only when the visual says nothing.
"""
import os

import pytest

from src.audio_design import (  # noqa: E402
    BUNDLED_SFX_FILES,
    VISUAL_SFX,
    _ENGINE_SFX,
    ensure_sfx_library,
    place_sfx,
)
from src.script_analyzer import analyze_sentence  # noqa: E402
from src import app_paths  # noqa: E402

REPO_SFX = os.path.join(os.path.dirname(__file__), "..", "assets", "sfx")


def test_bundled_real_files_exist():
    for name, fname in BUNDLED_SFX_FILES.items():
        p = os.path.join(REPO_SFX, fname)
        assert os.path.isfile(p), f"missing bundled SFX for {name}: {fname}"
        assert os.path.getsize(p) > 1000, fname


def test_sources_md_documents_every_file():
    md = open(os.path.join(REPO_SFX, "SOURCES.md"), encoding="utf-8").read()
    for fname in BUNDLED_SFX_FILES.values():
        assert fname in md, fname
    assert "Public Domain" in md or "public domain" in md.lower()
    assert "CC0" in md


def test_sfx_dir_resolves_in_dev_checkout():
    d = app_paths.sfx_dir()
    assert d is not None
    assert (d / "camera_real.wav").exists()


def test_library_prefers_real_files(tmp_path):
    lib = ensure_sfx_library(str(tmp_path))
    # real recordings win over synthesis
    assert lib["camera"].endswith("camera_real.wav")
    assert lib["crowd"].endswith("crowd_real.wav")
    assert lib["alert"].endswith("siren_real.wav")
    assert lib["boom"].endswith("boom_real.wav")
    assert lib["ding"].endswith("ding_real.wav")
    for p in lib.values():
        assert os.path.isfile(p), p


def test_library_synthesizes_missing_names(tmp_path):
    lib = ensure_sfx_library(str(tmp_path))
    # whoosh has no bundled recording -> synthesized fallback
    assert lib["whoosh"].endswith("whoosh_v2.wav")
    assert os.path.isfile(lib["whoosh"])
    # real files are valid WAVs ffmpeg can read
    import subprocess
    for name in ("camera", "crowd", "alert", "boom", "ding"):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=sample_rate,channels", "-of", "csv=p=0", lib[name]],
            capture_output=True, text=True)
        assert r.returncode == 0, name
        assert "44100,1" in r.stdout.replace(" ", ""), (name, r.stdout)


def _timings(n, dur=6.0):
    return [{"start": i * dur, "end": (i + 1) * dur} for i in range(n)]


def test_visual_explosion_drives_boom():
    sas = [analyze_sentence("The markets closed higher today.")]
    ev = place_sfx(sas, _timings(1), seed=1,
                   visual_tags=["explosion blast fire footage"],
                   transition_whoosh=False)
    assert any(e["sfx"] == "boom" for e in ev), ev


def test_visual_crowd_drives_crowd_bed():
    sas = [analyze_sentence("The markets closed higher today.")]
    ev = place_sfx(sas, _timings(1), seed=1,
                   visual_tags=["crowd protest rally street"],
                   transition_whoosh=False)
    assert any(e["sfx"] == "crowd" for e in ev), ev


def test_visual_photo_drives_shutter():
    sas = [analyze_sentence("The markets closed higher today.")]
    ev = place_sfx(sas, _timings(1), seed=1,
                   visual_tags=["archival photo black and white"],
                   transition_whoosh=False)
    assert any(e["sfx"] == "camera" for e in ev), ev


def test_visual_siren_drives_alert():
    sas = [analyze_sentence("The markets closed higher today.")]
    ev = place_sfx(sas, _timings(1), seed=1,
                   visual_tags=["police siren street night"],
                   transition_whoosh=False)
    assert any(e["sfx"] == "alert" for e in ev), ev


def test_bell_does_not_fire_on_rebellion():
    # word-boundary matching: "rebellion" must not trigger the bell ding
    sas = [analyze_sentence("The markets closed higher today.")]
    ev = place_sfx(sas, _timings(1), seed=1,
                   visual_tags=["rebellion uprising documentary"],
                   transition_whoosh=False)
    assert not any(e["sfx"] == "ding" for e in ev), ev


def test_narration_fallback_when_visual_silent():
    sas = [analyze_sentence("The explosion rocked the capital.")]
    ev = place_sfx(sas, _timings(1), seed=1, visual_tags=[""],
                   transition_whoosh=False)
    assert any(e["sfx"] == "boom" for e in ev), ev


def test_visual_beats_narration_on_conflict():
    # visual says crowd, narration says explosion -> visual wins
    sas = [analyze_sentence("The explosion rocked the capital.")]
    ev = place_sfx(sas, _timings(1), seed=1, max_per_scene=1,
                   visual_tags=["crowd cheering rally"],
                   transition_whoosh=False)
    got = [e["sfx"] for e in ev]
    assert got == ["crowd"], got


def test_no_immediate_repeats_with_visuals():
    sas = [analyze_sentence("Markets rally.") for _ in range(4)]
    ev = place_sfx(sas, _timings(4), seed=1,
                   visual_tags=["crowd protest", "explosion blast",
                                "crowd rally", "archival photo"],
                   transition_whoosh=False)
    names = [e["sfx"] for e in ev]
    for a, b in zip(names, names[1:]):
        assert a != b, names


def test_transition_whoosh_still_works():
    sas = [analyze_sentence("Markets rally.") for _ in range(3)]
    ev = place_sfx(sas, _timings(3), seed=1, visual_tags=[""] * 3,
                   transition_whoosh=True)
    assert sum(1 for e in ev if e["sfx"] == "whoosh") >= 1, ev


def test_density_cap_trims_transition_whooshes():
    # the cap drops the quietest transition whooshes first; visual/
    # keyword hits are never dropped (documented behavior)
    sas = [analyze_sentence("Markets rally.") for _ in range(20)]
    ev = place_sfx(sas, _timings(20, dur=3.0), seed=1,
                   visual_tags=[""] * 20, transition_whoosh=True)
    total_min = 20 * 3.0 / 60.0
    assert len(ev) <= int(total_min * 10) + 2, len(ev)
    assert all(e["sfx"] == "whoosh" for e in ev)


def test_visual_sfx_table_covers_research_beats():
    names = {s for s, _ in VISUAL_SFX}
    for must in ("boom", "crowd", "camera", "alert", "ding", "whoosh",
                 "impact", "tick"):
        assert must in names, must


def test_mix_audio_without_music_no_sfx(tmp_path):
    import subprocess
    from src.audio_design import mix_audio
    voice = str(tmp_path / "voice.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ar", "44100", voice],
                   check=True)
    out = str(tmp_path / "mix.m4a")
    # must not raise: previously the unconnected [vkey] split broke the
    # filtergraph whenever music was absent
    mix_audio(voice, None, [], {}, out, 2.0)
    assert os.path.getsize(out) > 1000


def test_mix_audio_without_music_with_real_sfx(tmp_path):
    import subprocess
    from src.audio_design import mix_audio
    lib = ensure_sfx_library(str(tmp_path / "lib"))
    voice = str(tmp_path / "voice.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-ar", "44100", voice],
                   check=True)
    out = str(tmp_path / "mix2.m4a")
    mix_audio(voice, None, [{"time": 1.0, "sfx": "boom", "gain_db": -10}],
              lib, out, 3.0)
    assert os.path.getsize(out) > 1000
