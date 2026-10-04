"""Uzair 2026-10-04: voice/visual + main-text sync fix.

Root cause: when the TTS engine gave no word boundaries (Gemini fallback /
pyttsx3), every timing in the render was ESTIMATED, so visuals and main text
drifted ahead/behind the voice. Fix: (1) faster-whisper is now bundled so
timings are MEASURED; its failures are logged loudly instead of silently
falling back; (2) main text appears EXACTLY when its hero word is spoken
(lead 0.0 -- "na aaga na peeche").
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_mt_lead_all_zero():
    from src.text_captions import _MT_LEAD
    assert _MT_LEAD, "lead table must not be empty"
    assert all(v == 0.0 for v in _MT_LEAD.values()), _MT_LEAD


def test_main_text_appears_exactly_at_hero_word():
    from src.text_captions import select_main_text
    text = "Imran Khan announced a massive protest in Lahore today"
    wt = [{"word": w, "start": 0.5 + i * 0.4, "end": 0.8 + i * 0.4}
          for i, w in enumerate(text.split())]
    cards = select_main_text(text, duration=6.0, word_timings=wt, context={})
    assert cards, "expected at least one card"
    for c in cards:
        hero = (c["hero_word"] or "").strip(".,!?\"'").lower()
        ht = next((w["start"] for w in wt
                   if w["word"].strip(".,!?\"'").lower() == hero), None)
        if ht is not None:
            assert abs(c["appear"] - ht) < 1e-6, (c["appear"], ht)


def test_timing_functions_accept_log_and_fallback_loudly():
    from src.voice import get_sentence_timings, whisper_word_timings
    msgs = []
    st = get_sentence_timings("/nonexistent.wav",
                              ["Hello world.", "Second line."],
                              None, log=msgs.append)
    assert len(st) == 2
    assert any("Whisper" in m for m in msgs), msgs  # failure is LOUD now
    msgs2 = []
    assert whisper_word_timings("/nonexistent.wav", log=msgs2.append) is None
    assert any("Whisper" in m for m in msgs2), msgs2


def test_estimator_still_works():
    from src.voice import estimate_word_timings
    ew = estimate_word_timings(
        [{"text": "hello world", "start": 1.0, "end": 3.0}])
    assert ew and len(ew) == 2
    assert ew[0]["start"] >= 1.0 and ew[-1]["end"] <= 3.0
