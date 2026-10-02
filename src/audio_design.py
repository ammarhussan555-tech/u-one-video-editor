"""Audio design: synthesized SFX library, music select/synth, ffmpeg mixing.

SFX are synthesized with numpy (no downloads needed). Music: user folder scan
by mood, else a generated ambient bed. Mixing via ffmpeg with sidechain
ducking + loudnorm so voice stays dominant and nothing clips.
"""
import os
import re
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


def _lowpass(x, cutoff_hz=1200.0):
    """One-pole lowpass — kills harsh digital hiss, keeps airy body."""
    a = 1.0 - np.exp(-2.0 * np.pi * cutoff_hz / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += a * (v - acc)
        y[i] = acc
    return y


def _raised_cosine(n, peak_pos=0.55):
    """Smooth swell envelope: gentle attack, natural decay, no clicks."""
    t = np.linspace(0, 1, n)
    env = np.where(t <= peak_pos,
                   0.5 - 0.5 * np.cos(np.pi * t / peak_pos),
                   0.5 + 0.5 * np.cos(np.pi * (t - peak_pos) / (1 - peak_pos)))
    return env


def sfx_whoosh(d=0.45):
    """Soft airy transition sweep — subtle by design.

    The old 1.2s raw-noise whoosh sounded harsh/synthetic ("ganda").
    Real creator videos use a barely-noticeable airy sweep under the cut:
    short, lowpassed (no hiss), smooth swell, quiet in the mix.
    """
    n = int(SR * d)
    x = _lowpass(_noise(d), cutoff_hz=1400.0)
    x = x / (np.max(np.abs(x)) + 1e-9)
    return 0.5 * x * _raised_cosine(n)


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


# ---- variety pack (2026-10-01): short UI-style sounds for caption
# entrances and headline hits. CapCut-style videos put a tiny pop/tick
# under each animated caption line instead of one big effect per scene.
def sfx_pop(d=0.18):
    """Punchy little pop for word pop / bounce entrances."""
    n = int(SR * d)
    t = np.arange(n) / SR
    f = np.linspace(520, 170, n)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22)
    return 0.8 * tone + 0.15 * _noise(d) * _env(n, 0.002, 0.001)


def sfx_ding(d=0.8):
    """Bright bell chime for breaking-news / headline hits."""
    n = int(SR * d)
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * 1568 * t) * 0.5
            + np.sin(2 * np.pi * 2093 * t) * 0.3
            + np.sin(2 * np.pi * 2637 * t) * 0.2) * np.exp(-t * 5)
    return 0.55 * tone


def sfx_tick(d=0.06):
    """Short click for typewriter caption chunks."""
    n = int(SR * d)
    x = _noise(d)
    x = np.convolve(x, np.ones(max(1, n // 8)) / max(1, n // 8),
                    mode="same")
    return 0.6 * x * _env(n, 0.001, 0.001)


def sfx_sparkle(d=0.5):
    """Shimmer for glow / aesthetic caption moments."""
    n = int(SR * d)
    t = np.arange(n) / SR
    f = np.linspace(3000, 6500, n)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
    tone += 0.5 * np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR) * np.exp(-t * 9)
    return 0.35 * tone


def sfx_punch(d=0.35):
    """Heavy thud for slam-in impact words and breaking titles."""
    n = int(SR * d)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * 68 * t) * np.exp(-t * 14)
    snap = _noise(d) * _env(n, 0.001, 0.001)
    return 0.9 * body + 0.35 * snap


def sfx_swoosh(d=0.5):
    """Soft quick swoosh for slide entrances (lighter than whoosh)."""
    n = int(SR * d)
    x = _noise(d) * _env(n, 0.45, 0.08)
    sweep = np.sin(2 * np.pi * np.linspace(400, 3200, n) * np.arange(n) / SR)
    return 0.45 * x + 0.18 * sweep * _env(n, 0.4, 0.08)


def sfx_glass_break(d=0.8):
    """Glass shatter: bright noise burst + detuned metallic pings."""
    n = int(SR * d)
    t = np.arange(n) / SR
    burst = _noise(d) * _env(n, 0.001, 0.001)
    # crude highpass via diff to keep only the bright crash
    burst = np.diff(burst, prepend=0.0)
    pings = np.zeros(n)
    rng = np.random.default_rng(7)
    for f in (2093, 2637, 3136, 3520, 4186):
        ph = rng.random() * 2 * np.pi
        pings += np.sin(2 * np.pi * f * t + ph) * np.exp(-t * (9 + rng.random() * 6))
    pings *= _env(n, 0.001, 0.001)
    x = 0.55 * burst / (np.max(np.abs(burst)) + 1e-9) + 0.45 * pings
    return 0.7 * x / (np.max(np.abs(x)) + 1e-9)


def sfx_door(d=0.6):
    """Door movement: low wooden thud + latch click."""
    n = int(SR * d)
    t = np.arange(n) / SR
    thud = np.sin(2 * np.pi * 72 * t) * np.exp(-t * 12)
    click_n = int(0.05 * SR)
    click = np.zeros(n)
    click[:click_n] = _noise(0.05) * _env(click_n, 0.001, 0.001)
    x = 0.8 * thud + 0.4 * click
    return 0.75 * x / (np.max(np.abs(x)) + 1e-9)


def sfx_rumble(d=2.5):
    """Deep launch rumble: brown noise swell, sub-heavy."""
    n = int(SR * d)
    x = _noise(d, brown=True)
    x = _lowpass(x, cutoff_hz=220.0)
    x = x / (np.max(np.abs(x)) + 1e-9)
    return 0.85 * x * _raised_cosine(n, peak_pos=0.7)


SFX_BUILDERS = {
    "whoosh": sfx_whoosh, "impact": sfx_impact, "boom": sfx_boom,
    "riser": sfx_riser, "alert": sfx_alert, "thunder": sfx_thunder,
    "wind": sfx_wind, "camera": sfx_camera, "digital": sfx_digital,
    "crowd": sfx_crowd,
    "pop": sfx_pop, "ding": sfx_ding, "tick": sfx_tick,
    "sparkle": sfx_sparkle, "punch": sfx_punch, "swoosh": sfx_swoosh,
    "glass_break": sfx_glass_break, "door": sfx_door, "rumble": sfx_rumble,
}

# Real PD/CC0 recordings bundled in assets/sfx/ (see SOURCES.md).
# These names use the recording when present; every other SFX name —
# and any name whose file is missing — falls back to numpy synthesis.
BUNDLED_SFX_FILES = {
    "camera": "camera_real.wav",   # Holga shutter (PD)
    "crowd": "crowd_real.wav",     # applause bed (PD)
    "alert": "siren_real.wav",     # siren (PD)
    "boom": "boom_real.wav",       # explosion (PD)
    "ding": "ding_real.wav",       # church bell (CC0)
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


# Visual -> SFX mapping (2026-10-02, from ~/workspace/research/
# sfx_visual_mapping.md Part 1 + sfx_200.md). The core principle: sound
# follows the VISUAL beat — what is ON SCREEN drives the cue, not the
# narration words alone. visual_tags[i] is the clip metadata
# (Asset.tags + Asset.query) for scene i. Word-boundary matching so
# "bell" never fires on "rebellion".
VISUAL_SFX = [
    # (sfx, [visual keywords])
    ("boom", ["explosion", "blast", "detonat", "missile", "airstrike",
              "bombing", "eruption"]),
    ("crowd", ["crowd", "protest", "rally", "audience", "demonstration",
               "march", "supporters", "cheer"]),
    ("camera", ["photo", "camera", "snapshot", "picture", "archival",
                "portrait"]),
    ("alert", ["siren", "police", "ambulance", "emergency"]),
    ("ding", ["bell", "trophy", "award", "victory", "celebrat"]),
    ("whoosh", ["map", "aerial", "drone", "satellite"]),
    ("impact", ["crash", "collision", "slam"]),
    ("tick", ["countdown", "timer", "clock", "stopwatch"]),
    ("digital", ["glitch", "hacker", "cyber"]),
    ("thunder", ["thunder", "lightning", "storm"]),
    ("swoosh", ["slide", "swipe", "transition"]),
]


def _visual_hit(vtext, kw):
    """Word-boundary match for visual tags (avoids 'bell' in 'rebellion')."""
    return re.search(r"\b" + re.escape(kw), vtext) is not None


def ensure_sfx_library(sfx_dir):
    # v3 (2026-10-02): real PD/CC0 recordings ship in assets/sfx/
    # (see SOURCES.md) and are preferred over numpy synthesis.
    # Synthesis remains the automatic fallback for any name without a
    # bundled file. v2 synth files are kept (whoosh redesign).
    VERSION = "v2"
    os.makedirs(sfx_dir, exist_ok=True)
    bundled = None
    try:
        from .app_paths import sfx_dir as _bundled_sfx_dir
        _b = _bundled_sfx_dir()
        bundled = str(_b) if _b else None
    except Exception:  # noqa: BLE001 - dev edge: just synthesize
        bundled = None
    paths = {}
    for name, fn in SFX_BUILDERS.items():
        real = None
        if bundled and name in BUNDLED_SFX_FILES:
            cand = os.path.join(bundled, BUNDLED_SFX_FILES[name])
            if os.path.isfile(cand):
                real = cand
        if real:
            paths[name] = real
            continue
        p = os.path.join(sfx_dir, f"{name}_{VERSION}.wav")
        old = os.path.join(sfx_dir, f"{name}.wav")
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
        if not os.path.exists(p):
            _write_wav(p, fn())
        paths[name] = p
    return paths


def _kw_hit(text, kw):
    """Match a keyword: word-boundary for short keywords (so 'ai' does not
    fire on 'said'/'again' — that was the main cause of the same SFX
    repeating on almost every scene), plain substring for longer ones."""
    if len(kw) <= 3:
        return re.search(r"\b" + re.escape(kw) + r"\b", text) is not None
    return kw in text


def place_sfx(scene_analyses, timings, max_per_scene=1, seed=0,
              transition_whoosh=True, visual_tags=None):
    """Contextual placement: VISUALS first, narration keywords as fallback.

    visual_tags[i] is the on-screen clip metadata (Asset.tags + query)
    for scene i. Per the 2026-10-02 visual->SFX research, what is ON
    SCREEN drives the cue (explosion visual -> boom, crowd visual ->
    crowd bed, photo -> shutter) — narration keyword matching only fills
    scenes with no visual hit. Never on every scene.

    Variety rules (2026-10-01): the same effect is never placed on two
    scenes in a row — among the keyword hits we prefer effects not used
    recently, and ties are broken with a seeded RNG so a render is
    reproducible but not monotonous.

    Transition whooshes (2026-10-01, from the 200-video SFX research,
    ~/workspace/research/sfx_200.md): the #1 convention across ~89 sources
    is a whoosh/swoosh on scene/segment transitions — peak transient lands
    exactly on the cut frame with ~2 frames of pre-roll (~0.08s at 24fps).
    One SFX per beat: a transition whoosh is skipped when a keyword SFX
    already lands within 0.6s of the cut. Total density is capped at ~10
    cues/minute (research: 8-12/min).
    """
    import random
    rng = random.Random(seed)
    events = []
    recent = []  # last few used sfx names; never repeat the tail
    for si, (sa, t) in enumerate(zip(scene_analyses, timings)):
        text = (sa.text + " " + " ".join(sa.keywords)).lower()
        vtext = ""
        if visual_tags and si < len(visual_tags):
            vtext = (visual_tags[si] or "").lower()
        hits = []
        # 1) VISUAL-first: what is on screen drives the SFX.
        for sfx, words in VISUAL_SFX:
            if any(_visual_hit(vtext, w) for w in words):
                hits.append(sfx)
        # 2) narration keywords only when the visual says nothing.
        if not hits:
            for sfx, words in SFX_KEYWORDS.items():
                if any(_kw_hit(text, w) for w in words):
                    hits.append(sfx)
        fresh = [h for h in hits if h not in recent] or hits
        chosen = rng.sample(fresh, min(max_per_scene, len(fresh)))
        for sfx in chosen:
            at = t["start"] + min(0.4, (t["end"] - t["start"]) * 0.2)
            events.append({"time": round(at, 3), "sfx": sfx, "gain_db": -10})
        recent = (recent + chosen)[-3:]

    if transition_whoosh and len(timings) > 1:
        # Whoosh under each scene cut: start ~2 frames before the cut so the
        # peak transient lands exactly on the cut frame (research #1).
        PRE_ROLL = 0.08
        for t in timings[1:]:
            cut = float(t["start"])
            at = round(max(0.0, cut - PRE_ROLL), 3)
            # One SFX per beat — skip if a keyword hit is already near.
            if any(abs(e["time"] - at) < 0.6 for e in events):
                continue
            events.append({"time": at, "sfx": "whoosh", "gain_db": -20})
            recent = (recent + ["whoosh"])[-3:]

    # Density cap: ~10 cues/min (research quotes 8-12/min). Drop the
    # quietest transition whooshes first if we overshoot.
    if timings:
        total_min = max(0.25, (float(timings[-1]["end"]) - float(timings[0]["start"])) / 60.0)
        cap = int(total_min * 10) + 2
        if len(events) > cap:
            kw = [e for e in events if e["sfx"] != "whoosh" or e["gain_db"] != -20]
            tr = [e for e in events if e not in kw]
            keep_tr = tr[:max(0, cap - len(kw))]
            events = kw + keep_tr

    events.sort(key=lambda e: e["time"])
    return events


# Map a caption style's engine to a subtle UI-style sound,
# CapCut-style: the sound matches how the caption moves.
_ENGINE_SFX = {
    # engine -> (sfx, gain_db)
    "pop": ("pop", -18),
    "spring": ("pop", -17),
    "karaoke": ("tick", -21),
    "wave": ("swoosh", -20),
    "highlight_box": ("pop", -19),
    "underline": ("swoosh", -20),
    "outline_fill": ("pop", -19),
    "glow": ("pop", -19),
    "typewriter": ("tick", -20),
    "solo": ("punch", -16),
    # fade: silence - a sound on every faded line gets annoying.
    # New engines (2026-10-02): each keeps its family's sound so the
    # audio language stays consistent across all 23 styles.
    "box_snap": ("pop", -17),        # snappy box -> punchy pop
    "pill": ("pop", -17),            # springy badge -> pop
    "bar": ("swoosh", -21),          # soft rise -> gentle swoosh
    "glide": ("swoosh", -20),        # slide -> swoosh
    "gradient": ("swoosh", -19),     # sweep -> swoosh
    "sticker": ("punch", -16),       # tilted slap -> punch
    "glitch": ("tick", -19),         # digital jitter -> tick
    "pulse": ("tick", -21),          # rhythmic throb -> soft tick
    # New engines (2026-10-02, real-tools set): each keeps its family's
    # sound so the audio language stays consistent across styles.
    "stomp": ("punch", -17),         # hard slam -> punch
    "reveal": ("swoosh", -20),       # wipe open -> swoosh
    "float": ("swoosh", -21),        # gentle rise -> soft swoosh
    "drop": ("pop", -18),            # drop + bounce -> pop
    "rotate": ("swoosh", -19),       # spin in -> swoosh
    "colour": ("pop", -19),          # wash fades in -> soft pop
    "dim": ("tick", -21),           # teleprompter dim -> soft tick
    "shake": ("tick", -19),          # earthquake jitter -> tick
}


def _caption_chunk_sfx(template, chunk_index=0):
    """Pick the micro-SFX for one caption chunk from the style's engine."""
    if not isinstance(template, dict):
        return None
    return _ENGINE_SFX.get(template.get("engine"))


def place_caption_sfx(sentence_timings, template, max_events=60,
                      min_gap=1.2):
    """Subtle per-chunk sounds synced to caption entrances (CapCut-style).

    One quiet pop/tick/swoosh where each caption chunk appears, matched to
    its animation. Never a machine gun: events are spaced >= min_gap apart
    and capped at max_events per video.
    """
    events = []
    last_at = -1e9
    for i, ch in enumerate(sentence_timings):
        pick = _caption_chunk_sfx(template, i)
        if not pick:
            continue
        sfx, gain = pick
        at = float(ch.get("start", 0))
        if at - last_at < min_gap:
            continue
        if len(events) >= max_events:
            break
        events.append({"time": round(at, 3), "sfx": sfx, "gain_db": gain})
        last_at = at
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

    fc = []
    mix_ins = []
    if music_idx is not None:
        # sidechain key needs a copy of the voice; without music the key
        # output would be unconnected and the filtergraph fails
        fc.append("[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                  "asplit=2[vkey][vmix]")
    else:
        fc.append("[0:a]aresample=44100,aformat=channel_layouts=stereo[vmix]")
    mix_ins = ["[vmix]"]
    if music_idx is not None:
        fc.append(
            f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"volume=0.25,aloop=loop=-1:size=2147483647,atrim=duration={D}[bg]")
        fc.append("[bg][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=500[mduck]")
        mix_ins.append("[mduck]")
    for i, (si, ev) in enumerate(sfx_inputs):
        ms = int(ev["time"] * 1000)
        chain = f"[{si}:a]aresample=44100,aformat=channel_layouts=stereo"
        # Semantic-engine events carry duration + fades: trim the source to
        # the decided duration and fade in/out. Legacy events (no keys)
        # keep the old behavior untouched.
        dur = ev.get("duration")
        if dur:
            d = max(0.1, float(dur))
            fi = max(0.0, float(ev.get("fade_in", 0.0)))
            fo = min(max(0.0, float(ev.get("fade_out", 0.0))), d * 0.9)
            chain += f",atrim=duration={d}"
            if fi > 0:
                chain += f",afade=t=in:st=0:d={fi}"
            if fo > 0:
                chain += f",afade=t=out:st={max(0.0, d - fo)}:d={fo}"
            chain += ",asetpts=PTS-STARTPTS"
        chain += (f",adelay={ms}|{ms},"
                  f"volume={ev.get('gain_db', -10)}dB[s{i}]")
        fc.append(chain)
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
