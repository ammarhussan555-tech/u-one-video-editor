"""Voiceover: upload existing audio OR generate AI voice; sentence timing.

Timing chain (best effort):
  1. faster-whisper word timestamps (if installed) -> aligned to sentences
  2. edge-tts word boundaries (when voice was generated in-app)
  3. proportional fallback by character length
"""
import asyncio
import os
import subprocess
import wave


def audio_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        pass
    try:
        from mutagen import File as MFile
        f = MFile(path)
        if f is not None and getattr(f, "info", None) and getattr(f.info, "length", None):
            return float(f.info.length)
    except Exception:
        pass
    try:
        from src.ffmpeg_util import find_ffmpeg, find_ffprobe, run_no_window
        _probe = find_ffprobe(find_ffmpeg())
        if _probe:
            r = run_no_window(
                [_probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=15)
            return float(r.stdout.strip())
    except Exception:
        pass
    return 0.0


def synthesize_speech(text, out_wav, voice="en-US-AriaNeural"):
    """Returns (audio_path, word_timings|None). edge-tts first, pyttsx3 (Windows SAPI) fallback."""
    err1 = None
    try:
        import edge_tts

        async def _run():
            bounds = []
            comm = edge_tts.Communicate(text, voice)
            with open(out_wav, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        bounds.append({
                            "word": chunk["text"],
                            "start": chunk["offset"] / 1e7,
                            "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                        })
            return bounds

        bounds = asyncio.run(_run())
        return out_wav, bounds
    except Exception as e:  # noqa: BLE001
        err1 = e
    try:
        import pyttsx3
        eng = pyttsx3.init()
        eng.save_to_file(text, out_wav)
        eng.runAndWait()
        return out_wav, None
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"TTS failed (edge-tts: {err1}; pyttsx3: {e}). "
            "Install edge-tts (needs internet) or pyttsx3.") from e


def _align_words_to_sentences(sentences, words):
    out, wi, t = [], 0, 0.0
    for s in sentences:
        sw = [w for w in s.split()]
        seg = words[wi:wi + len(sw)] if wi < len(words) else []
        if seg:
            start, end = seg[0]["start"], seg[-1]["end"]
            wi += len(seg)
            t = end
        else:
            start, end = t, t + max(0.5, len(s) * 0.06)
            t = end
        out.append({"text": s, "start": round(start, 3), "end": round(end, 3)})
    return out


def get_sentence_timings(audio_path, sentences, tts_word_timings=None):
    if tts_word_timings:
        try:
            return _align_words_to_sentences(sentences, tts_word_timings)
        except Exception:  # noqa: BLE001
            pass
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, word_timestamps=True)
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append({"word": w.word, "start": w.start, "end": w.end})
        if words:
            return _align_words_to_sentences(sentences, words)
    except Exception:  # noqa: BLE001
        pass
    dur = audio_duration(audio_path) or max(1.0, sum(len(s) for s in sentences) * 0.06)
    total_chars = sum(len(s) for s in sentences) or 1
    out, t = [], 0.0
    for s in sentences:
        d = dur * len(s) / total_chars
        out.append({"text": s, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def estimate_word_timings(sentence_timings):
    """Deterministic fallback word timings when Whisper is unavailable.

    faster-whisper is an optional (unbundled) dependency, so uploaded
    voiceovers often have no measured word boundaries. Without word
    timings, build_ass() silently renders every preset as SIMPLE static
    captions -- whichever style the user picked. This distributes each
    sentence's duration across its words proportionally to word length
    (mirroring get_sentence_timings()' long-standing proportional
    fallback), so the active-word highlight stays approximately synced
    and all 120 presets animate. These are ESTIMATES, not measured
    timings; Whisper remains the accurate path when installed.
    Returns a list of {word, start, end} or None if there is nothing
    to distribute.
    """
    words = []
    for s in sentence_timings or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        parts = text.split()
        if not parts:
            continue
        start = float(s.get("start", 0.0))
        end = float(s.get("end", start))
        if end <= start:
            end = start + max(0.4 * len(parts), 0.5)
        weights = [len(w) + 1 for w in parts]  # +1 for the trailing space
        total = sum(weights) or 1
        dur = end - start
        t = start
        for i, w in enumerate(parts):
            w_end = end if i == len(parts) - 1 else t + dur * weights[i] / total
            words.append({"word": w, "start": round(t, 3),
                          "end": round(w_end, 3)})
            t += dur * weights[i] / total
    return words or None


def whisper_word_timings(audio_path):
    """Best-effort word-level timings via faster-whisper.

    Used when the TTS engine did not provide word boundaries (uploaded
    voiceover, or pyttsx3 fallback) so captions still get CapCut-style
    word-by-word animation instead of static lines.
    Returns a list of {word, start, end} or None on any failure.
    """
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, word_timestamps=True)
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append({"word": w.word.strip(),
                              "start": round(w.start, 3),
                              "end": round(w.end, 3)})
        return words or None
    except Exception:  # noqa: BLE001
        return None
