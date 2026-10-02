"""Universal Semantic SFX Engine.

Decides, per scene, whether a sound effect is needed and — if so — which
one, when, how loud, and for how long. Works across ALL video niches:
there are no niche-specific hardcoded rules anywhere in this module.

Four inputs per scene (derived from data the pipeline already has):
  1. SCRIPT/NARRATION — scene text, keywords, numbers, phrases
  2. VISUAL CONTENT  — clip metadata (Asset.tags + Asset.query)
  3. VISUAL ACTION    — action verbs (narration) + motion words (visual tags);
                       marked LOW confidence when narration-only
  4. SCENE MOOD      — per-sentence mood + video-level mood

Core principle: semantic EVENTS, not keywords. A detector fires only when
the signals describe a real sound-producing event. When in doubt the
engine decides NO SFX (confidence < 0.60).

Decision format per scene:
  {"sfx_required": bool, "reason": str, "confidence": float,
   "events": [{"type", "sfx_category", "start_time", "duration",
               "volume", "fade_in", "fade_out"}]}
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Confidence bands (user spec)
# ---------------------------------------------------------------------------
CONF_VERY_STRONG = 0.90   # 0.90 - 1.00
CONF_STRONG = 0.75        # 0.75 - 0.89
CONF_POSSIBLE = 0.60      # 0.60 - 0.74
CONF_FLOOR = 0.60         # below -> prefer NO SFX (rule 15)

# ---------------------------------------------------------------------------
# Taxonomy registry — extensible via register_category() with NO core changes
# ---------------------------------------------------------------------------
@dataclass
class SFXCategory:
    id: str
    label: str
    description: str
    # sfx names (audio_design.SFX_BUILDERS keys) in preference order.
    # Real recordings first. EMPTY list + no synth_fallback => the engine
    # will never play this category (rule: never play a wrong sound).
    assets: list = field(default_factory=list)
    default_duration: float = 1.0
    default_volume: float = 0.5          # linear 0..1, pre-ducking
    peak_offset: float = 0.05            # peak sits this far into the sound
    layer_with: list = field(default_factory=list)  # category ids
    ambience: bool = False               # continuous bed while scene lasts


CATEGORIES: dict[str, SFXCategory] = {}


def register_category(cat: SFXCategory) -> None:
    """Add (or replace) a taxonomy category without touching core engine."""
    CATEGORIES[cat.id] = cat


def _reg(cid, label, desc, assets, dur=1.0, vol=0.5, peak=0.05,
         layer=(), amb=False):
    register_category(SFXCategory(cid, label, desc, list(assets), dur, vol,
                                  peak, list(layer), amb))


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
     vol=0.35)
_reg("camera_move", "Camera movement", "pans, aerials, drone moves",
     ["whoosh"], dur=0.8, vol=0.35)
_reg("zoom", "Zoom", "zoom in/out motion", ["swoosh"], dur=0.6, vol=0.32)
# --- Editorial --------------------------------------------------------------------------
_reg("transitions", "Transitions", "scene cuts and wipes", ["whoosh"],
     dur=0.45, vol=0.30, peak=0.22)
_reg("reveals", "Reveals", "big on-screen reveals", ["impact"], dur=1.0,
     vol=0.45, peak=0.05)
_reg("tension", "Tension", "build-up before a payoff", ["riser"], dur=1.6,
     vol=0.45, peak=1.4)
_reg("suspense", "Suspense", "uneasy beds", ["riser"], dur=2.0, vol=0.35,
     peak=1.8)
_reg("mystery", "Mystery", "quiet intrigue", ["riser"], dur=2.0, vol=0.28)
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
# Scene context — the four inputs
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


# Action verb families. "narrated" = verb in narration only (LOWER
# confidence); "visual" = motion word in the clip metadata itself.
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
}
_MOTION_TAGS = ["zoom", "pan", "aerial", "drone", "driving", "flying",
                "moving", "motion", "tracking", "orbit", "timelapse",
                "slowmo", "slow-motion"]
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
    for m in _MOTION_TAGS:
        if _wb(vis, m) and not any(v == "zoom" and m == "zoom" for v, _ in found):
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
        ctxs.append(SceneContext(
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
        ))
    for c in ctxs:
        c.duration = max(0.2, c.end - c.start)
    return ctxs


# ---------------------------------------------------------------------------
# Detector candidates
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    category: str | None     # None => explicit NO-SFX verdict
    confidence: float
    reason: str
    anchor: str = "action"   # action | start | end | cut
    anchor_frac: float = 0.25
    volume_scale: float = 1.0
    layer: bool = False


def _acts(ctx, *verbs):
    return [(v, s) for v, s in ctx.actions if v in verbs]


def _vhas(ctx, *words):
    vt = ctx.visual_text.lower()
    return any(_wb(vt, w) for w in words)


def _nhas(ctx, *words):
    nt = ctx.narration.lower()
    return any(_wb(nt, w) for w in words)


# --- individual detectors: semantic events, never bare keywords -------------
def _d_talking_head(ctx):
    """Person standing and talking, no meaningful action -> NO SFX."""
    if _vhas(ctx, "talking", "speaking", "interview", "anchor", "podcast",
             "person talking", "man speaking", "woman speaking"):
        if not _acts(ctx, "open", "close", "slam", "break", "launch",
                     "explode", "hit", "fall", "drive", "fly", "cheer",
                     "appear", "zoom"):
            return Candidate(None, 0.95,
                             "Person talking on screen; no meaningful "
                             "physical action.", anchor="start")
    return None


def _d_explosion(ctx):
    vis = _vhas(ctx, "explosion", "blast", "missile", "airstrike",
                "bombing", "fireball", "detonat")
    narr = _nhas(ctx, "explosion", "detonat", "airstrike", "bombing")
    acts = _acts(ctx, "explode")
    if vis:
        # aftermath / ruins / debris WITHOUT active fire = the event
        # already passed; a new boom here would mistime the sound
        # (rule 9/10). Active fire keeps it a live event.
        vt = ctx.visual_text.lower()
        aftermath = any(_wb(vt, w) for w in
                        ("aftermath", "ruins", "rubble", "debris"))
        live_fire = any(_wb(vt, w) for w in
                        ("fire", "fireball", "burning", "flames"))
        if aftermath and not live_fire:
            return Candidate("explosions", 0.55,
                             "Explosion aftermath on screen; the event "
                             "already passed.", anchor="action",
                             anchor_frac=0.15)
        if narr or acts:
            return Candidate("explosions", 0.95,
                             "Explosion visible on screen, confirmed by "
                             "narration.", anchor="action", anchor_frac=0.15)
        return Candidate("explosions", 0.85,
                         "Explosion visible on screen.", anchor="action",
                         anchor_frac=0.15)
    return None  # narration-only mention stays below the floor (rule 15)


def _d_crowd(ctx):
    if _vhas(ctx, "crowd", "protest", "rally", "audience", "demonstration",
             "march", "supporters", "cheering"):
        return Candidate("crowd", 0.85, "Crowd visible on screen.",
                         anchor="start", volume_scale=0.9)
    return None


def _d_door(ctx):
    if not _vhas(ctx, "door", "gate", "entrance"):
        return None
    acts = _acts(ctx, "open", "close", "slam")
    if acts and acts[0][1] == "visual":
        return Candidate("doors", 0.92, "Door action visible on screen.",
                         anchor="action", anchor_frac=0.3)
    if acts:
        return Candidate("doors", 0.78,
                         "Door on screen; opening/closing narrated.",
                         anchor="action", anchor_frac=0.3)
    return Candidate("doors", 0.62, "Door visible; movement implied.",
                     anchor="action", anchor_frac=0.4, volume_scale=0.7)


def _d_glass(ctx):
    acts = _acts(ctx, "break")
    if acts and _vhas(ctx, "glass", "window", "mirror"):
        conf = 0.90 if acts[0][1] == "visual" else 0.80
        return Candidate("glass", conf, "Glass breaking on screen.",
                         anchor="action", anchor_frac=0.3)
    if _nhas(ctx, "shattered", "smashed") and _vhas(ctx, "glass"):
        return Candidate("glass", 0.72, "Shattered glass shown.",
                         anchor="action", anchor_frac=0.3)
    return None


def _d_thunder(ctx):
    if _vhas(ctx, "lightning", "thunderstorm", "storm"):
        return Candidate("weather", 0.85, "Lightning/storm visible.",
                         anchor="action", anchor_frac=0.2)
    return None


def _d_rocket(ctx):
    if not _vhas(ctx, "rocket", "spacecraft", "missile", "shuttle"):
        return None
    acts = _acts(ctx, "launch")
    if acts:
        return Candidate("space", 0.93, "Rocket launch on screen.",
                         anchor="action", anchor_frac=0.1, layer=True)
    return Candidate("space", 0.66, "Rocket on screen; launch implied.",
                     anchor="action", anchor_frac=0.2, volume_scale=0.7)


def _d_map_zoom(ctx):
    if not _vhas(ctx, "map"):
        return None
    acts = _acts(ctx, "zoom")
    if acts:
        conf = 0.82 if acts[0][1] == "visual" else 0.74
        return Candidate("zoom", conf, "Map zoom motion.", anchor="action",
                         anchor_frac=0.2, volume_scale=0.8)
    if ("camera_move", "visual") in ctx.actions:
        return Candidate("maps", 0.68, "Map with camera motion.",
                         anchor="action", anchor_frac=0.2, volume_scale=0.7)
    return None


def _d_photo(ctx):
    """Historical photograph: NO random sound. Only a subtle shutter when
    the photo visibly appears/lands — otherwise nothing."""
    if not _vhas(ctx, "photo", "photograph", "archival", "portrait",
                 "historical"):
        return None
    acts = _acts(ctx, "appear")
    if acts:
        return Candidate("ui", 0.62,
                         "Photograph appears; subtle shutter justified.",
                         anchor="action", anchor_frac=0.2, volume_scale=0.5)
    return Candidate(None, 0.80,
                     "Still photograph on screen; no sound event.")


def _d_stat(ctx):
    """Important number/text: subtle click ONLY when the visual animation
    supports it. Visual-animation signal is weak in this pipeline, so we
    require a magnitude number + emphasis context, else NO SFX."""
    if not ctx.numbers:
        return None
    emphasis = _nhas(ctx, "revealed", "announced", "confirmed", "record",
                     "broke", "soaring", "plummet", "unprecedented")
    visual_text_cue = _vhas(ctx, "text", "graphic", "stat", "chart",
                            "infographic", "title")
    if emphasis and visual_text_cue:
        return Candidate("ui", 0.66,
                         "Key figure emphasized with on-screen graphic.",
                         anchor="action", anchor_frac=0.3, volume_scale=0.6)
    return None


def _d_alarm(ctx):
    if _vhas(ctx, "siren", "police", "ambulance", "emergency", "fire truck"):
        return Candidate("sirens", 0.85, "Emergency vehicle on screen.",
                         anchor="start", volume_scale=0.9)
    return None


def _d_tension(ctx):
    if ctx.mood in ("suspense", "dramatic", "tense") and _nhas(
            ctx, "but then", "suddenly", "what happened next", "the truth",
            "nobody expected"):
        return Candidate("tension", 0.70, "Tension build before a payoff.",
                         anchor="end", volume_scale=0.8)
    return None


def _d_vehicle(ctx):
    # No suitable vehicle assets exist -> honest NO SFX (rule 15).
    return None


DETECTORS = [_d_talking_head, _d_explosion, _d_crowd, _d_door, _d_glass,
             _d_thunder, _d_rocket, _d_map_zoom, _d_photo, _d_stat,
             _d_alarm, _d_tension, _d_vehicle]


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


def _anchor_time(ctx: SceneContext, cand: Candidate, cat: SFXCategory,
                 duration: float) -> float:
    if cand.anchor == "cut":
        return max(0.0, ctx.start - 0.08)          # pre-roll; peak on cut
    if cand.anchor == "end":
        return max(ctx.start, ctx.end - duration)  # riser lands on payoff
    if cand.anchor == "start":
        return ctx.start + 0.05
    # "action": peak of the sound lands on the visual action moment
    at = ctx.start + ctx.duration * cand.anchor_frac - cat.peak_offset
    return max(ctx.start, at)


def decide_scene(ctx: SceneContext, state: dict) -> dict:
    """Full 16-rule decision for one scene. `state` carries cross-scene
    memory (recent categories, event times) and is mutated."""
    cands = [c for d in DETECTORS if (c := d(ctx)) is not None]
    # explicit NO-SFX verdicts (talking head, still photo, ...)
    vetoes = [c for c in cands if c.category is None]
    cands = [c for c in cands if c.category is not None]

    # asset check FIRST: drop anything with no suitable sound (rule 15)
    viable = []
    for c in cands:
        if resolve_asset(c.category) is None:
            continue
        viable.append(c)
    viable.sort(key=lambda c: -c.confidence)
    best = viable[0] if viable else None

    # a strong explicit NO-SFX beats a weak candidate
    for v in vetoes:
        if v.confidence >= CONF_VERY_STRONG and (
                best is None or best.confidence < CONF_VERY_STRONG):
            return {**NO_SFX_DECISION, "confidence": round(v.confidence, 2),
                    "reason": v.reason, "events": []}

    if best is None or best.confidence < CONF_FLOOR:
        reason = NO_SFX_DECISION["reason"]
        if best is not None:
            reason = (f"Best candidate '{best.category}' confidence "
                      f"{best.confidence:.2f} below floor 0.60.")
        return {**NO_SFX_DECISION, "confidence":
                round(best.confidence, 2) if best else 0.0,
                "reason": reason, "events": []}

    # rule 13/14: no repeat of the same category across consecutive scenes
    # unless the action genuinely repeats. A second showing of the same
    # event (e.g. another angle of the same explosion) is not a new event.
    recent = state.get("recent_cats", [])
    if best.category in recent[-2:] and not ctx.action_repeat \
            and best.confidence <= 0.85:
        return {**NO_SFX_DECISION, "confidence": round(best.confidence, 2),
                "reason": f"'{best.category}' already used in a recent "
                          f"scene; action does not repeat.", "events": []}

    cat = CATEGORIES[best.category]
    asset = resolve_asset(best.category)
    # rule 11: duration matches the event; rule 12: ambience beds run
    # continuously while the environment lasts
    duration = ctx.duration if (cat.ambience or cat.default_duration <= 0) \
        else min(cat.default_duration, ctx.duration)

    # rules 5/6: never overpower narration — duck when voice is present
    volume = cat.default_volume * best.volume_scale
    duck_db = -4.0 if ctx.narration_present else 0.0

    start_time = _anchor_time(ctx, best, cat, duration)
    events = [{
        "type": best.category,
        "sfx_category": best.category,
        "sfx": asset,
        "start_time": round(start_time, 3),
        "duration": round(duration, 3),
        "volume": round(max(0.05, min(1.0, volume)), 3),
        "gain_db": volume_to_gain_db(volume, duck_db),
        "fade_in": round(min(0.05, duration * 0.2), 3),
        "fade_out": round(min(0.4, duration * 0.3), 3),
    }]
    # rule 8: layer only when the event realistically needs multiple sounds
    if best.layer:
        for partner in cat.layer_with:
            pcat = CATEGORIES.get(partner)
            passet = resolve_asset(partner)
            if not pcat or not passet or passet == asset:
                continue
            events.append({
                "type": partner, "sfx_category": partner, "sfx": passet,
                "start_time": round(start_time, 3),
                "duration": round(duration, 3),
                "volume": round(max(0.05, min(1.0, volume * 0.6)), 3),
                "gain_db": volume_to_gain_db(volume * 0.6, duck_db),
                "fade_in": round(min(0.1, duration * 0.2), 3),
                "fade_out": round(min(0.6, duration * 0.3), 3),
            })
            break  # rule 7: never stack excessively — one layer max

    state.setdefault("recent_cats", []).append(best.category)
    state.setdefault("event_times", []).append(start_time)
    return {"sfx_required": True, "reason": best.reason,
            "confidence": round(best.confidence, 2), "events": events}


# ---------------------------------------------------------------------------
# Video-level planning: transitions, density cap, one-per-beat
# ---------------------------------------------------------------------------
def plan_video_sfx(contexts, seed=0):
    """Decide SFX for every scene. Returns (decisions, events) where events
    are in the legacy pipeline format plus duration/fade keys."""
    state = {"recent_cats": [], "event_times": []}
    decisions = [decide_scene(c, state) for c in contexts]

    # transition whooshes on cuts (rule: peak on the cut frame, pre-roll;
    # skip when another SFX already owns the beat)
    for i, ctx in enumerate(contexts):
        if i == 0:
            continue
        at = round(max(0.0, ctx.start - 0.08), 3)
        if any(abs(e["start_time"] - at) < 0.6
               for d in decisions for e in d["events"]):
            continue
        cat = CATEGORIES["transitions"]
        asset = resolve_asset("transitions")
        if asset is None:
            continue
        decisions[i]["events"].append({
            "type": "transitions", "sfx_category": "transitions",
            "sfx": asset, "start_time": at, "duration": 0.45,
            "volume": 0.30, "gain_db": volume_to_gain_db(0.30, -4.0),
            "fade_in": 0.05, "fade_out": 0.15})
        # NOTE: sfx_required stays as the scene-content decision — the
        # whoosh belongs to the edit boundary, not the scene itself.

    # density cap ~10 cues/min (rule: never sound artificially overloaded)
    total = sum(len(d["events"]) for d in decisions)
    if contexts:
        span_min = max(0.25, (contexts[-1].end - contexts[0].start) / 60.0)
        cap = int(span_min * 10) + 2
        if total > cap:
            # drop quietest transition whooshes first
            for d in decisions:
                tr = [e for e in d["events"]
                      if e["sfx_category"] == "transitions"]
                keep = [e for e in d["events"]
                        if e["sfx_category"] != "transitions"]
                d["events"] = keep + tr[:1]
            total = sum(len(d["events"]) for d in decisions)
            if total > cap:  # still over: trim from the end
                flat = [(di, e) for di, d in enumerate(decisions)
                        for e in d["events"]]
                drop = set(id(e) for _, e in
                           sorted(flat, key=lambda p: -p[1]["gain_db"])
                           [:(total - cap)])
                for d in decisions:
                    d["events"] = [e for e in d["events"]
                                   if id(e) not in drop]
                    if not d["events"]:
                        d.update(NO_SFX_DECISION)
                        d["confidence"] = 0.0
                        d["events"] = []

    events = []
    for d in decisions:
        for e in d["events"]:
            events.append({"time": e["start_time"], "sfx": e["sfx"],
                           "gain_db": e["gain_db"],
                           "duration": e["duration"],
                           "fade_in": e["fade_in"], "fade_out": e["fade_out"],
                           "sfx_category": e["sfx_category"]})
    events.sort(key=lambda e: e["time"])
    return decisions, events
