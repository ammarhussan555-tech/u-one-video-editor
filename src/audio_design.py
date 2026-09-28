"""Audio design: synthesized SFX library, music select/synth, ffmpeg mixing.

SFX are synthesized with numpy (no downloads needed). Music: user folder scan
by mood, else a generated ambient bed. Mixing via ffmpeg with sidechain
ducking + loudnorm so voice stays dominant and nothing clips.
"""
import os
import subprocess
import wave

import numpy as np

SR = 44100

# ---------------- synthesis ----------------

def _write_wav(path, data):
    data = np.clip(np.asarray(data, dtype=np.float64), -1, 1)
    pcm = (data * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _noise(dur, brown=False):
    n = int(SR * dur)
    x = np.random.default_rng(abs(hash((dur, brown))) % (2 ** 31)).standard_normal(n)
    if brown:
        x = np.cumsum(x)
        x = x / (np.max(np.abs(x)) + 1e-9)
    return x


def _env(n, attack=0.02, decay_end=0.001):
    a = max(1, int(n * attack))
    env = np.ones(n)
    env[:a] = np.linspace(0, 1, a)
    env[a:] = np.linspace(1, decay_end, n - a)
    return env


def sfx_whoosh(d=1.2):
    n = int(SR * d)
    x = _noise(d) * _env(n, 0.35, 0.05)
    sweep = np.sin(2 * np.pi * np.linspace(200, 2500, n) * np.arange(n) / SR)
    return 0.6 * x + 0.25 * sweep * _env(n, 0.3, 0.05)


def sfx_impact(d=1.0):
    n = int(SR * d)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 5)
    return body + 0.4 * _noise(d) * _env(n, 0.005, 0.001)


def sfx_boom(d=2.2):
    n = int(SR * d)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * 42 * t) * np.exp(-t * 2.2) + 0.35 * _noise(d, True) * _env(n, 0.01, 0.001)


def sfx_riser(d=1.6):
    n = int(SR * d)
    t = np.arange(n) / SR
    f = np.linspace(180, 1400, n)
    return 0.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.linspace(0.05, 1, n)


def sfx_alert(d=0.9):
    n = int(SR * d)
    t = np.arange(n) / SR
    sq = np.sign(np.sin(2 * np.pi * 880 * t)) * (t < d / 2) + np.sign(np.sin(2 * np.pi * 660 * t)) * (t >= d / 2)
    return 0.35 * sq * _env(n, 0.01, 0.2)


def sfx_thunder(d=2.6):
    return 0.8 * _noise(d, True) * _env(d and int(SR * d), 0.005, 0.001)


def sfx_wind(d=3.0):
    n = int(SR * d)
    t = np.arange(n) / SR
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.4 * t)
    return 0.5 * _noise(d) * lfo * _env(n, 0.2, 0.2)


def sfx_camera(d=0.35):
    n = int(SR * d)
    x = _noise(d)
    x[int(0.05 * SR):] *= 0.05
    return 0.7 * x * _env(n, 0.001, 0.001)


def sfx_digital(d=0.7):
    n = int(SR * d)
    t = np.arange(n) / SR
    blips = sum(np.sin(2 * np.pi * f * t) * ((t * 8).astype(int) % 2 == 0)
                for f in (1200, 1600, 2000))
    return 0.2 * blips * _env(n, 0.01, 0.05)


def sfx_crowd(d=3.0):
    n = int(SR * d)
    return 0.4 * _noise(d) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * np.arange(n) / SR))


SFX_BUILDERS = {
    "whoosh": sfx_whoosh, "impact": sfx_impact, "boom": sfx_boom,
    "riser": sfx_riser, "alert": sfx_alert, "thunder": sfx_thunder,
    "wind": sfx_wind, "camera": sfx_camera, "digital": sfx_digital,
    "crowd": sfx_crowd,
}

SFX_KEYWORDS = {
    "whoosh": ["suddenly", "fast", "launch", "fly", "transition", "speed"],
    "impact": ["crash", "collision", "hit", "blast"],
    "boom": ["explosion", "eruption", "erupt", "detonate", "blast"],
    "riser": ["tension", "rising", "approaching", "coming", "building"],
    "alert": ["warning", "alert", "breaking", "urgent", "alarm", "emergency"],
    "thunder": ["thunder", "lightning"],
    "wind": ["wind", "hurricane", "storm", "desert"],
    "crowd": ["crowd", "audience", "cheer", "protest", "supporters"],
    "camera": ["photo", "camera", "snapshot", "picture"],
    "digital": ["digital", "computer", "data", "robot", "signal", "ai"],
}


def ensure_sfx_library(sfx_dir):
    os.makedirs(sfx_dir, exist_ok=True)
    paths = {}
    for name, fn in SFX_BUILDERS.items():
        p = os.path.join(sfx_dir, f"{name}.wav")
        if not os.path.exists(p):
            _write_wav(p, fn())
        paths[name] = p
    return paths


def place_sfx(scene_analyses, timings, max_per_scene=1):
    """Contextual placement only: keyword match per scene, never on every scene."""
    events = []
    for sa, t in zip(scene_analyses, timings):
        text = (sa.text + " " + " ".join(sa.keywords)).lower()
        hits = []
        for sfx, words in SFX_KEYWORDS.items():
            if any(w in text for w in words):
                hits.append(sfx)
        for sfx in hits[:max_per_scene]:
            at = t["start"] + min(0.4, (t["end"] - t["start"]) * 0.2)
            events.append({"time": round(at, 3), "sfx": sfx, "gain_db": -10})
    return events


# ---------------- music ----------------

MOOD_FILE_HINTS = {
    "dramatic": ["dramatic", "epic", "trailer", "intense"],
    "suspense": ["suspense", "mystery", "dark", "tension"],
    "calm": ["calm", "ambient", "peaceful", "soft"],
    "emotional": ["emotional", "piano", "sad", "hope"],
    "scientific": ["science", "tech", "documentary", "minimal"],
    "news": ["news", "broadcast", "corporate", "uplifting"],
    "neutral": ["ambient", "background", "calm"],
}


def select_music(music_dir, mood):
    if not music_dir or not os.path.isdir(music_dir):
        return None
    files = [os.path.join(music_dir, f) for f in os.listdir(music_dir)
             if f.lower().endswith((".mp3", ".wav", ".ogg", ".m4a"))]
    if not files:
        return None
    hints = MOOD_FILE_HINTS.get(mood, MOOD_FILE_HINTS["neutral"])
    for f in files:
        if any(h in os.path.basename(f).lower() for h in hints):
            return f
    return sorted(files)[0]


def synth_ambient_bed(duration, path, mood="neutral"):
    """Generated royalty-free ambient bed when the user has no music folder."""
    n = int(SR * duration)
    t = np.arange(n) / SR
    freqs = [110, 164.8, 220, 277.2] if mood in ("calm", "emotional", "neutral") else [98, 146.8, 196, 246.9]
    bed = sum(0.12 * np.sin(2 * np.pi * f * t + p) for f, p in zip(freqs, (0, 1.3, 2.1, 0.7)))
    trem = 0.7 + 0.3 * np.sin(2 * np.pi * 0.15 * t)
    bed = bed * trem * _env(n, 0.05, 0.05)
    _write_wav(path, bed * 0.5)
    return path


# ---------------- mixing ----------------

def mix_audio(voice_path, music_path, sfx_events, sfx_library, out_path,
              total_dur, ffmpeg="ffmpeg"):
    """Voice dominant: music ducked via sidechain, sfx tucked under, loudnorm + limiter."""
    D = round(total_dur, 2)
    inputs, idx = ["-i", voice_path], 1
    if music_path:
        inputs += ["-i", music_path]
        music_idx = idx
        idx += 1
    else:
        music_idx = None
    sfx_inputs = []
    for ev in sfx_events:
        p = sfx_library.get(ev["sfx"])
        if p and os.path.exists(p):
            inputs += ["-i", p]
            sfx_inputs.append((idx, ev))
            idx += 1

    fc = ["[0:a]aresample=44100,aformat=channel_layouts=stereo,asplit=2[vkey][vmix]"]
    mix_ins = ["[vmix]"]
    if music_idx is not None:
        fc.append(
            f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"volume=0.25,aloop=loop=-1:size=2147483647,atrim=duration={D}[bg]")
        fc.append("[bg][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=500[mduck]")
        mix_ins.append("[mduck]")
    for i, (si, ev) in enumerate(sfx_inputs):
        ms = int(ev["time"] * 1000)
        fc.append(f"[{si}:a]aresample=44100,aformat=channel_layouts=stereo,"
                  f"adelay={ms}|{ms},volume={ev.get('gain_db', -10)}dB[s{i}]")
        mix_ins.append(f"[s{i}]")
    n = len(mix_ins)
    fc.append(f"{''.join(mix_ins)}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0,"
              f"loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95,atrim=duration={D}[aout]")
    cmd = [*inputs, "-filter_complex", ";".join(fc),
           "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", out_path]
    from src.ffmpeg_util import run_ffmpeg
    try:
        run_ffmpeg(cmd, stage="audio_mix", timeout=900)
    except Exception as e:  # noqa: BLE001 - keep the stage context, simplify the message
        raise RuntimeError(
            "U One could not mix the audio track. Your project has been "
            "saved - details are in the log.") from e
    return out_path
