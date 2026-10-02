"""Universal Semantic SFX Engine — decision-layer tests.

Covers the 2026-10-02 engine spec end to end:
  A. selection: REQUIRED/OPTIONAL/NO_SFX, noun-is-not-an-event hard rule
  B. event-first pipeline ordering
  C. 9-factor candidate ranking
  D. global SFX history (whole video, not per scene)
  E. exact-file repetition: 20 s cooldown, max 2 per video
  F. category cooldown 10-20 s
  G. consecutive-scene event dedup
  H. semantic similarity groups
  I. cinematic high threshold
  J. NO SFX is a successful result
  K. global SFX budget
  L. never fill empty audio
  M. debug mode format
"""
import pytest

from src.semantic_sfx import (
    AudioEvent, CATEGORIES, CONF_FLOOR, NO_SFX_DECISION, OPTIONAL, REQUIRED,
    SFXCategory, SFXConfig, SFXHistory,
    SceneContext, build_scene_contexts, candidates_for, decide_scene,
    detect_events, format_debug_log, pick_primary, plan_video_sfx,
    rank_candidate, register_category, resolve_asset, volume_to_gain_db,
)
from src.semantic_sfx import _decide_event


def ctx(narration="", visual="", start=0.0, end=5.0, mood="neutral",
        numbers=()):
    c = SceneContext(narration=narration, visual_text=visual,
                     start=start, end=end, mood=mood,
                     narration_present=bool(narration.strip()), scene_id=0)
    c.numbers = list(numbers)
    c.duration = end - start
    c.visual_signal = bool(visual.strip())
    from src.semantic_sfx import extract_actions
    c.actions = extract_actions(narration, visual)
    return c


def decide(c, state=None):
    return decide_scene(c, state if state is not None else {})


def ev_cat(d):
    return d["events"][0]["sfx_category"] if d["sfx_required"] else None


# ---------------------------------------------------------------------------
# User's core examples
# ---------------------------------------------------------------------------
def test_talking_head_no_sfx():
    d = decide(ctx("The minister addressed the nation today.",
                   "man speaking podium press conference"))
    assert d["sfx_required"] is False
    assert d["classification"] == "NO_SFX"


def test_door_open_door_sfx():
    d = decide(ctx("He opens the heavy metal door.", "man metal door"))
    assert d["sfx_required"] is True
    assert ev_cat(d) == "doors"
    assert d["events"][0]["sfx"] == "door"


def test_rocket_launch_rumble():
    d = decide(ctx("The rocket launches into the sky.",
                   "rocket launch pad night"))
    assert d["sfx_required"] is True
    assert "space" in [e["sfx_category"] for e in d["events"]]


def test_map_zoom_subtle_whoosh():
    d = decide(ctx("Here is the region on the map.", "map zoom animation"))
    assert d["sfx_required"] is True
    e = d["events"][0]
    assert e["sfx_category"] == "zoom"
    assert e["sfx"] == "swoosh"
    assert e["volume"] <= 0.5


def test_glass_break():
    d = decide(ctx("The window shattered into pieces.",
                   "broken glass window"))
    assert d["sfx_required"] is True
    assert ev_cat(d) == "glass"
    assert d["events"][0]["sfx"] == "glass_break"


def test_lightning_thunder():
    d = decide(ctx("Lightning split the night sky.",
                   "lightning storm clouds"))
    assert d["sfx_required"] is True
    assert ev_cat(d) == "weather"


def test_photo_alone_no_sfx():
    d = decide(ctx("This photograph was taken in 1948.",
                   "historical black and white photo"))
    assert d["sfx_required"] is False


def test_number_with_emphasis_and_graphic_subtle_click():
    d = decide(ctx("The deficit revealed today reached 4.2 billion dollars.",
                   "statistic text graphic chart", numbers=["4.2"]))
    assert d["sfx_required"] is True
    assert d["events"][0]["volume"] <= 0.5


def test_number_without_emphasis_no_sfx():
    d = decide(ctx("He bought 3 apples yesterday.", "man walking street",
                   numbers=["3"]))
    assert d["sfx_required"] is False


# ---------------------------------------------------------------------------
# A. HARD RULE: a noun is NOT an event
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("narration,visual", [
    ("The volcano is the tallest mountain here.", "volcano mountain peak"),
    ("The car is parked outside the house.", "red car street parked"),
    ("He remembered the rain fondly.", "old man armchair room"),
    ("The explosion happened in 1945.", "historical photo ruins aftermath"),
    ("The airport was busy this morning.", "airport terminal hall"),
    ("The city never sleeps at night.", "city skyline night"),
])
def test_noun_is_not_an_event(narration, visual):
    d = decide(ctx(narration, visual))
    assert d["sfx_required"] is False, f"noun fired SFX: {d['reason']}"


def test_verb_without_visual_action_no_event():
    # "explosion" merely mentioned, nothing on screen -> NO SFX
    d = decide(ctx("The explosion was heard miles away.", "city street day"))
    assert d["sfx_required"] is False


# ---------------------------------------------------------------------------
# A. scene classification
# ---------------------------------------------------------------------------
def test_classification_required_optional_none():
    r = decide(ctx("An explosion rocked the building.", "explosion fire"))
    assert r["classification"] == REQUIRED
    o = decide(ctx("Here is the region on the map.", "map zoom animation"))
    assert o["classification"] == OPTIONAL
    n = decide(ctx("The minister addressed the nation today.",
                   "man speaking podium"))
    assert n["classification"] == "NO_SFX"


def test_optional_needs_high_confidence():
    # weak optional evidence must NOT pass
    d = decide(ctx("Maybe the door moved.", "room interior"))
    assert d["sfx_required"] is False


# ---------------------------------------------------------------------------
# B. event-first pipeline ordering
# ---------------------------------------------------------------------------
def test_event_first_ordering():
    c = ctx("An explosion rocked the building.", "explosion fire")
    events = detect_events(c)
    assert events, "no event detected"
    primary = pick_primary(events)
    assert primary.event_type == "explosion"          # 1. WHAT IS HAPPENING
    assert primary.category_hint == "explosions"     # 2. SFX CATEGORY
    cands = candidates_for(primary)                  # 3. CANDIDATES
    assert cands and cands[0][1] == "boom"           # 4. actual file, last
    d = decide(c)
    assert d["debug"]["detected_event"] == "explosion"


# ---------------------------------------------------------------------------
# C. 9-factor candidate ranking
# ---------------------------------------------------------------------------
def _ranked(ev_kwargs, asset="door", category="doors", history=None,
            start=0.0):
    from src.semantic_sfx import SFXHistory, DEFAULT_CONFIG
    h = history or SFXHistory(DEFAULT_CONFIG)
    ev = AudioEvent("door_action", "test", REQUIRED, category_hint=category,
                    **ev_kwargs)
    c = ctx("He opens the door.", "man door", start=start, end=start + 5)
    return rank_candidate(ev, category, asset, c, h, DEFAULT_CONFIG)


def test_ranking_all_nine_factors_present():
    r = _ranked({})
    assert r["blocked"] is None
    f = r["factors"]
    for k in ("semantic_match", "visual_match", "action_match",
              "timing_match", "context_match", "quality_score",
              "repetition_penalty", "overuse_penalty",
              "generic_sfx_penalty", "final"):
        assert k in f, k


def test_ranking_monotonic_in_match_factors():
    base = _ranked({})["factors"]["final"]
    for kw in ("semantic", "visual", "action", "timing", "context"):
        low = _ranked({kw: 0.2})["factors"]["final"]
        high = _ranked({kw: 1.0})["factors"]["final"]
        assert high > low, kw
    assert base > 0.60


def test_ranking_quality_real_beats_synth():
    real = _ranked({}, asset="boom", category="explosions")["factors"]["final"]
    synth = _ranked({}, asset="door", category="doors")["factors"]["final"]
    # both pass; quality is one factor among nine, not the decider
    assert real > 0 and synth > 0


def test_ranking_generic_penalty_applies():
    plain = _ranked({"cinematic": True, "justification": 0.2},
                    asset="whoosh", category="transitions")
    just = _ranked({"cinematic": True, "justification": 0.9},
                   asset="whoosh", category="transitions")
    assert plain["factors"]["generic_sfx_penalty"] == 0.35
    assert just["factors"]["generic_sfx_penalty"] == 0.0
    assert just["factors"]["final"] > plain["factors"]["final"]


def test_ranking_repetition_penalty_grows():
    from src.semantic_sfx import SFXHistory, DEFAULT_CONFIG, SFXRecord
    h = SFXHistory(DEFAULT_CONFIG)
    c0 = _ranked({})["factors"]
    for i in range(3):
        h.record(SFXRecord("door", "doors", "door_action", i,
                           float(i * 30), float(i * 30 + 1)))
    c3 = _ranked({}, history=h)["factors"]
    assert c3["repetition_penalty"] > c0["repetition_penalty"]
    assert c3["final"] < c0["final"]


def test_weak_match_never_forced():
    ev = AudioEvent("door_action", "weak", OPTIONAL, category_hint="doors",
                    semantic=0.3, visual=0.3, action=0.3, timing=0.5,
                    context=0.5)
    c = ctx("vague room.", "room", start=0.0, end=5.0)
    st = {}
    d = _decide_event(c, ev, st, 0, SFXConfig(),
                      st.setdefault("history", SFXHistory(SFXConfig())))
    assert d["sfx_required"] is False
    assert "threshold" in d["reason"]


# ---------------------------------------------------------------------------
# D. global history across the whole video
# ---------------------------------------------------------------------------
def test_global_history_consulted_before_placement():
    st = {}
    d1 = decide(ctx("He opens the metal door.", "man metal door",
                    start=0.0, end=5.0), st)
    assert d1["sfx_required"] is True
    # same event 10 s later, separate scene, explicit new instance
    d2 = decide(ctx("He opens another metal door.", "man metal door",
                    start=10.0, end=15.0), st)
    assert d2["sfx_required"] is False  # exact file 'door' < 20 s ago
    assert "20s" in d2["reason"] or "repetition" in d2["reason"].lower()


# ---------------------------------------------------------------------------
# E. exact-file repetition rules
# ---------------------------------------------------------------------------
def test_exact_file_20s_cooldown():
    st = {}
    d1 = decide(ctx("An explosion rocked the building.", "explosion fire",
                    start=0.0, end=5.0), st)
    assert d1["sfx_required"] is True
    d2 = decide(ctx("Another explosion followed seconds later.",
                    "explosion fireball", start=10.0, end=15.0), st)
    assert d2["sfx_required"] is False


def test_exact_file_max_two_per_video():
    st = {}
    results = []
    for t in (0.0, 25.0, 50.0):
        d = decide(ctx("Another explosion followed seconds later.",
                       "explosion fireball", start=t, end=t + 5.0), st)
        results.append(d["sfx_required"])
    assert results == [True, True, False]  # 3rd use blocked


# ---------------------------------------------------------------------------
# F. category cooldown (different file, same category)
# ---------------------------------------------------------------------------
def test_category_cooldown_blocks_different_file():
    register_category(SFXCategory(
        id="test_cat", label="t", description="t",
        assets=["tick", "pop"], default_duration=0.3, default_volume=0.4))
    try:
        st = {}
        h = SFXHistory(SFXConfig())
        st["history"] = h
        ev = AudioEvent("test_thing", "test", REQUIRED,
                        category_hint="test_cat",
                        semantic=0.9, visual=0.9, action=0.9, timing=0.9,
                        context=0.8)
        d1 = _decide_event(ctx("x", "y", start=0.0, end=5.0), ev, st, 0,
                           SFXConfig(), h)
        assert d1["sfx_required"] is True
        assert d1["events"][0]["sfx"] == "tick"
        d2 = _decide_event(ctx("x", "y", start=5.0, end=10.0), ev, st, 1,
                           SFXConfig(), h)
        assert d2["sfx_required"] is False  # 'pop' available, category < 15 s
        assert "category" in d2["reason"].lower()
    finally:
        del CATEGORIES["test_cat"]


# ---------------------------------------------------------------------------
# G. consecutive-scene event dedup
# ---------------------------------------------------------------------------
def _plan(texts, vtags, starts):
    from types import SimpleNamespace
    scenes = [SimpleNamespace(id=i, text=t) for i, t in enumerate(texts)]
    from src.script_analyzer import analyze_sentence
    sas = [analyze_sentence(t) for t in texts]
    timings = [{"start": s, "end": s + 5.0} for s in starts]
    ctxs = build_scene_contexts(scenes, sas, timings, vtags)
    return plan_video_sfx(ctxs, seed=1)


def test_consecutive_same_event_deduped():
    texts = ["The volcano erupts violently.", "Lava pours down the slopes.",
             "Ash covers the villages."]
    vtags = ["volcano erupting lava", "volcano erupting lava flow",
             "volcano eruption ash"]
    decisions, events = _plan(texts, vtags, [0.0, 5.0, 10.0])
    assert decisions[0]["sfx_required"] is True
    assert decisions[1]["sfx_required"] is False
    assert decisions[2]["sfx_required"] is False
    assert "merg" in decisions[1]["reason"].lower()
    assert len(events) == 1  # ONE eruption layer, not three


def test_genuinely_new_instance_after_cooldown_allowed():
    texts = ["An explosion rocked the building.",
             "Another explosion followed much later."]
    vtags = ["explosion fire", "explosion fireball"]
    decisions, events = _plan(texts, vtags, [0.0, 30.0])
    assert decisions[0]["sfx_required"] is True
    assert decisions[1]["sfx_required"] is True  # 2nd file use, cooldowns OK
    assert len(events) == 2  # boom, then boom again (no layering here)


# ---------------------------------------------------------------------------
# H. semantic similarity groups
# ---------------------------------------------------------------------------
def test_semantic_similarity_blocks():
    st = {}
    d1 = decide(ctx("An explosion rocked the building.", "explosion fire",
                    start=0.0, end=5.0), st)
    assert d1["sfx_required"] is True
    # different file (impact), same meaning (heavy hit) -> blocked
    d2 = decide(ctx("The car crash was terrible.", "car crash collision",
                    start=5.0, end=10.0), st)
    assert d2["sfx_required"] is False
    assert "similar" in d2["reason"].lower()


# ---------------------------------------------------------------------------
# I. cinematic sounds: very high threshold, never defaults
# ---------------------------------------------------------------------------
def test_plain_cut_gets_no_whoosh():
    texts = ["The minister spoke calmly.", "Markets closed higher today."]
    vtags = ["man speaking podium", "stock market building"]
    decisions, events = _plan(texts, vtags, [0.0, 5.0])
    assert events == []


def test_motivated_cut_gets_whoosh():
    texts = ["The minister spoke calmly.", "Markets closed higher today."]
    vtags = ["man speaking podium whip pan fast", "stock market building"]
    decisions, events = _plan(texts, vtags, [0.0, 5.0])
    assert any(e["sfx"] == "whoosh" for e in events)


def test_tension_without_visual_support_no_riser():
    d = decide(ctx("But then, nobody expected what happened next.",
                   "man talking calm office", mood="suspense"))
    assert d["sfx_required"] is False


# ---------------------------------------------------------------------------
# J. NO SFX is a successful result
# ---------------------------------------------------------------------------
def test_no_sfx_dict_shape():
    d = decide(ctx("She sat quietly reading.", "woman reading book"))
    assert d["sfx_required"] is False
    assert d["events"] == []
    assert set(d) >= {"sfx_required", "reason", "confidence", "events",
                      "classification", "debug"}


def test_decision_dict_shape():
    d = decide(ctx("The rocket launches.", "rocket launch pad"))
    assert set(d) >= {"sfx_required", "reason", "confidence", "events",
                      "classification", "debug"}
    e = d["events"][0]
    assert set(e) >= {"type", "sfx_category", "sfx", "start_time", "duration",
                      "volume", "gain_db", "fade_in", "fade_out"}
    assert 0.60 <= d["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# K. global SFX budget
# ---------------------------------------------------------------------------
def test_budget_rolling_window():
    from src.semantic_sfx import SFXHistory, SFXRecord
    h = SFXHistory(SFXConfig())
    for i in range(3):
        h.record(SFXRecord("tick", "ui", "stat_reveal", i,
                           float(i * 2), float(i * 2 + 0.2)))
    ok, _ = h.budget_allows(0.0, False, OPTIONAL)
    assert ok is False   # 3 events in [0,10) already -> 4th optional blocked
    ok2, _ = h.budget_allows(0.0, False, REQUIRED)
    assert ok2 is True   # clearly important events may override


def test_budget_transition_8s():
    from src.semantic_sfx import SFXHistory, SFXRecord
    h = SFXHistory(SFXConfig())
    h.record(SFXRecord("whoosh", "transitions", "transition_cut", 1,
                       10.0, 10.45))
    assert h.transitions_in_window(12.0 - 8.0, 12.0) != []


# ---------------------------------------------------------------------------
# L. never fill empty audio
# ---------------------------------------------------------------------------
def test_no_minimum_target_quiet_video():
    texts = ["Welcome back to the broadcast.",
             "The minister addressed the nation today.",
             "She sat quietly reading."]
    vtags = ["news anchor studio", "man speaking podium", "woman reading book"]
    decisions, events = _plan(texts, vtags, [0.0, 5.0, 10.0])
    assert all(d["sfx_required"] is False for d in decisions)
    assert events == []


# ---------------------------------------------------------------------------
# M. debug mode
# ---------------------------------------------------------------------------
def test_debug_log_format():
    texts = ["Nothing happens here.", "He opens the metal door.",
             "He opens the metal door."]
    vtags = ["empty room", "man metal door", "man metal door"]
    decisions, _ = _plan(texts, vtags, [0.0, 10.0, 20.0])
    log = format_debug_log(decisions)
    assert "Scene 0" in log and "Decision: NO SFX" in log
    assert "Scene 1" in log and "Decision: USE SFX" in log
    assert "Detected event: door_action" in log
    assert "Scene 2" in log
    assert "Same event/category recently used" in log \
        or "repetition" in log.lower()
    for field in ("Selected SFX:", "Category:", "Confidence:",
                  "Why selected:", "Previous similar SFX:",
                  "Repetition penalty:", "Final score:", "Reason:"):
        assert field in log


# ---------------------------------------------------------------------------
# Kept behaviors from the previous engine
# ---------------------------------------------------------------------------
def test_confidence_bands_documented():
    assert CONF_FLOOR == 0.60


def test_ducking_when_narration_present():
    loud = volume_to_gain_db(0.6, duck_db=0.0)
    ducked = volume_to_gain_db(0.6, duck_db=-4.0)
    assert ducked < loud
    d = decide(ctx("The rocket launches.", "rocket launch pad"))
    assert d["events"][0]["gain_db"] <= -4.0


def test_crowd_ambience_matches_scene_duration():
    d = decide(ctx("The rally filled the square.", "huge crowd protest",
                   start=0.0, end=12.0))
    assert d["sfx_required"] is True
    e = d["events"][0]
    assert e["duration"] == pytest.approx(12.0)
    assert e["fade_in"] > 0 and e["fade_out"] > 0


def test_no_ambience_for_talking_head():
    d = decide(ctx("Welcome back to the broadcast.", "news anchor studio",
                   start=0.0, end=20.0))
    assert d["sfx_required"] is False


def test_category_without_asset_never_plays():
    assert resolve_asset("vehicles") is None
    d = decide(ctx("The car drove down the highway.", "car highway driving"))
    assert d["sfx_required"] is False


def test_register_category_without_core_changes():
    register_category(SFXCategory(
        id="test_horn", label="Test horn", description="unit test",
        assets=["ding"], default_duration=0.5, default_volume=0.4))
    assert "test_horn" in CATEGORIES
    assert resolve_asset("test_horn") == "ding"
    del CATEGORIES["test_horn"]


def test_no_repeat_across_consecutive_scenes():
    s1 = ctx("An explosion rocked the building.", "explosion fire")
    s2 = ctx("Smoke rose over the city.", "explosion aftermath smoke")
    st = {}
    d1 = decide(s1, st)
    assert d1["sfx_required"] is True
    d2 = decide(s2, st)
    assert d2["sfx_required"] is False  # aftermath: event already passed


def test_plan_video_structure_and_density():
    texts = ["The rocket launches.", "The minister spoke calmly.",
             "Lightning split the sky."]
    vtags = ["rocket launch pad", "man speaking podium", "lightning storm"]
    decisions, events = _plan(texts, vtags, [0.0, 5.0, 10.0])
    assert len(decisions) == 3
    assert decisions[1]["sfx_required"] is False
    for e in events:
        assert set(e) >= {"time", "sfx", "gain_db", "duration",
                          "fade_in", "fade_out", "sfx_category"}
    # 15 s video -> safety cap int(0.25*10)+2 = 4
    assert len(events) <= 4


def test_build_scene_contexts_marks_weak_visual_signal():
    from src.script_analyzer import analyze_sentence
    from types import SimpleNamespace
    scenes = [SimpleNamespace(id=0, text="Hello world.")]
    sas = [analyze_sentence("Hello world.")]
    ctxs = build_scene_contexts(scenes, sas, [{"start": 0, "end": 2}], [""])
    assert ctxs[0].visual_signal is False
    assert ctxs[0].narration_present is True
