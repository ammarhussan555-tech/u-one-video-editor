"""Regression tests for Uzair's 2026-10-02 20:46 PKT report:
"koi be caption select kro apply simple hoata ha"
(whichever caption preset is selected, the burn comes out simple).

Root cause: faster-whisper is an OPTIONAL (commented-out) dependency,
so with an uploaded voiceover there are no measured word timings.
whisper_word_timings() returned None, word_timings stayed empty, and
build_ass() silently took its SIMPLE branch for every preset -- the
gallery cards look fancy (static mocks), but every burn was plain.

Fix: estimate_word_timings() (proportional fallback, mirroring
get_sentence_timings()' long-standing approach) wired in
render_engine.py after the Whisper attempt, so all 120 presets
animate even without Whisper.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.voice import estimate_word_timings  # noqa: E402
from src.voiceover import estimate_word_timings as facade_est  # noqa: E402
from src.text_captions import build_ass, CAPTION_TEMPLATES  # noqa: E402


def test_facade_exports_estimator():
    assert facade_est is estimate_word_timings


def test_estimator_distributes_proportionally():
    sents = [{"text": "Deep underground sits", "start": 0.0, "end": 6.0}]
    words = estimate_word_timings(sents)
    assert words is not None and len(words) == 3
    # Exact sentence bounds, monotonic, no gaps/overlaps.
    assert words[0]["start"] == 0.0
    assert words[-1]["end"] == 6.0
    for a, b in zip(words, words[1:]):
        assert a["start"] < a["end"] <= b["start"] + 1e-9
    # Longer words get more time.
    d = {w["word"]: w["end"] - w["start"] for w in words}
    assert d["underground"] > d["sits"] > 0


def test_estimator_empty_input():
    assert estimate_word_timings([]) is None
    assert estimate_word_timings(None) is None
    assert estimate_word_timings([{"text": "  ", "start": 0, "end": 1}]) is None


def test_estimated_words_drive_fancy_burn():
    # Bold 01 + estimated words must take the animated branch:
    # UPPERCASE text + multiple per-word dialogue events.
    tmpl = dict(CAPTION_TEMPLATES["bold_01"])
    sents = [{"text": "The quick brown", "start": 0.0, "end": 3.0}]
    words = estimate_word_timings(sents)
    assert words
    build_ass(sents, words, "/tmp/wt_fancy.ass", font_size=48,
              highlight=True, play_res=(1920, 1080), template=tmpl)
    ass = Path("/tmp/wt_fancy.ass").read_text(encoding="utf-8")
    assert "THE" in ass and "QUICK" in ass and "BROWN" in ass
    assert ass.count("Dialogue:") > 2, \
        "fancy branch did not run -- still simple!"


def test_no_words_still_simple():
    tmpl = dict(CAPTION_TEMPLATES["bold_01"])
    sents = [{"text": "The quick brown", "start": 0.0, "end": 3.0}]
    build_ass(sents, None, "/tmp/wt_simple.ass", font_size=48,
              highlight=True, play_res=(1920, 1080), template=tmpl)
    ass = Path("/tmp/wt_simple.ass").read_text(encoding="utf-8")
    assert ass.count("Dialogue:") == 1


def test_render_engine_wires_fallback():
    src = (ROOT / "src" / "render_engine.py").read_text(encoding="utf-8")
    assert "estimate_word_timings" in src
    assert "_est_wt(self.sentence_timings)" in src
