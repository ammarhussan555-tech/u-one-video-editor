"""Universal Semantic SFX Engine — decision layer.

Fixes SFX selection and repetition at the ENGINE level (2026-10-02 spec).
A dedicated decision layer runs BEFORE anything is placed on the timeline:

    SCRIPT + VISUAL
      -> EVENT DETECTION          (what is happening? a noun is NOT an event)
      -> SCENE CLASSIFICATION    (REQUIRED / OPTIONAL / NO SFX)
      -> AUDIO EVENT             (what would this event sound like?)
      -> SFX CATEGORY            (universal taxonomy, extensible)
      -> SFX CANDIDATES          (every playable asset for the category)
      -> RANKING                 (9 factors -> final score)
      -> REPETITION CHECK        (global history: file / category / meaning)
      -> FINAL SFX or NO SFX     (never force a weak match)

Works across ALL video niches: no niche-specific hardcoded rules anywhere.

Per-scene decision format:
  {"sfx_required": bool, "reason": str, "confidence": float,
   "classification": "REQUIRED"|"OPTIONAL"|"NO_SFX",
   "events": [{"type", "sfx_category", "sfx", "start_time", "duration",
               "volume", "gain_db", "fade_in", "fade_out"}],
   "debug": {...}}

NO SFX is a successful result, never an error.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

# ---------------------------------------------------------------------------
# Confidence bands (user spec)
# ---------------------------------------------------------------------------
CONF_VERY_STRONG = 0.90   # 0.90 - 1.00
CONF_STRONG = 0.75        # 0.75 - 0.89
CONF_POSSIBLE = 0.60      # 0.60 - 0.74
CONF_FLOOR = 0.60         # below -> prefer NO SFX

# Scene classifications
REQUIRED = "REQUIRED"     # scene needs this sound (clear sound event)
OPTIONAL = "OPTIONAL"     # sound plausible / enhancing (needs high score)
NO_SFX_CLASS = "NO_SFX"   # scene class C: add nothing, always

# ---------------------------------------------------------------------------
# Engine configuration — every limit from the user spec, tunable in one place
# ---------------------------------------------------------------------------
@dataclass
class SFXConfig:
    exact_file_cooldown_s: float = 20.0   # E: no same file within 20 s
    max_file_uses: int = 2                # E: max 2x per video ...
    allow_repetition: bool = False        # E: ... unless user enables this
    category_cooldown_s: float = 15.0     # F: 10-20 s between same category
    similarity_cooldown_s: float = 15.0   # H: same meaning = same group
    min_confidence_required: float = 0.60 # A: REQUIRED threshold
    min_confidence_optional: float = 0.75 # A: high-confidence B only
    min_confidence_cinematic: float = 0.85  # I: generic cinematic bar
    max_major_per_scene: int = 1          # K
    max_events_per_10s: int = 3           # K
    max_cinematic_per_10s: int = 1        # K
    max_transition_per_8s: int = 1        # K
    narration_duck_db: float = -4.0       # rule: never overpower narration
    merge_gap_s: float = 5.0              # G: consecutive same-event scenes
    enable_transition_sfx: bool = True
    debug: bool = False


DEFAULT_CONFIG = SFXConfig()

# ---------------------------------------------------------------------------
# Taxonomy registry — extensible via register_category() with NO core changes
# ---------------------------------------------------------------------------
@dataclass
class SFXCategory:
    id: str
    label: str
    description: str
    # sfx names (audio_design.SFX_BUILDERS keys) in preference order.
    # Real recordings first. EMPTY list => the engine never plays this
    # category (rule: never play a wrong sound).
    assets: list = field(default_factory=list)
    default_duration: float = 1.0
    default_volume: float = 0.5          # linear 0..1, pre-ducking
    peak_offset: float = 0.05           # peak sits this far into the sound
    layer_with: list = field(default_factory=list)  # category ids
    ambience: bool = False              # continuous bed while scene lasts
    cinematic: bool = False             # generic cinematic sound (sect. I)


CATEGORIES: dict[str, SFXCategory] = {}


def register_category(cat: SFXCategory) -> None:
    """Add (or replace) a taxonomy category without touching core engine."""
    CATEGORIES[cat.id] = cat


def _reg(cid, label, desc, assets, dur=1.0, vol=0.5, peak=0.05,
         layer=(), amb=False, cin=False):
    register_category(SFXCategory(cid, label, desc, list(assets), dur, vol,
                                  peak, list(layer), amb, cin))


# --- Nature / weather / water / animals ------------------------------------
_reg("nature", "Nature ambience", "forests, fields, outdoors beds",
     ["wind"], dur=4.0, vol=0.30, amb=True)
_reg("weather", "Weather", "storms and thunder", ["thunder", "wind"], dur=3.0,
     vol=0.45)
_reg("water", "Water", "rivers, rain, splashes — no suitable asset",
     [], dur=2.0, vol=0.4, amb=True)
_reg("animals", "Animals", "wildlife calls — no suitable asset",
     [], dur=1.5, vol=0.4)
# --- Human ------------------------------------------------------------------
_reg("human_movement", "Human movement", "walking, running, gestures",
     [], dur=1.0, vol=0.35)
_reg("footsteps", "Footsteps", "no suitable asset", [], dur=0.5, vol=0.3)
_reg("breathing", "Breathing", "no suitable asset", [], dur=1.5, vol=0.25)
_reg("crowd", "Crowd", "crowds, applause, protests, rallies",
     ["crowd"], dur=0.0, vol=0.50, amb=True)   # dur 0 => whole scene
# --- Vehicles / machines ------------------------------------------------------
_reg("vehicles", "Vehicles", "cars, traffic — no suitable asset",
     [], dur=2.0, vol=0.45, amb=True)
_reg("aircraft", "Aircraft", "planes, helicopters — no suitable asset",
     [], dur=2.5, vol=0.45, amb=True)
_reg("trains", "Trains", "no suitable asset", [], dur=2.5, vol=0.45)
_reg("machinery", "Machinery", "engines, motors — no suitable asset",
     [], dur=2.0, vol=0.4, amb=True)
_reg("industrial", "Industrial", "factories — no suitable asset",
     [], dur=2.0, vol=0.4, amb=True)
_reg("construction", "Construction", "building impacts", ["impact"], dur=1.2,
     vol=0.5)
# --- Materials / objects -------------------------------------------------------
_reg("doors", "Doors", "door open/close/slam, metal or wood",
     ["door"], dur=0.6, vol=0.55, peak=0.08)
_reg("glass", "Glass", "glass breaking / shattering", ["glass_break"],
     dur=0.8, vol=0.60, peak=0.03)
_reg("metal", "Metal", "metal hits, clanks", ["impact"], dur=0.7, vol=0.45,
     peak=0.03)
_reg("wood", "Wood", "wooden knocks, thuds", ["punch"], dur=0.5, vol=0.40,
     peak=0.05)
_reg("paper", "Paper", "paper rustle, documents — no suitable asset",
     [], dur=0.6, vol=0.30)
# --- Tech -----------------------------------------------------------------------
_reg("electronics", "Electronics", "devices, gadgets", ["digital"], dur=0.7,
     vol=0.35)
_reg("computers", "Computers", "typing, servers, UI", ["digital"], dur=0.7,
     vol=0.35)
_reg("phones", "Phones", "rings, notifications — no suitable asset",
     [], dur=0.9, vol=0.45)
_reg("ui", "UI sounds", "clicks, pops for interface motion",
     ["tick", "pop"], dur=0.2, vol=0.35, peak=0.01)
_reg("radio", "Communication / radio", "radio chatter, comms", ["digital"],
     dur=1.2, vol=0.35)
# --- Alarms ----------------------------------------------------------------------
_reg("alarms", "Alarms", "warning alarms", ["alert"], dur=1.5, vol=0.55)
_reg("sirens", "Sirens", "police / ambulance / emergency sirens",
     ["alert"], dur=2.5, vol=0.50)
# --- Impact family -----------------------------------------------------------------
_reg("impacts", "Impacts", "hits, slams, thuds", ["impact", "punch"], dur=1.0,
     vol=0.55, peak=0.04)
_reg("crashes", "Crashes", "collisions, wrecks", ["impact"], dur=1.8,
     vol=0.65, peak=0.05, layer=["explosions"])
_reg("explosions", "Explosions", "blasts, detonations, airstrikes",
     ["boom"], dur=2.2, vol=0.70, peak=0.06)
_reg("combat", "Combat", "battle chaos — generic hits only", ["impact"],
     dur=1.5, vol=0.50)
_reg("weapons", "Weapons", "gunfire etc. — no suitable asset",
     [], dur=0.5, vol=0.55)
# --- Science / space ---------------------------------------------------------------
_reg("laboratory", "Science / laboratory", "lab ambience, equipment",
     ["digital"], dur=1.5, vol=0.30, amb=True)
_reg("space", "Space / launch", "rocket rumble, deep launch beds",
     ["rumble"], dur=2.5, vol=0.65, peak=0.4, layer=["explosions"])
# --- Navigation / camera --------------------------------------------------------------
_reg("maps", "Maps / navigation", "map motion", ["swoosh"], dur=0.6,
     vol=0.35, cin=True)
_reg("camera_move", "Camera movement", "pans, aerials, drone moves",
     ["whoosh"], dur=0.8, vol=0.35, cin=True)
_reg("zoom", "Zoom", "zoom in/out motion", ["swoosh"], dur=0.6, vol=0.32,
     cin=True)
# --- Editorial (generic cinematic — section I: very high threshold) ---------------------
_reg("transitions", "Transitions", "scene cuts and wipes", ["whoosh"],
     dur=0.45, vol=0.30, peak=0.22, cin=True)
_reg("reveals", "Reveals", "big on-screen reveals", ["impact"], dur=1.0,
     vol=0.45, peak=0.05, cin=True)
_reg("tension", "Tension", "build-up before a payoff", ["riser"], dur=1.6,
     vol=0.45, peak=1.4, cin=True)
_reg("suspense", "Suspense", "uneasy beds", ["riser"], dur=2.0, vol=0.35,
     peak=1.8, cin=True)
_reg("mystery", "Mystery", "quiet intrigue", ["riser"], dur=2.0, vol=0.28,
     cin=True)
_reg("horror", "Horror atmosphere", "dark dread beds", ["wind"], dur=3.0,
     vol=0.30, amb=True)
# --- Ambience ------------------------------------------------------------------------------
_reg("ambience_cinematic", "Cinematic ambience", "no suitable asset",
     [], dur=4.0, vol=0.25, amb=True)
_reg("ambience_documentary", "Documentary ambience", "no suitable asset",
     [], dur=4.0, vol=0.25, amb=True)
_reg("ambience_news", "News / broadcast ambience", "no suitable asset",
     [], dur=4.0, vol=0.22, amb=True)
# --- Confirmation ----------------------------------------------------------------------------
_reg("success", "Success / confirmation", "achievements, wins, positive beats",
     ["ding"], dur=0.8, vol=0.50, peak=0.05)
# --- Fire -------------------------------------------------------------------------------------
_reg("fire", "Fire", "flames, burning — no suitable asset",
     [], dur=2.0, vol=0.4, amb=True)

# ---------------------------------------------------------------------------
# Semantic similarity groups (section H): compare MEANING, not filenames.
# "cinematic boom 01" / "cinematic boom 02" / "cinematic impact 01" are one
# repetition group. Hard groups block re-use inside the similarity cooldown;
# soft groups only add a penalty.
# ---------------------------------------------------------------------------
SIMILARITY_GROUPS = {
    "heavy_hit": ({"boom", "impact", "punch"}, True),
    "air_sweep": ({"whoosh", "swoosh"}, True),
    "build": ({"riser"}, True),
    "ui_blip": ({"tick", "pop", "ding", "digital", "camera", "sparkle"},
                False),
}


def _similarity_group(asset: str):
    for gid, (members, hard) in SIMILARITY_GROUPS.items():
        if asset in members:
            return gid, hard
    return None, False


# ---------------------------------------------------------------------------
# Scene context — the four inputs (+ neighbours for prev/next analysis)
# ---------------------------------------------------------------------------
@dataclass
class SceneContext:
    narration: str = ""
    keywords: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    phrases: list = field(default_factory=list)
    visual_text: str = ""            # Asset.tags + Asset.query
    actions: list = field(default_factory=list)  # (verb, signal) signal in
                                                 # {"visual","narrated"}
    mood: str = "neutral"
    topic: str = "general"
    start: float = 0.0
    end: float = 0.0
    narration_present: bool = True
    action_repeat: bool = False      # "another", "again", "second" ...
    visual_signal: bool = False      # any visual metadata at all
    prev: object = None              # previous SceneContext (set by planner)
    next: object = None              # next SceneContext (set by planner)
    scene_id: int = 0


# Action verb families. "visual" = the verb (or the event noun acting as one)
# appears in the clip metadata itself; "narrated" = narration only.
_ACTION_VERBS = {
    "open": ["open", "opens", "opened", "opening", "unlock"],
    "close": ["close", "closes", "closed", "closing", "shut"],
    "slam": ["slam", "slams", "slammed"],
    "break": ["break", "breaks", "broke", "broken", "shatter",
              "shattered", "smash", "smashed", "crack"],
    "launch": ["launch", "launches", "launched", "liftoff", "lift-off",
               "takeoff", "take-off", "blast off"],
    "zoom": ["zoom", "zooms", "zooming", "zoomed"],
    "appear": ["appear", "appears", "appeared", "emerge", "emerges",
               "emerged", "unveil"],
    "explode": ["explode", "explodes", "exploded", "explosion", "detonate",
                "detonated", "blast", "blasts"],
    "drive": ["drive", "drives", "driving", "drove", "speeding"],
    "fly": ["fly", "flies", "flying", "flew", "soar"],
    "fall": ["fall", "falls", "fell", "collapse", "collapsed"],
    "hit": ["hit", "hits", "strike", "strikes", "struck", "slam"],
    "ring": ["ring", "rings", "ringing", "call", "phone"],
    "type": ["type", "typing", "typed", "keyboard"],
    "cheer": ["cheer", "cheers", "cheering", "applaud", "chant"],
    "rain": ["rain", "raining", "pour"],
    "burn": ["burn", "burns", "burning", "fire", "flames"],
    "erupt": ["erupt", "erupts", "erupted", "erupting", "eruption"],
    "crash": ["crash", "crashes", "crashed", "collision", "collide",
              "collided", "wreck"],
}
# Camera-motion tags: movement OF the camera, not of subjects.
_CAMERA_TAGS = ["zoom", "pan", "aerial", "drone", "tracking", "orbit",
                "timelapse", "dolly", "crane", "gimbal", "tilt"]
_REPEAT_WORDS = ["another", "again", "second", "once more", "repeated"]


def _wb(text: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


def extract_actions(narration: str, visual_text: str):
    """(verb, signal) pairs. Visual-confirmed actions outrank narrated ones."""
    narr, vis = narration.lower(), visual_text.lower()
    found = []
    for verb, words in _ACTION_VERBS.items():
        v_hit = any(_wb(vis, w) for w in words)
        n_hit = any(_wb(narr, w) for w in words)
        if v_hit:
            found.append((verb, "visual"))
        elif n_hit:
            found.append((verb, "narrated"))
    for m in _CAMERA_TAGS:
        if _wb(vis, m):
            found.append(("camera_move", "visual"))
    return found


def build_scene_contexts(scenes, analyses, timings, vtags, video_mood="neutral"):
    """Derive the four engine inputs from pipeline data. Inputs with weak
    signal are flagged (visual_signal=False) so detectors can demote."""
    ctxs = []
    for i, (sc, sa) in enumerate(zip(scenes, analyses)):
        t = timings[i] if i < len(timings) else {"start": 0.0, "end": 1.0}
        vtext = (vtags[i] if vtags and i < len(vtags) else "") or ""
        narr = getattr(sa, "text", "") or getattr(sc, "text", "") or ""
        actions = extract_actions(narr, vtext)
        c = SceneContext(
            narration=narr,
            keywords=list(getattr(sa, "keywords", []) or []),
            numbers=list(getattr(sa, "numbers", []) or []),
            phrases=list(getattr(sa, "phrases", []) or []),
            visual_text=vtext.strip(),
            actions=actions,
            mood=getattr(sa, "mood", None) or video_mood or "neutral",
            topic=getattr(sa, "topic", "general") or "general",
            start=float(t.get("start", 0.0)),
            end=float(t.get("end", 1.0)),
            narration_present=bool(narr.strip()),
            action_repeat=any(_wb(narr.lower(), w) for w in _REPEAT_WORDS),
            visual_signal=bool(vtext.strip()),
            scene_id=i,
        )
        c.duration = max(0.2, c.end - c.start)
        ctxs.append(c)
    return ctxs


# ---------------------------------------------------------------------------
# Audio events — WHAT IS HAPPENING, before what it sounds like.
# HARD RULE: a noun is NOT an event. Detectors require event evidence
# (an action, a state change, an appearing/disappearing thing), never a
# bare noun in narration or tags.
# ---------------------------------------------------------------------------
@dataclass
class AudioEvent:
    event_type: str
    description: str
    classification: str            # REQUIRED | OPTIONAL | NONE
    category_hint: str | None = None
    confidence: float = 0.0        # for explicit NONE verdicts
    anchor: str = "action"         # action | start | end | cut
    anchor_frac: float = 0.25
    volume_scale: float = 1.0
    layer: bool = False
    # ranking evidence, 0..1
    semantic: float = 0.7
    visual: float = 0.7
    action: float = 0.7
    timing: float = 0.8
    context: float = 0.7
    cinematic: bool = False        # generic cinematic sound? (section I)
    justification: float = 0.0     # 0..1: how strongly the edit calls for it


def _acts(ctx, *verbs):
    return [(v, s) for v, s in ctx.actions if v in verbs]


def _vhas(ctx, *words):
    vt = ctx.visual_text.lower()
    return any(_wb(vt, w) for w in words)


def _nhas(ctx, *words):
    nt = ctx.narration.lower()
    return any(_wb(nt, w) for w in words)


# --- event detectors -------------------------------------------------------
def _ev_talking_head(ctx, prev, nxt, env):
    if _vhas(ctx, "talking", "speaking", "interview", "anchor", "podcast",
             "person talking", "man speaking", "woman speaking",
             "news anchor", "presenter"):
        if not _acts(ctx, "open", "close", "slam", "break", "launch",
                     "explode", "erupt", "hit", "fall", "drive", "fly",
                     "cheer", "appear", "zoom", "crash", "camera_move"):
            return [AudioEvent("talking_head",
                               "Person talking on screen; no meaningful "
                               "physical action.", "NONE", confidence=0.95)]
    return []


def _ev_explosion(ctx, prev, nxt, env):
    vt = ctx.visual_text.lower()
    active = _vhas(ctx, "explosion", "fireball", "detonat", "missile",
                   "airstrike", "bombing")
    if not active:
        return []  # narration-only mention => NO EVENT (hard rule)
    aftermath = any(_wb(vt, w) for w in
                    ("aftermath", "ruins", "rubble", "debris"))
    live_fire = any(_wb(vt, w) for w in
                    ("fire", "fireball", "burning", "flames"))
    if aftermath and not live_fire:
        # The event already passed — unless the previous scene showed the
        # blast itself, in which case this is a continuation (handled by
        # dedup), not a new event either way.
        return [AudioEvent("explosion_aftermath",
                           "Explosion aftermath on screen; the blast itself "
                           "already passed.", "NONE", confidence=0.80)]
    acts = _acts(ctx, "explode")
    narr = _nhas(ctx, "explosion", "detonat", "airstrike", "bombing",
                 "exploded", "blast")
    vis_action = bool(acts and acts[0][1] == "visual")
    return [AudioEvent(
        "explosion", "Explosion visible on screen.",
        "REQUIRED", category_hint="explosions",
        anchor="action", anchor_frac=0.15,
        semantic=1.0 if narr else 0.6,
        visual=1.0 if vis_action else 0.85,
        action=1.0 if acts else 0.7,
        timing=0.95, context=0.9)]


def _ev_eruption(ctx, prev, nxt, env):
    if not _vhas(ctx, "volcano", "eruption"):
        return []
    active = _vhas(ctx, "erupting", "eruption", "lava", "ash")
    if not active:
        return []  # noun only ("volcano") => NO EVENT (hard rule)
    acts = _acts(ctx, "erupt", "explode", "burn")
    vis_action = bool(acts and acts[0][1] == "visual")
    return [AudioEvent(
        "eruption", "Volcano erupting on screen.",
        "REQUIRED" if vis_action else "OPTIONAL",
        category_hint="explosions", anchor="action", anchor_frac=0.2,
        semantic=0.9 if _nhas(ctx, "erupt") else 0.6,
        visual=1.0 if vis_action else 0.8, action=1.0 if acts else 0.6,
        timing=0.9, context=0.85)]


def _ev_crash(ctx, prev, nxt, env):
    if not _vhas(ctx, "crash", "collision", "wreck"):
        return []
    acts = _acts(ctx, "crash", "hit", "fall")
    vis_action = bool(acts and acts[0][1] == "visual")
    if not acts and not _nhas(ctx, "crash", "collision", "collided"):
        return []  # noun only => NO EVENT
    return [AudioEvent(
        "crash", "Collision visible on screen.",
        "REQUIRED" if vis_action else "OPTIONAL",
        category_hint="crashes", anchor="action", anchor_frac=0.2,
        semantic=0.9, visual=1.0 if vis_action else 0.75,
        action=1.0 if acts else 0.6, timing=0.9, context=0.8)]


def _ev_door(ctx, prev, nxt, env):
    if not _vhas(ctx, "door", "gate", "entrance"):
        return []
    acts = _acts(ctx, "open", "close", "slam")
    if not acts:
        return []  # a door just sitting there makes no sound (hard rule)
    vis_action = acts[0][1] == "visual"
    return [AudioEvent(
        "door_action", "Door opens/closes on screen.",
        "REQUIRED" if vis_action else "OPTIONAL",
        category_hint="doors", anchor="action", anchor_frac=0.3,
        semantic=0.9, visual=1.0 if vis_action else 0.7,
        action=1.0, timing=0.95, context=0.7)]


def _ev_glass(ctx, prev, nxt, env):
    acts = _acts(ctx, "break")
    if not acts or not _vhas(ctx, "glass", "window", "mirror"):
        return []
    vis_action = acts[0][1] == "visual"
    return [AudioEvent(
        "glass_break", "Glass breaking on screen.",
        "REQUIRED" if vis_action else "OPTIONAL",
        category_hint="glass", anchor="action", anchor_frac=0.3,
        semantic=0.9, visual=1.0 if vis_action else 0.75,
        action=1.0, timing=0.95, context=0.7)]


def _ev_thunder(ctx, prev, nxt, env):
    if not _vhas(ctx, "lightning", "thunderstorm"):
        return []  # "rain"/"storm" in narration alone => NO EVENT
    return [AudioEvent(
        "thunderstorm", "Lightning visible; thunder follows.",
        "REQUIRED", category_hint="weather",
        anchor="action", anchor_frac=0.2,
        semantic=0.9 if _nhas(ctx, "lightning", "thunder", "storm") else 0.6,
        visual=1.0, action=0.6, timing=0.85, context=0.8)]


def _ev_rocket(ctx, prev, nxt, env):
    if not _vhas(ctx, "rocket", "spacecraft", "shuttle"):
        return []
    acts = _acts(ctx, "launch")
    if not acts:
        return []  # rocket on the pad, noun only => NO EVENT
    vis_action = acts[0][1] == "visual"
    return [AudioEvent(
        "rocket_launch", "Rocket launching on screen.",
        "REQUIRED" if vis_action else "OPTIONAL",
        category_hint="space", anchor="action", anchor_frac=0.1,
        layer=True, semantic=1.0, visual=1.0 if vis_action else 0.8,
        action=1.0, timing=0.95, context=0.85)]


def _ev_map_zoom(ctx, prev, nxt, env):
    if not _vhas(ctx, "map"):
        return []
    acts = _acts(ctx, "zoom")
    if not acts:
        return []  # static map => NO EVENT
    vis_action = acts[0][1] == "visual"
    return [AudioEvent(
        "map_zoom", "Map zoom motion on screen.",
        "OPTIONAL", category_hint="zoom",
        anchor="action", anchor_frac=0.2, volume_scale=0.8,
        semantic=0.6, visual=1.0 if vis_action else 0.7, action=1.0,
        timing=0.95, context=0.7, cinematic=True,
        justification=0.8 if vis_action else 0.5)]


def _ev_camera_move(ctx, prev, nxt, env):
    if not any(v == "camera_move" and s == "visual"
               for v, s in ctx.actions):
        return []
    motion_words = [m for m in _CAMERA_TAGS if _wb(ctx.visual_text.lower(), m)]
    strong = len(motion_words) >= 1 and _vhas(
        ctx, "aerial", "drone", "fast", "sweep", "flyover")
    return [AudioEvent(
        "camera_move", "Camera in motion (%s)." % "/".join(motion_words[:2]),
        "OPTIONAL", category_hint="camera_move",
        anchor="start", volume_scale=0.8,
        semantic=0.5, visual=1.0 if strong else 0.8, action=0.9,
        timing=0.8, context=0.7, cinematic=True,
        justification=0.8 if strong else 0.45)]


def _ev_crowd(ctx, prev, nxt, env):
    if not _vhas(ctx, "crowd", "protest", "rally", "audience",
                 "demonstration", "march", "supporters"):
        return []
    cheering = _vhas(ctx, "cheer", "applause", "chant") or \
        _nhas(ctx, "cheer", "applause", "chant", "roar")
    return [AudioEvent(
        "crowd", "Crowd on screen.",
        "REQUIRED" if cheering else "OPTIONAL",
        category_hint="crowd", anchor="start", volume_scale=0.9,
        semantic=0.85 if _nhas(ctx, "crowd", "rally", "protest",
                               "supporters") else 0.6,
        visual=1.0, action=0.8 if cheering else 0.5, timing=0.8,
        context=0.85)]


def _ev_siren(ctx, prev, nxt, env):
    if not _vhas(ctx, "siren", "police", "ambulance", "emergency",
                 "fire truck", "patrol"):
        return []  # "emergency" in narration alone => NO EVENT
    return [AudioEvent(
        "siren_vehicle", "Emergency vehicle on screen.",
        "OPTIONAL", category_hint="sirens",
        anchor="start", volume_scale=0.9,
        semantic=0.7 if _nhas(ctx, "siren", "police", "ambulance",
                              "emergency") else 0.5,
        visual=1.0, action=0.7, timing=0.8, context=0.7)]


def _ev_photo(ctx, prev, nxt, env):
    if not _vhas(ctx, "photo", "photograph", "archival", "portrait",
                 "historical"):
        return []
    acts = _acts(ctx, "appear")
    if not acts:
        # Still photograph: NO random sound, ever (user spec example).
        return [AudioEvent("still_photo",
                           "Still photograph on screen; no sound event.",
                           "NONE", confidence=0.85)]
    return [AudioEvent(
        "photo_appear", "Photograph appears on screen.",
        "OPTIONAL", category_hint="ui",
        anchor="action", anchor_frac=0.2, volume_scale=0.5,
        semantic=0.6, visual=0.9, action=0.8, timing=0.85, context=0.6)]


def _ev_stat(ctx, prev, nxt, env):
    if not ctx.numbers:
        return []
    emphasis = _nhas(ctx, "revealed", "announced", "confirmed", "record",
                     "broke", "soaring", "plummet", "unprecedented",
                     "staggering", "massive")
    visual_cue = _vhas(ctx, "text", "graphic", "stat", "chart",
                       "infographic", "title", "number")
    if not (emphasis and visual_cue):
        # A number merely mentioned, or no on-screen graphic => the
        # visual animation does not support a cue => NO EVENT.
        return []
    return [AudioEvent(
        "stat_reveal", "Key figure emphasized with on-screen graphic.",
        "OPTIONAL", category_hint="ui",
        anchor="action", anchor_frac=0.3, volume_scale=0.6,
        semantic=0.85, visual=0.9, action=0.7, timing=0.85, context=0.7)]


def _ev_tension(ctx, prev, nxt, env):
    if ctx.mood not in ("suspense", "dramatic", "tense"):
        return []
    if not _nhas(ctx, "but then", "suddenly", "what happened next",
                 "the truth", "nobody expected", "little did"):
        return []
    visual_tension = _vhas(ctx, "storm", "dark", "shadow", "confrontation",
                           "countdown", "ticking")
    return [AudioEvent(
        "tension_build", "Tension building before a payoff.",
        "OPTIONAL", category_hint="tension",
        anchor="end", volume_scale=0.8,
        semantic=0.7, visual=0.6 if visual_tension else 0.3, action=0.3,
        timing=0.8, context=0.9, cinematic=True,
        justification=0.7 if visual_tension else 0.3)]


def _ev_vehicle(ctx, prev, nxt, env):
    # Honest event with no suitable asset: detected so the pipeline can
    # explain WHY nothing plays, then dropped at candidate stage.
    if not _vhas(ctx, "car", "vehicle", "truck", "traffic"):
        return []
    acts = _acts(ctx, "drive")
    if not acts:
        return []  # parked car, noun only => NO EVENT
    return [AudioEvent(
        "vehicle_drive", "Vehicle driving on screen (no suitable asset).",
        "OPTIONAL", category_hint="vehicles",
        anchor="start", semantic=0.8, visual=0.9, action=0.9, timing=0.7,
        context=0.7)]


DETECTORS = [_ev_talking_head, _ev_explosion, _ev_eruption, _ev_crash,
             _ev_door, _ev_glass, _ev_thunder, _ev_rocket, _ev_map_zoom,
             _ev_camera_move, _ev_crowd, _ev_siren, _ev_photo, _ev_stat,
             _ev_tension, _ev_vehicle]


def detect_events(ctx, prev=None, nxt=None, env=None):
    """SCRIPT + VISUAL -> EVENT DETECTION. Returns AudioEvent list."""
    prev = prev if prev is not None else getattr(ctx, "prev", None)
    nxt = nxt if nxt is not None else getattr(ctx, "next", None)
    env = env or {}
    found = []
    for det in DETECTORS:
        try:
            found.extend(det(ctx, prev, nxt, env) or [])
        except Exception:
            continue
    return found


def _event_strength(ev: AudioEvent) -> float:
    return (ev.semantic + ev.visual + ev.action) / 3.0


def pick_primary(events):
    """Classify the scene: REQUIRED > OPTIONAL > NONE (strong NONE vetoes)."""
    if not events:
        return None
    reqs = [e for e in events if e.classification == REQUIRED]
    if reqs:
        return max(reqs, key=_event_strength)
    nones = [e for e in events if e.classification == "NONE"]
    if any(e.confidence >= 0.90 for e in nones):
        # Scene class C: explicit no-sound verdict wins over weak optionals.
        return max(nones, key=lambda e: e.confidence)
    opts = [e for e in events if e.classification == OPTIONAL]
    if opts:
        return max(opts, key=_event_strength)
    if nones:
        return max(nones, key=lambda e: e.confidence)
    return None


# ---------------------------------------------------------------------------
# Candidate ranking — 9 factors (section C)
# ---------------------------------------------------------------------------
def _bundled_names():
    try:
        from . import audio_design as ad
        return set(getattr(ad, "BUNDLED_SFX_FILES", {}).keys())
    except Exception:
        return set()


def candidates_for(event: AudioEvent):
    """EVENT -> SFX CATEGORY -> SFX CANDIDATES (every playable asset)."""
    cat = CATEGORIES.get(event.category_hint or "")
    if not cat:
        return []
    try:
        from . import audio_design as ad
        builders = set(getattr(ad, "SFX_BUILDERS", {}).keys())
    except Exception:
        builders = set()
    return [(cat.id, a) for a in cat.assets if a in builders]


def rank_candidate(event, category_id, asset, ctx, history, config):
    """Score one candidate on the 9 factors. Returns dict with final score
    or a hard block reason (repetition rules are blocks, not nudges)."""
    factors = {}
    # --- hard repetition blocks (sections D/E/F/H) -------------------------
    # (only past placements count; future-dated records are ignored)
    lu = history.last_file_time(asset)
    if lu is not None and 0 <= (ctx.start - lu) < config.exact_file_cooldown_s:
        return {"blocked": f"exact file '{asset}' used "
                f"{ctx.start - lu:.1f}s ago (< {config.exact_file_cooldown_s:.0f}s)",
                "factors": factors}
    if not config.allow_repetition and \
            history.file_uses(asset, before=ctx.start) >= config.max_file_uses:
        return {"blocked": f"file '{asset}' already used "
                f"{config.max_file_uses}x in this video", "factors": factors}
    lc = history.last_category_time(category_id)
    if lc is not None and 0 <= (ctx.start - lc) < config.category_cooldown_s:
        return {"blocked": f"category '{category_id}' used "
                f"{ctx.start - lc:.1f}s ago (< {config.category_cooldown_s:.0f}s)",
                "factors": factors}
    gid, hard = _similarity_group(asset)
    if hard:
        lg = history.last_group_time(gid, exclude_asset=asset)
        if lg is not None and \
                0 <= (ctx.start - lg) < config.similarity_cooldown_s:
            return {"blocked": f"similar sound already used "
                    f"{ctx.start - lg:.1f}s ago (group '{gid}')",
                    "factors": factors}
    # --- the 9 factors ------------------------------------------------------
    factors["semantic_match"] = event.semantic
    factors["visual_match"] = event.visual
    factors["action_match"] = event.action
    factors["timing_match"] = event.timing
    factors["context_match"] = event.context
    factors["quality_score"] = 1.0 if asset in _bundled_names() else 0.6
    factors["repetition_penalty"] = round(
        min(0.45, 0.15 * history.category_uses(category_id,
                                               before=ctx.start)), 3)
    factors["overuse_penalty"] = round(
        min(0.5, 0.10 * max(0, history.total_events() - 4)), 3)
    factors["generic_sfx_penalty"] = (
        0.0 if (not event.cinematic or event.justification >= 0.7) else 0.35)
    final = (0.25 * factors["semantic_match"]
             + 0.25 * factors["visual_match"]
             + 0.20 * factors["action_match"]
             + 0.10 * factors["timing_match"]
             + 0.10 * factors["context_match"]
             + 0.10 * factors["quality_score"]
             - factors["repetition_penalty"]
             - factors["overuse_penalty"]
             - factors["generic_sfx_penalty"])
    factors["final"] = round(max(0.0, min(1.0, final)), 3)
    return {"blocked": None, "factors": factors}


def _threshold_for(event, config):
    if event.cinematic and event.justification < 0.7:
        return config.min_confidence_cinematic
    if event.classification == REQUIRED:
        return config.min_confidence_required
    return config.min_confidence_optional


# ---------------------------------------------------------------------------
# Global SFX history — ONE history for the entire video (section D)
# ---------------------------------------------------------------------------
@dataclass
class SFXRecord:
    file: str
    category: str
    event_type: str
    scene_id: int
    start: float
    end: float
    confidence: float = 0.0
    classification: str = OPTIONAL
    cinematic: bool = False


class SFXHistory:
    """Every selection checks this BEFORE placement."""

    def __init__(self, config: SFXConfig):
        self.config = config
        self.records: list[SFXRecord] = []

    # -- lookups ------------------------------------------------------------
    def _times(self, pred):
        return [r.start for r in self.records if pred(r)]

    def last_file_time(self, asset):
        ts = self._times(lambda r: r.file == asset)
        return max(ts) if ts else None

    def file_uses(self, asset, before=None):
        return sum(1 for r in self.records
                   if r.file == asset and (before is None or r.start <= before))

    def last_category_time(self, category_id):
        ts = self._times(lambda r: r.category == category_id)
        return max(ts) if ts else None

    def category_uses(self, category_id, before=None):
        return sum(1 for r in self.records
                   if r.category == category_id
                   and (before is None or r.start <= before))

    def last_group_time(self, group_id, exclude_asset=None):
        members = SIMILARITY_GROUPS[group_id][0]
        ts = [r.start for r in self.records
              if r.file in members and r.file != exclude_asset]
        return max(ts) if ts else None

    def total_events(self):
        return len(self.records)

    def events_in_window(self, t0, t1):
        return [r for r in self.records if t0 <= r.start < t1]

    def cinematic_in_window(self, t0, t1):
        return [r for r in self.records
                if t0 <= r.start < t1 and r.cinematic]

    def transitions_in_window(self, t0, t1):
        return [r for r in self.records
                if t0 <= r.start < t1 and r.event_type == "transition_cut"]

    def prev_similar_text(self, asset, category_id):
        gid, _ = _similarity_group(asset)
        cands = [r for r in self.records
                 if r.file == asset or r.category == category_id
                 or (gid and r.file in SIMILARITY_GROUPS[gid][0])]
        if not cands:
            return "none"
        r = max(cands, key=lambda r: r.start)
        return f"{r.file} ({r.category}) @ {r.start:.1f}s"

    # -- mutation -------------------------------------------------------------
    def record(self, rec: SFXRecord):
        self.records.append(rec)

    def remove_scene(self, scene_id):
        self.records = [r for r in self.records if r.scene_id != scene_id]

    # -- budget (section K) -----------------------------------------------------
    def budget_allows(self, start, cinematic, classification):
        cfg = self.config
        if len(self.events_in_window(start, start + 10.0)) \
                >= cfg.max_events_per_10s and classification != REQUIRED:
            return False, "over 3 SFX events per 10s budget"
        if cinematic and len(
                self.cinematic_in_window(start, start + 10.0)) \
                >= cfg.max_cinematic_per_10s:
            return False, "over 1 generic cinematic per 10s budget"
        return True, ""


# ---------------------------------------------------------------------------
# Asset resolution — never play a wrong sound
# ---------------------------------------------------------------------------
def resolve_asset(category_id: str):
    """Return an SFX_BUILDERS key for the category, or None when no
    suitable asset exists (=> the decision becomes NO SFX)."""
    from . import audio_design as ad
    cat = CATEGORIES.get(category_id)
    if not cat:
        return None
    for name in cat.assets:
        if name in ad.SFX_BUILDERS:
            return name
    return None


def volume_to_gain_db(volume: float, duck_db: float = 0.0) -> float:
    v = max(0.01, min(1.0, volume))
    return round(max(-32.0, min(-2.0, 20 * math.log10(v) + duck_db)), 1)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
NO_SFX_DECISION = {"sfx_required": False,
                   "reason": "No meaningful physical or contextual sound "
                             "event is present."}


def _no_sfx(reason, confidence=0.0, classification=NO_SFX_CLASS,
            event=None, debug_extra=None):
    d = {**NO_SFX_DECISION, "confidence": round(confidence, 2),
         "reason": reason, "events": [],
         "classification": classification}
    dbg = {"detected_event": event.event_type if event else None,
           "event_description": event.description if event else "",
           "classification": classification,
           "selected_sfx": None, "category": None,
           "confidence": round(confidence, 2),
           "why": "", "prev_similar": "none",
           "repetition_penalty": 0.0, "final_score": round(confidence, 2),
           "reason": reason, "decision": "NO SFX"}
    if debug_extra:
        dbg.update(debug_extra)
    d["debug"] = dbg
    return d


def _anchor_time(ctx: SceneContext, event: AudioEvent, cat: SFXCategory,
                 duration: float) -> float:
    if event.anchor == "cut":
        return max(0.0, ctx.start - 0.08)          # pre-roll; peak on cut
    if event.anchor == "end":
        return max(ctx.start, ctx.end - duration)  # riser lands on payoff
    if event.anchor == "start":
        return ctx.start + 0.05
    at = ctx.start + ctx.duration * event.anchor_frac - cat.peak_offset
    return max(ctx.start, at)


def _decide_event(ctx, event, state, scene_id, config, history):
    """Candidate generation -> ranking -> repetition check -> final."""
    cands = candidates_for(event)
    if not cands:
        return _no_sfx(
            f"Event '{event.event_type}' detected but category "
            f"'{event.category_hint}' has no suitable asset — no wrong "
            f"sound will play.", confidence=event.visual * 0.5,
            classification=event.classification, event=event)

    ranked = []
    blocked_reasons = []
    for cat_id, asset in cands:
        r = rank_candidate(event, cat_id, asset, ctx, history, config)
        if r["blocked"]:
            blocked_reasons.append(r["blocked"])
            continue
        ranked.append((r["factors"]["final"], cat_id, asset, r["factors"]))
    if not ranked:
        return _no_sfx(
            "All candidates blocked by repetition rules: "
            + "; ".join(blocked_reasons[:2]),
            confidence=0.5, classification=event.classification, event=event,
            debug_extra={"prev_similar": history.prev_similar_text(
                cands[0][1], cands[0][0])})

    ranked.sort(key=lambda t: -t[0])
    final, cat_id, asset, factors = ranked[0]
    threshold = _threshold_for(event, config)
    if final < threshold:
        return _no_sfx(
            f"Best candidate '{asset}' scored {final:.2f}, below the "
            f"{threshold:.2f} threshold — weak match not forced.",
            confidence=final, classification=event.classification,
            event=event,
            debug_extra={
                "selected_sfx": asset, "category": cat_id,
                "repetition_penalty": factors["repetition_penalty"],
                "final_score": final,
                "prev_similar": history.prev_similar_text(asset, cat_id)})

    # Budget check (section K): safety limits, not targets.
    ok, why_not = history.budget_allows(ctx.start, event.cinematic,
                                        event.classification)
    if not ok:
        return _no_sfx(f"Global SFX budget reached ({why_not}); "
                       f"event not important enough to override.",
                       confidence=final, classification=event.classification,
                       event=event,
                       debug_extra={"selected_sfx": asset, "category": cat_id,
                                    "final_score": final})

    cat = CATEGORIES[cat_id]
    duration = ctx.duration if (cat.ambience or cat.default_duration <= 0) \
        else min(cat.default_duration, ctx.duration)
    volume = cat.default_volume * event.volume_scale
    duck_db = config.narration_duck_db if ctx.narration_present else 0.0
    start_time = _anchor_time(ctx, event, cat, duration)
    base = {
        "type": event.event_type, "sfx_category": cat_id, "sfx": asset,
        "start_time": round(start_time, 3),
        "duration": round(duration, 3),
        "volume": round(max(0.05, min(1.0, volume)), 3),
        "gain_db": volume_to_gain_db(volume, duck_db),
        "fade_in": round(min(0.05, duration * 0.2), 3),
        "fade_out": round(min(0.4, duration * 0.3), 3),
    }
    events = [base]
    if event.layer:  # rule 8: layer only when the event needs it (one max)
        for partner in cat.layer_with:
            pcat = CATEGORIES.get(partner)
            passet = resolve_asset(partner)
            if not pcat or not passet or passet == asset:
                continue
            events.append({
                "type": event.event_type, "sfx_category": partner,
                "sfx": passet, "start_time": round(start_time, 3),
                "duration": round(duration, 3),
                "volume": round(max(0.05, min(1.0, volume * 0.6)), 3),
                "gain_db": volume_to_gain_db(volume * 0.6, duck_db),
                "fade_in": round(min(0.1, duration * 0.2), 3),
                "fade_out": round(min(0.6, duration * 0.3), 3)})
            break

    for e in events:
        history.record(SFXRecord(
            file=e["sfx"], category=e["sfx_category"],
            event_type=event.event_type, scene_id=scene_id,
            start=e["start_time"], end=e["start_time"] + e["duration"],
            confidence=final, classification=event.classification,
            cinematic=event.cinematic))

    why = (f"{event.description} "
           f"(visual {event.visual:.2f}, semantic {event.semantic:.2f}, "
           f"action {event.action:.2f})")
    return {
        "sfx_required": True, "reason": why,
        "confidence": final, "classification": event.classification,
        "events": events,
        "debug": {
            "detected_event": event.event_type,
            "event_description": event.description,
            "classification": event.classification,
            "selected_sfx": asset, "category": cat_id,
            "confidence": final, "why": why,
            "prev_similar": history.prev_similar_text(asset, cat_id),
            "repetition_penalty": factors["repetition_penalty"],
            "final_score": final, "reason": why,
            "decision": "USE SFX"},
    }


def _audio_env(ctx, history):
    return {"narration": ctx.narration_present,
            "recent_events": history.total_events()}


def decide_scene(ctx: SceneContext, state: dict) -> dict:
    """Decision-layer entry for ONE scene. `state` carries the global
    SFXHistory under state["history"] (created on first use)."""
    config = getattr(state.get("history"), "config", None) or DEFAULT_CONFIG
    history = state.setdefault("history", SFXHistory(config))
    env = _audio_env(ctx, history)
    primary = pick_primary(detect_events(ctx, env=env))
    if primary is None:
        d = _no_sfx(NO_SFX_DECISION["reason"], confidence=0.2)
    elif primary.classification == "NONE":
        d = _no_sfx(primary.description, confidence=primary.confidence,
                    event=primary)
    else:
        d = _decide_event(ctx, primary, state, ctx.scene_id, config, history)
    d["debug"]["scene"] = ctx.scene_id
    return d


# ---------------------------------------------------------------------------
# Transitions — cinematic sounds are NOT defaults (section I)
# ---------------------------------------------------------------------------
_TRANSITION_MOTION = ["whip", "swipe", "wipe", "zoom", "pan", "aerial",
                      "drone", "fast", "montage", "spin", "flyover"]


def _transition_justification(prev: SceneContext, ctx: SceneContext) -> float:
    score = 0.40
    pv = (prev.visual_text or "").lower()
    cv = (ctx.visual_text or "").lower()
    if any(_wb(pv, w) or _wb(cv, w) for w in _TRANSITION_MOTION):
        score += 0.35
    if prev.topic != ctx.topic and "general" not in (prev.topic, ctx.topic):
        score += 0.20
    if any(v == "camera_move" for v, _ in ctx.actions):
        score += 0.15
    return min(1.0, score)


def _place_transitions(contexts, decisions, history, config):
    if not config.enable_transition_sfx:
        return
    for i in range(1, len(contexts)):
        ctx, prev = contexts[i], contexts[i - 1]
        just = _transition_justification(prev, ctx)
        ev = AudioEvent(
            "transition_cut", "Scene cut at %.2fs." % ctx.start, OPTIONAL,
            category_hint="transitions", anchor="cut",
            semantic=round(just, 2),
            visual=0.9 if just >= 0.75 else (0.7 if just >= 0.55 else 0.4),
            action=0.7, timing=1.0,  # the cut frame is an exact anchor
            context=0.7, cinematic=True, justification=just)
        at = round(max(0.0, ctx.start - 0.08), 3)
        # one SFX per beat: skip when a scene SFX already owns the cut
        if any(abs(e["start_time"] - at) < 0.6
               for d in decisions if d for e in d["events"]):
            continue
        # max 1 transition SFX per 8 s (section K)
        if history.transitions_in_window(at - 8.0, at):
            continue
        cands = candidates_for(ev)
        if not cands:
            continue
        cat_id, asset = cands[0]
        r = rank_candidate(ev, cat_id, asset, ctx, history, config)
        if r["blocked"]:
            continue
        final = r["factors"]["final"]
        if final < _threshold_for(ev, config):
            continue
        ok, _ = history.budget_allows(at, True, OPTIONAL)
        if not ok:
            continue
        cat = CATEGORIES[cat_id]
        duck = config.narration_duck_db if ctx.narration_present else 0.0
        decisions[i]["events"].append({
            "type": "transition_cut", "sfx_category": cat_id, "sfx": asset,
            "start_time": at, "duration": cat.default_duration,
            "volume": round(cat.default_volume, 3),
            "gain_db": volume_to_gain_db(cat.default_volume, duck),
            "fade_in": 0.05, "fade_out": 0.15})
        history.record(SFXRecord(
            file=asset, category=cat_id, event_type="transition_cut",
            scene_id=i, start=at, end=at + cat.default_duration,
            confidence=final, classification=OPTIONAL, cinematic=True))
        # NOTE: decisions[i]["sfx_required"] is untouched — the whoosh
        # belongs to the edit boundary, not the scene's own content.


# ---------------------------------------------------------------------------
# Video-level planning
# ---------------------------------------------------------------------------
def _is_new_instance(ctx) -> bool:
    """Explicit new occurrence of an event ('another explosion') vs the
    same event continuing across scenes."""
    if not ctx.action_repeat:
        return False
    return any(s == "visual" for _, s in ctx.actions)


def _group_consecutive(contexts, primaries, config):
    """Section G: consecutive scenes showing the SAME event become ONE
    continuous event instead of one SFX per scene."""
    groups = []  # [(scene_indices, AudioEvent|None)]
    for i, ev in enumerate(primaries):
        if ev is None or ev.classification == "NONE" or \
                ev.event_type == "transition_cut":
            groups.append(([i], ev))
            continue
        if groups and groups[-1][1] is not None \
                and groups[-1][1].classification != "NONE" \
                and groups[-1][1].event_type == ev.event_type \
                and groups[-1][1].category_hint == ev.category_hint:
            prev_i = groups[-1][0][-1]
            gap = contexts[i].start - contexts[prev_i].end
            if gap < config.merge_gap_s and not _is_new_instance(contexts[i]):
                groups[-1][0].append(i)
                continue
        groups.append(([i], ev))
    return groups


def _merged_no_sfx(first_idx, idx):
    d = _no_sfx("Same event continues from the previous scene — merged "
                "into one continuous SFX instead of a new one per scene.",
                confidence=0.7, event=None)
    d["debug"]["detected_event"] = "continued_event"
    return d


def plan_video_sfx(contexts, seed=0, debug=False, config=None):
    """Full decision-layer pipeline. Returns (decisions, events); events
    are in the legacy pipeline format plus duration/fade keys."""
    config = config or DEFAULT_CONFIG
    history = SFXHistory(config)
    state = {"history": history}
    for i, c in enumerate(contexts):
        c.prev = contexts[i - 1] if i else None
        c.next = contexts[i + 1] if i + 1 < len(contexts) else None
        c.scene_id = i

    # 1-2. event detection + primary per scene (no placement yet)
    primaries = []
    for c in contexts:
        env = _audio_env(c, history)
        primaries.append(pick_primary(detect_events(c, env=env)))

    # 3. dedup: merge consecutive same-event scenes (section G)
    groups = _group_consecutive(contexts, primaries, config)
    decisions = [None] * len(contexts)
    for idxs, ev in groups:
        first = idxs[0]
        if ev is None or ev.classification == "NONE":
            for i in idxs:
                c = contexts[i]
                if ev is None:
                    decisions[i] = _no_sfx(
                        NO_SFX_DECISION["reason"], confidence=0.2)
                else:
                    decisions[i] = _no_sfx(
                        ev.description, confidence=ev.confidence, event=ev)
                decisions[i]["debug"]["scene"] = i
            continue
        # one decision for the whole span: extend the first scene's window
        span = replace(contexts[first],
                       end=contexts[idxs[-1]].end)
        span.duration = max(0.2, span.end - span.start)
        d = _decide_event(span, ev, state, first, config, history)
        d["debug"]["scene"] = first
        d["debug"]["merged_scenes"] = idxs
        decisions[first] = d
        for i in idxs[1:]:
            decisions[i] = _merged_no_sfx(first, i)
            decisions[i]["debug"]["scene"] = i

    # 4. transitions (cinematic — high bar, section I)
    _place_transitions(contexts, decisions, history, config)

    # per-event confidence/classification for the cap sort below
    for d in decisions:
        for e in d["events"]:
            e.setdefault("confidence", d.get("confidence", 0.5))
            e.setdefault("classification",
                         d.get("classification", OPTIONAL))

    # 5. global safety cap ~10 cues/min (never artificially overloaded)
    total = sum(len(d["events"]) for d in decisions)
    if contexts:
        span_min = max(0.25, (contexts[-1].end - contexts[0].start) / 60.0)
        cap = int(span_min * 10) + 2
        if total > cap:
            flat = [(di, e) for di, d in enumerate(decisions)
                    for e in d["events"]]
            # drop lowest-confidence, non-REQUIRED first
            flat.sort(key=lambda p: (
                p[1].get("classification", OPTIONAL) == REQUIRED,
                p[1].get("confidence", 0.5)))
            drop_ids = set(id(e) for _, e in flat[:(total - cap)])
            for d in decisions:
                d["events"] = [e for e in d["events"]
                               if id(e) not in drop_ids]
                if not d["events"] and d["sfx_required"]:
                    scene_no = d.get("debug", {}).get("scene", "?")
                    d.update(_no_sfx("Dropped by the global density cap; "
                                     "restraint beats overload.",
                                     confidence=0.4))
                    d["debug"]["scene"] = scene_no

    events = []
    for d in decisions:
        for e in d["events"]:
            events.append({"time": e["start_time"], "sfx": e["sfx"],
                           "gain_db": e["gain_db"],
                           "duration": e["duration"],
                           "fade_in": e["fade_in"], "fade_out": e["fade_out"],
                           "sfx_category": e["sfx_category"]})
    events.sort(key=lambda e: e["time"])
    if debug or config.debug:
        for d in decisions:
            d["debug"]["enabled"] = True
    return decisions, events


# ---------------------------------------------------------------------------
# Debug mode (section M) — optional per-scene log
# ---------------------------------------------------------------------------
def format_debug_log(decisions) -> str:
    """Render the user's debug format, one block per scene."""
    blocks = []
    for d in decisions:
        g = d.get("debug", {})
        blocks.append(
            "Scene {scene}\n"
            "Detected event: {ev}\n"
            "Selected SFX: {sfx}\n"
            "Category: {cat}\n"
            "Confidence: {conf:.2f}\n"
            "Why selected: {why}\n"
            "Previous similar SFX: {prev}\n"
            "Repetition penalty: {rep:.2f}\n"
            "Final score: {score:.2f}\n"
            "Reason: {reason}\n"
            "Decision: {dec}".format(
                scene=g.get("scene", "?"),
                ev=g.get("detected_event") or "None",
                sfx=g.get("selected_sfx") or "None",
                cat=g.get("category") or "-",
                conf=g.get("confidence", 0.0),
                why=g.get("why") or "-",
                prev=g.get("prev_similar", "none"),
                rep=g.get("repetition_penalty", 0.0),
                score=g.get("final_score", g.get("confidence", 0.0)),
                reason=d.get("reason", ""),
                dec=g.get("decision", "NO SFX")))
    return "\n\n".join(blocks)
