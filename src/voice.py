"""Voiceover: upload existing audio OR generate AI voice; sentence timing.

Timing chain (best effort):
  1. faster-whisper word timestamps (if installed) -> aligned to sentences
  2. edge-tts word boundaries (when voice was generated in-app)
  3. speech-activity-aware proportional fallback: an energy VAD finds the
     actual speech regions, and sentences/words are distributed over
     SPEECH time (not wall-clock). This removes the progressive drift
     (captions a phrase ahead/behind the voice) that pure proportional
     timing suffers on real recordings with leading silence and pauses.
"""
import asyncio
import os
import subprocess
import wave


# ---------------------------------------------------------------------------
# Speech-activity detection (energy VAD) for timing estimation.
# Used when no measured word timings exist (uploaded voiceover / pyttsx3,
# faster-whisper not bundled). Pure proportional timing assumes uniform
# speech from t=0; real recordings have leading/trailing silence and
# inter-sentence pauses, which makes the caption highlight drift further
# and further from the voice ("aage ya peeche"). Mapping the proportional
# shares onto SPEECH-active time instead of wall-clock removes that drift.
# ---------------------------------------------------------------------------
_speech_cache = {}


def _speech_intervals(audio_path):
    """Return [(start, end)] seconds of speech-active audio.

    Energy VAD: decode to mono 16kHz via ffmpeg, 30ms frames, adaptive
    threshold ~9dB above the noise floor, gaps < 300ms merged, blips
    < 150ms dropped. Returns [] when speech cannot be determined (then
    callers fall back to plain proportional timing). Results are cached
    per file (path + mtime + size). Never raises.
    """
    try:
        st = os.stat(audio_path)
        ckey = (str(audio_path), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if ckey in _speech_cache:
        return _speech_cache[ckey]
    intervals = []
    try:
        import numpy as np
        from src.ffmpeg_util import find_ffmpeg, _no_window_kwargs
        ff = find_ffmpeg()
        if not ff:
            return []
        p = subprocess.run(
            [ff, "-v", "error", "-i", str(audio_path),
             "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
            capture_output=True, timeout=180, **_no_window_kwargs())
        raw = p.stdout
        if not raw or len(raw) < 3200:
            return []
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        sr = 16000
        fw, hop = int(sr * 0.030), int(sr * 0.010)
        n_frames = 1 + (len(samples) - fw) // hop
        if n_frames < 10:
            return []
        # frame energy via stride trick
        shape = (n_frames, fw)
        strides = (samples.strides[0] * hop, samples.strides[0])
        frames = np.lib.stride_tricks.as_strided(samples, shape, strides)
        energy = (frames ** 2).mean(axis=1)
        noise = np.percentile(energy, 10)
        thr = max(noise * 8.0, 1e-8)  # ~9 dB above the noise floor
        active = energy > thr
        # merge gaps < 300ms (30 frames), drop segments < 150ms (15 frames)
        idx = np.flatnonzero(active)
        if idx.size:
            bounds, s0, prev = [], idx[0], idx[0]
            for i in idx[1:]:
                if i - prev > 30:
                    bounds.append((s0, prev))
                    s0 = i
                prev = i
            bounds.append((s0, prev))
            for a, b in bounds:
                if b - a + 1 >= 15:
                    intervals.append((a * hop / sr, (b * hop + fw) / sr))
    except Exception:  # noqa: BLE001
        intervals = []
    _speech_cache[ckey] = intervals
    return intervals


def _distribute_over_speech(parts, weights, span_start, span_end, intervals):
    """Assign each part a (start, end) inside [span_start, span_end].

    Each part's share of the SPEECH-active time inside the span is
    proportional to its weight; shares are mapped back to wall-clock by
    walking the active intervals. With no usable speech intervals this
    degrades gracefully to plain wall-clock proportional timing.
    """
    n = len(parts)
    weights = [max(float(w), 0.01) for w in weights]
    total_w = sum(weights) or 1.0
    # clip intervals to the span
    segs = []
    for a, b in intervals or []:
        a2, b2 = max(a, span_start), min(b, span_end)
        if b2 - a2 > 0.02:
            segs.append((a2, b2))
    total_active = sum(b - a for a, b in segs)
    out = []
    if total_active < 0.05 or n == 0:
        # fallback: uniform wall-clock proportional
        dur = max(span_end - span_start, 0.01)
        t = span_start
        for i, w in enumerate(weights):
            e = span_end if i == n - 1 else t + dur * w / total_w
            out.append((round(t, 3), round(e, 3)))
            t = e
        return out
    # walk the active timeline
    bounds = []  # (active_offset_start, wall_start, wall_end)
    acc = 0.0
    for a, b in segs:
        bounds.append((acc, a, b))
        acc += b - a

    def wall_at(off):
        off = min(max(off, 0.0), total_active)
        for aoff, a, b in bounds:
            if off <= aoff + (b - a) + 1e-9:
                return a + min(off - aoff, b - a)
        return bounds[-1][2]

    t_act = 0.0
    for i, w in enumerate(weights):
        share = total_active * w / total_w
        s = wall_at(t_act)
        t_act += share
        e = wall_at(t_act) if i < n - 1 else wall_at(total_active)
        # never let a part end before it starts; keep tiny minimum width
        e = max(e, s + 0.02)
        out.append((round(s, 3), round(e, 3)))
    return out


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


def synthesize_speech(text, out_wav, voice="en-US-AriaNeural", log=None):
    """Returns (audio_path, word_timings|None). edge-tts first, pyttsx3 (Windows SAPI) fallback.

    log: optional callable(str) for render-log messages (e.g. WHY edge-tts
    was skipped -- Uzair 2026-10-04: the fallback reason was invisible).
    """
    def _log(m):
        if log:
            try:
                log(m)
            except Exception:  # noqa: BLE001 - logging must never break TTS
                pass

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
        _log(f"edge-tts unavailable ({e}); using the Windows system voice "
             f"(no word timings -- they will be measured afterwards).")
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


def get_sentence_timings(audio_path, sentences, tts_word_timings=None, log=None):
    """Sentence timings, measured when possible.

    Priority: TTS word boundaries (exact) -> Whisper measurement (exact,
    needs the faster-whisper package + a one-time ~75MB model download) ->
    speech-activity estimate (approximate). Uzair 2026-10-04: estimated
    timings made visuals/main-text drift ahead/behind the voice, so the
    Whisper path is now bundled and its failures are logged LOUDLY instead
    of silently falling back.
    """
    def _log(m):
        if log:
            try:
                log(m)
            except Exception:  # noqa: BLE001
                pass

    if tts_word_timings:
        try:
            return _align_words_to_sentences(sentences, tts_word_timings)
        except Exception:  # noqa: BLE001
            pass
    try:
        from faster_whisper import WhisperModel
        _log("Measuring speech timings (Whisper; one-time ~75MB model "
             "download on first use)...")
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, word_timestamps=True)
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append({"word": w.word, "start": w.start, "end": w.end})
        if words:
            _log(f"Speech timings measured ({len(words)} words).")
            return _align_words_to_sentences(sentences, words)
        _log("WARNING: Whisper returned no words; using estimated timings "
             "(visuals/main text may drift from the voice).")
    except Exception as e:  # noqa: BLE001
        _log(f"WARNING: Whisper measurement failed ({e}); using estimated "
             f"timings (visuals/main text may drift from the voice).")
    dur = audio_duration(audio_path) or max(1.0, sum(len(s) for s in sentences) * 0.06)
    # Speech-aware fallback: distribute over SPEECH-active time so leading
    # silence and inter-sentence pauses don't push captions ahead/behind
    # the voice (Uzair 2026-10-03: "word match nahi ho raha, aage/peeche").
    intervals = _speech_intervals(audio_path)
    weights = [len(s) for s in sentences]
    spans = _distribute_over_speech(sentences, weights, 0.0, dur, intervals)
    return [{"text": s, "start": a, "end": b}
            for s, (a, b) in zip(sentences, spans)]


def estimate_word_timings(sentence_timings, audio_path=None):
    """Deterministic fallback word timings when Whisper is unavailable.

    faster-whisper is bundled with U One, so this fallback only runs when
    the Whisper measurement itself fails (e.g. the one-time model download
    was blocked). Without word timings, build_ass() silently renders every
    preset as SIMPLE static captions -- whichever style the user picked.

    When audio_path is given, words are distributed over the SPEECH-active
    time inside each sentence (energy VAD), so the active-word highlight
    tracks the voice through leading silence and pauses instead of
    drifting a phrase ahead/behind. Without it, falls back to plain
    proportional distribution by word length (mirroring
    get_sentence_timings()' long-standing proportional fallback).

    These are ESTIMATES, not measured timings; Whisper remains the
    accurate path when installed. Returns a list of {word, start, end}
    or None if there is nothing to distribute.
    """
    intervals = _speech_intervals(audio_path) if audio_path else []
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
        spans = _distribute_over_speech(parts, weights, start, end,
                                        intervals)
        for w, (ws, we) in zip(parts, spans):
            words.append({"word": w, "start": ws, "end": we})
    return words or None


def whisper_word_timings(audio_path, log=None):
    """Best-effort word-level timings via faster-whisper.

    Used when the TTS engine did not provide word boundaries (uploaded
    voiceover, or pyttsx3 fallback) so captions still get CapCut-style
    word-by-word animation instead of static lines.
    Returns a list of {word, start, end} or None on any failure.
    Failures are logged via `log` (Uzair 2026-10-04: silent fallback hid
    why timings were estimated).
    """
    def _log(m):
        if log:
            try:
                log(m)
            except Exception:  # noqa: BLE001
                pass

    try:
        from faster_whisper import WhisperModel
        _log("Measuring word timings (Whisper; one-time ~75MB model "
             "download on first use)...")
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, word_timestamps=True)
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append({"word": w.word.strip(),
                              "start": round(w.start, 3),
                              "end": round(w.end, 3)})
        if words:
            _log(f"Word timings measured ({len(words)} words).")
            return words
        _log("WARNING: Whisper returned no words; using estimated timings.")
        return None
    except Exception as e:  # noqa: BLE001
        _log(f"WARNING: Whisper word measurement failed ({e}); using "
             f"estimated timings.")
        return None
