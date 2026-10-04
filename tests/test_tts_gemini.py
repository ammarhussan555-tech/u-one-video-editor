"""Google AI Studio (Gemini) TTS provider tests. No real API calls: the HTTP
layer is mocked."""
import base64
import json
import os
import struct
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import tts_gemini as tg


def test_voice_list_has_30():
    voices = tg.list_voices()
    assert len(voices) == 30, f"expected 30 voices, got {len(voices)}"
    names = [n for n, _ in voices]
    assert len(set(names)) == 30, "voice names must be unique"
    for n, s in voices:
        assert n and s, "each voice needs (name, style)"
    assert ("Kore", "Firm") in voices


def test_chunking_sentence_boundaries():
    text = " ".join(f"Sentence number {i} about world news." for i in range(200))
    chunks = tg.chunk_text(text, max_chars=4000)
    assert len(chunks) > 1, "long text must be split"
    assert all(len(c) <= 4000 for c in chunks), "no chunk may exceed the limit"
    # Nothing lost.
    assert " ".join(chunks).split() == text.split()


def test_chunking_short_text_single():
    assert tg.chunk_text("Hello world.") == ["Hello world."]


def _fake_response(pcm_bytes):
    payload = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000",
                        "data": base64.b64encode(pcm_bytes).decode()}}]}}]}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return FakeResp()


def _pcm(seconds=1):
    n = 24000 * seconds
    return struct.pack("<" + "h" * n, *([1000] * n))


def test_synthesize_mocked_writes_wav(tmp_path):
    pcm = _pcm(1)
    orig = tg.urllib.request.urlopen
    tg.urllib.request.urlopen = lambda req, timeout=None: _fake_response(pcm)
    try:
        out = str(tmp_path / "voice.wav")
        got = tg.synthesize("Hello world.", voice="Kore", api_key="fake",
                            out_wav=out, timeout=10)
        assert os.path.isfile(got)
        with open(got, "rb") as f:
            head = f.read(44)
        assert head[:4] == b"RIFF" and head[8:12] == b"WAVE"
        sr = struct.unpack("<I", head[24:28])[0]
        ch = struct.unpack("<H", head[22:24])[0]
        assert sr == 48000 and ch == 2, "pipeline expects 48k stereo"
    finally:
        tg.urllib.request.urlopen = orig


def test_quota_error_on_429():
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                     {}, None)

    orig = tg.urllib.request.urlopen
    tg.urllib.request.urlopen = boom
    try:
        try:
            tg.synthesize("Hello.", voice="Kore", api_key="fake",
                          out_wav="/tmp/never.wav", timeout=10)
            assert False, "expected TTSQuotaError"
        except tg.TTSQuotaError:
            pass
    finally:
        tg.urllib.request.urlopen = orig


def test_no_key_raises():
    try:
        tg.synthesize("Hello.", voice="Kore", api_key="",
                      out_wav="/tmp/never.wav")
        assert False, "expected TTSError"
    except tg.TTSError:
        pass


def test_bad_voice_raises():
    try:
        tg.synthesize("Hello.", voice="Nobody", api_key="fake",
                      out_wav="/tmp/never.wav")
        assert False, "expected TTSError"
    except tg.TTSError:
        pass
