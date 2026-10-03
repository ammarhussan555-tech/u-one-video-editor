"""Speech-aware caption timing (Uzair 2026-10-03).

"caption jo voice main word bol ja rha ha wo nhi chal rha, ya to wo aage
1 line chal rha ya wo peeche" -- with no measured word timings (uploaded
voiceover, faster-whisper unbundled), pure proportional timing assumes
uniform speech from t=0. Real recordings have leading silence and pauses,
so the highlight drifted progressively from the voice. Sentences/words
are now distributed over SPEECH-active time (energy VAD).
"""
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.voice import (_distribute_over_speech, _speech_intervals,
                       estimate_word_timings, get_sentence_timings)


def _make_wav(path, segments, sr=16000, total=6.5):
    """segments: [(start, end)] of 'speech' (loud tone); rest is silence."""
    t = np.arange(0, total, 1 / sr)
    sig = np.zeros_like(t)
    for a, b in segments:
        m = (t >= a) & (t < b)
        sig[m] = 0.5 * np.sin(2 * np.pi * 220 * t[m])
    sig = (sig * 30000).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(sig.tobytes())


@pytest.fixture()
def speech_wav(tmp_path):
    p = str(tmp_path / "vad.wav")
    # 1.5s leading silence, speech 1.5-3.5, 1s pause, speech 4.5-6.0
    _make_wav(p, [(1.5, 3.5), (4.5, 6.0)])
    return p


def test_vad_finds_speech(speech_wav):
    iv = _speech_intervals(speech_wav)
    assert len(iv) == 2
    assert abs(iv[0][0] - 1.5) < 0.15 and abs(iv[0][1] - 3.5) < 0.15
    assert abs(iv[1][0] - 4.5) < 0.15 and abs(iv[1][1] - 6.0) < 0.15


def test_vad_missing_file_returns_empty():
    assert _speech_intervals("/nonexistent/xyz.wav") == []


def test_distribute_no_intervals_falls_back_proportional():
    spans = _distribute_over_speech(["aa", "bbbb"], [2, 4], 0.0, 6.0, [])
    # 2:4 split of 6s -> (0,2), (2,6)
    assert spans[0] == (0.0, 2.0)
    assert spans[1] == (2.0, 6.0)


def test_distribute_skips_leading_silence(speech_wav):
    iv = _speech_intervals(speech_wav)
    spans = _distribute_over_speech(["hello world", "foo bar"],
                                    [11, 7], 0.0, 6.5, iv)
    # first sentence must NOT start at t=0 (the old drift bug)
    assert spans[0][0] >= 1.3, spans
    # both sentences inside speech regions
    assert spans[0][1] <= spans[1][0] + 0.01
    assert spans[1][1] <= 6.2


def test_sentence_timings_speech_aligned(speech_wav):
    sents = get_sentence_timings(speech_wav, ["hello world", "foo bar"])
    assert len(sents) == 2
    # old code started sentence 1 at t=0 despite 1.5s of silence
    assert sents[0]["start"] >= 1.3, sents[0]
    assert sents[0]["end"] <= sents[1]["start"] + 0.01
    assert sents[1]["end"] <= 6.2


def test_estimate_word_timings_backward_compatible():
    # no audio_path -> old plain proportional behavior (weights len+1:
    # "AA"->3, "BBBB"->5, so 3/8*6=2.25 split)
    sents = [{"text": "AA BBBB", "start": 0.0, "end": 6.0}]
    words = estimate_word_timings(sents)
    assert [(w["word"], w["start"], w["end"]) for w in words] == [
        ("AA", 0.0, 2.25), ("BBBB", 2.25, 6.0)]


def test_estimate_word_timings_speech_aligned(speech_wav):
    sents = [{"text": "AA BBBB", "start": 0.0, "end": 6.5}]
    words = estimate_word_timings(sents, speech_wav)
    assert words[0]["start"] >= 1.3, words[0]
    # words stay ordered and inside the sentence span
    assert words[0]["end"] <= words[1]["start"] + 0.01
    assert words[1]["end"] <= 6.5
