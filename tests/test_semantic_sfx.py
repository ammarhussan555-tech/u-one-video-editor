"""Universal Semantic SFX Engine tests.

Covers every example in the user's spec:
  talking -> NO SFX | door+open -> doors | rocket+launch -> space/rumble |
  map+zoom -> zoom whoosh | glass shatter -> glass_break |
  lightning -> thunder | photo alone -> NO SFX | number+emphasis -> subtle
  click (only with visual support) | confidence floor | no-repeat rule |
  narration ducking | ambience rules | register_category extensibility.
"""
import pytest

from src.semantic_sfx import (
    CATEGORIES, CONF_FLOOR, NO_SFX_DECISION, SFXCategory,
    SceneContext, build_scene_contexts, decide_scene, plan_video_sfx,
    register_category, resolve_asset, volume_to_gain_db,
)


def ctx(narration="", visual="", start=0.0, end=5.0, mood="neutral",
        numbers=(), actions_extra=()):
    c = SceneContext(narration=narration, visual_text=visual,
                     start=start, end=end, mood=mood,
                     narration_present=bool(narration.strip()))
    c.numbers = list(numbers)
    c.duration = end - start
    c.visual_signal = bool(visual.strip())
    from src.semantic_sfx import extract_actions
    c.actions = extract_actions(narration, visual) + list(actions_extra)
    return c


def decide(c, state=None):
    return decide_scene(c, state if state is not None else {})


# --- user's examples -------------------------------------------------------
def test_talking_head_no_sfx():
    d = decide(ctx("The minister addressed the nation today.",
                   "man speaking podium press conference"))
    assert d["sfx_required"] is False
    assert "talking" in d["reason"].lower() or "no meaningful" in d["reason"].lower()


def test_door_open_door_sfx():
    d = decide(ctx("He opens the heavy metal door.",
                   "man metal door"))
    assert d["sfx_required"] is True
    assert d["events"][0]["sfx_category"] == "doors"
    assert d["events"][0]["sfx"] == "door"


def test_rocket_launch_rumble():
    d = decide(ctx("The rocket launches into the sky.",
                   "rocket launch pad night"))
    assert d["sfx_required"] is True
    cats = [e["sfx_category"] for e in d["events"]]
    assert "space" in cats  # rumble, layered with explosion boom


def test_map_zoom_subtle_whoosh():
    d = decide(ctx("Here is the region on the map.", "map zoom animation"))
    assert d["sfx_required"] is True
    e = d["events"][0]
    assert e["sfx_category"] == "zoom"
    assert e["sfx"] == "swoosh"
    assert e["volume"] <= 0.5  # subtle


def test_glass_break():
    d = decide(ctx("The window shattered into pieces.",
                   "broken glass window"))
    assert d["sfx_required"] is True
    assert d["events"][0]["sfx_category"] == "glass"
    assert d["events"][0]["sfx"] == "glass_break"


def test_lightning_thunder():
    d = decide(ctx("Lightning split the night sky.",
                   "lightning storm clouds"))
    assert d["sfx_required"] is True
    assert d["events"][0]["sfx_category"] == "weather"


def test_photo_alone_no_sfx():
    d = decide(ctx("This photograph was taken in 1948.",
                   "historical black and white photo"))
    assert d["sfx_required"] is False


def test_number_with_emphasis_and_graphic_subtle_click():
    d = decide(ctx("The deficit revealed today reached 4.2 billion dollars.",
                   "statistic text graphic chart", numbers=["4.2"]))
    assert d["sfx_required"] is True
    e = d["events"][0]
    assert e["volume"] <= 0.5  # subtle only


def test_number_without_emphasis_no_sfx():
    d = decide(ctx("He bought 3 apples yesterday.", "man walking street",
                   numbers=["3"]))
    assert d["sfx_required"] is False


# --- confidence floor -------------------------------------------------------
def test_narration_only_explosion_below_floor():
    # "explosion" merely mentioned, nothing on screen -> < 0.60 -> NO SFX
    d = decide(ctx("The explosion was heard miles away.", "city street day"))
    assert d["sfx_required"] is False


def test_confidence_bands_documented():
    assert CONF_FLOOR == 0.60


# --- no-repeat rule ----------------------------------------------------------
def test_no_repeat_across_consecutive_scenes():
    s1 = ctx("An explosion rocked the building.", "explosion fire")
    s2 = ctx("Smoke rose over the city.", "explosion aftermath smoke")
    st = {}
    d1 = decide(s1, st)
    assert d1["sfx_required"] is True
    d2 = decide(s2, st)
    assert d2["sfx_required"] is False  # same category, action not repeated


def test_repeat_allowed_when_action_repeats():
    s1 = ctx("An explosion rocked the building.", "explosion fire")
    s2 = ctx("Another explosion followed seconds later.",
             "explosion fireball")
    st = {}
    decide(s1, st)
    d2 = decide(s2, st)
    assert d2["sfx_required"] is True


# --- ducking under narration ---------------------------------------------------
def test_ducking_when_narration_present():
    loud = volume_to_gain_db(0.6, duck_db=0.0)
    ducked = volume_to_gain_db(0.6, duck_db=-4.0)
    assert ducked < loud
    c = ctx("The rocket launches.", "rocket launch pad", )
    d = decide(c)
    assert d["events"][0]["gain_db"] <= -4.0  # ducked, never overpowering


# --- ambience rules --------------------------------------------------------------
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


# --- never a wrong sound ----------------------------------------------------------
def test_category_without_asset_never_plays():
    # vehicles has no suitable asset -> must not fire
    assert resolve_asset("vehicles") is None
    d = decide(ctx("The car drove down the highway.", "car highway driving"))
    assert d["sfx_required"] is False


# --- extensibility --------------------------------------------------------------------
def test_register_category_without_core_changes():
    register_category(SFXCategory(
        id="test_horn", label="Test horn", description="unit test",
        assets=["ding"], default_duration=0.5, default_volume=0.4))
    assert "test_horn" in CATEGORIES
    assert resolve_asset("test_horn") == "ding"
    del CATEGORIES["test_horn"]


# --- decision structure -----------------------------------------------------------------
def test_decision_dict_shape():
    d = decide(ctx("The rocket launches.", "rocket launch pad"))
    assert set(d) >= {"sfx_required", "reason", "confidence", "events"}
    e = d["events"][0]
    assert set(e) >= {"type", "sfx_category", "start_time", "duration",
                      "volume", "fade_in", "fade_out"}
    assert 0.60 <= d["confidence"] <= 1.0


def test_no_sfx_dict_shape():
    d = decide(ctx("She sat quietly reading.", "woman reading book"))
    assert d["sfx_required"] is False
    assert d["events"] == []
    assert "reason" in d


# --- video-level planning ------------------------------------------------------------------
def _fake_scene(text):
    from types import SimpleNamespace
    return SimpleNamespace(id=0, text=text)


def test_plan_video_transitions_and_density():
    from src.script_analyzer import analyze_sentence
    texts = ["The rocket launches.", "The minister spoke calmly.",
             "Lightning split the sky."]
    vtags = ["rocket launch pad", "man speaking podium", "lightning storm"]
    scenes = [_fake_scene(t) for t in texts]
    sas = [analyze_sentence(t) for t in texts]
    timings = [{"start": i * 5.0, "end": i * 5.0 + 5.0} for i in range(3)]
    ctxs = build_scene_contexts(scenes, sas, timings, vtags)
    decisions, events = plan_video_sfx(ctxs, seed=1)
    assert len(decisions) == 3
    # talking-head scene stays silent (no forced SFX)
    assert decisions[1]["sfx_required"] is False
    # events carry pipeline fields
    for e in events:
        assert set(e) >= {"time", "sfx", "gain_db", "duration",
                          "fade_in", "fade_out"}
    # density cap respected
    assert len(events) <= int(0.25 * 10) + 2 + 3


def test_build_scene_contexts_marks_weak_visual_signal():
    from src.script_analyzer import analyze_sentence
    scenes = [_fake_scene("Hello world.")]
    sas = [analyze_sentence("Hello world.")]
    ctxs = build_scene_contexts(scenes, sas,
                                [{"start": 0, "end": 2}], [""])
    assert ctxs[0].visual_signal is False
    assert ctxs[0].narration_present is True
