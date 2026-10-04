"""Google AI Studio (Gemini) TTS voiceover provider (Uzair 2026-10-04).

Free API key from aistudio.google.com -- no card, no paid tier.
All 30 prebuilt voices, each with its documented style.

Endpoint (verified Oct 2026):
    POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
    auth:  x-goog-api-key header (or ?key= query param)
    body:  {"contents":[{"parts":[{"text": "..."}]}],
            "generationConfig":{"responseModalities":["AUDIO"],
             "speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":"Kore"}}}}}
Response: base64 PCM signed 16-bit, 24000 Hz, mono. We wrap it in a WAV
header, then convert with the bundled ffmpeg to 48 kHz stereo -- the same
shape the rest of the voiceover stage expects.

Limits handled in code:
- ~32K-token TTS context  -> chunk the script (~4000 chars, split on
  sentence boundaries), synthesize each chunk, concatenate.
- transient failures       -> retry once per chunk.
- free-tier daily quota    -> HTTP 429 / quota errors raise TTSQuotaError
  so the caller FALLS BACK to the default TTS engine (never raises past
  the render, never blocks it).
"""
from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from .script_analyzer import split_sentences

# (voice name, style) -- all 30 prebuilt Gemini TTS voices.
VOICES = [
    ("Zephyr", "Bright"),
    ("Puck", "Upbeat"),
    ("Charon", "Informative"),
    ("Kore", "Firm"),
    ("Fenrir", "Excitable"),
    ("Leda", "Youthful"),
    ("Orus", "Firm"),
    ("Aoede", "Breezy"),
    ("Callirrhoe", "Easy-going"),
    ("Autonoe", "Bright"),
    ("Enceladus", "Breathy"),
    ("Iapetus", "Clear"),
    ("Umbriel", "Easy-going"),
    ("Algieba", "Smooth"),
    ("Despina", "Smooth"),
    ("Erinome", "Clear"),
    ("Algenib", "Gravelly"),
    ("Rasalgethi", "Informative"),
    ("Laomedeia", "Upbeat"),
    ("Achernar", "Soft"),
    ("Alnilam", "Firm"),
    ("Schedar", "Even"),
    ("Gacrux", "Mature"),
    ("Pulcherrima", "Forward"),
    ("Achird", "Friendly"),
    ("Zubenelgenubi", "Casual"),
    ("Vindemiatrix", "Gentle"),
    ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"),
    ("Sulafat", "Warm"),
]

MODELS = (
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-tts-preview",
)
DEFAULT_MODEL = MODELS[0]
DEFAULT_VOICE = "Kore"

_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
             "{model}:generateContent")
_SAMPLE_RATE = 24000
_CHUNK_CHARS = 4000


class TTSQuotaError(Exception):
    """Free-tier quota exhausted (HTTP 429 / quota message)."""


class TTSError(Exception):
    """Non-quota Gemini TTS failure."""


def list_voices():
    """[(name, style)] for all 30 prebuilt voices."""
    return list(VOICES)


def voice_names():
    return [n for n, _ in VOICES]


def chunk_text(text, max_chars=_CHUNK_CHARS):
    """Split on sentence boundaries into <= max_chars chunks."""
    sents = split_sentences(text or "")
    chunks, cur = [], ""
    for s in sents:
        if len(cur) + len(s) + 1 > max_chars and cur:
            chunks.append(cur.strip())
            cur = s
        else:
            cur = (cur + " " + s).strip() if cur else s
    if cur.strip():
        chunks.append(cur.strip())
    return chunks or [text or ""]


def _request_pcm(text, voice, api_key, model, timeout):
    body = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}}}}}).encode()
    url = _ENDPOINT.format(model=model)
    last = None
    for attempt in (1, 2):  # retry once on transient failure
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": api_key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as e:
            last = e
            try:
                detail = e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                detail = ""
            if e.code == 429 or "quota" in detail.lower() \
                    or "RESOURCE_EXHAUSTED" in detail:
                raise TTSQuotaError(
                    f"Gemini TTS quota exhausted (HTTP {e.code}). "
                    f"Free-tier daily limit -- falling back to default TTS.") from e
            if attempt == 2:
                raise TTSError(f"Gemini TTS HTTP {e.code}: {detail[:200]}") from e
            time.sleep(2)
        except Exception as e:  # noqa: BLE001 - network etc.
            last = e
            if attempt == 2:
                raise TTSError(f"Gemini TTS request failed: {e}") from e
            time.sleep(2)
    else:  # pragma: no cover - defensive
        raise TTSError(f"Gemini TTS request failed: {last}")

    pcm = bytearray()
    try:
        for cand in payload.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or {}
                if inline.get("data"):
                    pcm.extend(base64.b64decode(inline["data"]))
    except Exception as e:  # noqa: BLE001
        raise TTSError(f"Could not decode Gemini TTS audio: {e}") from e
    if not pcm:
        raise TTSError("Gemini TTS returned no audio data.")
    return bytes(pcm)


def _write_wav(path, pcm_bytes, sample_rate=_SAMPLE_RATE):
    n = len(pcm_bytes) // 2
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(pcm_bytes)))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate,
                            sample_rate * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", len(pcm_bytes)))
        f.write(pcm_bytes)
    return path


def _to_final_wav(raw_wav, out_wav):
    """Convert raw 24k mono WAV to the 48k stereo WAV the pipeline expects."""
    from .ffmpeg_util import find_ffmpeg, run_no_window
    ff = find_ffmpeg()
    tmp = out_wav + ".tmp.wav"
    r = run_no_window(
        [ff, "-y", "-v", "error", "-i", raw_wav,
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", tmp],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not os.path.isfile(tmp):
        raise TTSError(f"ffmpeg voice convert failed: {r.stderr[:200]}")
    os.replace(tmp, out_wav)
    return out_wav


def synthesize(text, voice=DEFAULT_VOICE, api_key="", model=DEFAULT_MODEL,
               out_wav="", timeout=120):
    """Full script -> final WAV. Chunks, concatenates, retries once per
    chunk. Raises TTSQuotaError on quota exhaustion (caller falls back),
    TTSError on other failures."""
    if not (api_key or "").strip():
        raise TTSError("No Gemini API key provided.")
    if voice not in voice_names():
        raise TTSError(f"Unknown Gemini voice: {voice!r}.")
    chunks = chunk_text(text)
    pcm_all = bytearray()
    for i, ch in enumerate(chunks):
        pcm_all.extend(_request_pcm(ch, voice, api_key.strip(), model, timeout))
    fd, raw = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        _write_wav(raw, bytes(pcm_all))
        return _to_final_wav(raw, out_wav or raw.replace(".wav", "_final.wav"))
    finally:
        try:
            os.remove(raw)
        except OSError:  # noqa: BLE001
            pass
