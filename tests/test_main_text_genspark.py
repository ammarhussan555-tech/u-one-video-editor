"""Genspark-validated main-text engine tests.

Spec: ~/workspace/research/main_text_genspark_spec.md
(a) filler-only sentence emits nothing (spec regression)
(b) "$68 billion ... collapsed" -> ONLY "COLLAPSED" in red
(c) a 6s scene with two STATs emits exactly one overlay
(d) every entrance <= 250ms and every exit strictly shorter than its entrance
(e) hold within [1.5, 4.0]s
(f) one classification example for each of the 7 types
(g) date-deadline override beats a name
"""
from src.text_captions import (
    MAIN_TEXT_STYLE, MAIN_TEXT_TYPES,
    classify_main_text, select_main_text,
)


def test_a_filler_only_sentence_emits_nothing():
    cards = select_main_text(
        "Instead of buying expensive supplements, you could try this "
        "simple alternative.",
        duration=6.0, keywords=("supplements", "alternative"))
    assert cards == []


def test_b_only_collapsed_is_red():
    cards = select_main_text(
        "The company's $68 billion empire collapsed in minutes.",
        duration=6.0)
    assert len(cards) == 1
    card = cards[0]
    assert card["type"] == "STAT"
    assert card["accent"] == "&H000000FF"  # #FF0000
    assert card["accent_words"] == ["COLLAPSED"]
    assert "COLLAPSED" in card["text"]


def test_c_six_second_scene_with_two_stats_emits_one_overlay():
    cards = select_main_text(
        "Oil prices jumped 15 percent as the market lost $2 trillion "
        "in value.",
        duration=6.0)
    assert len(cards) == 1
    assert cards[0]["type"] == "STAT"
    assert "$2" in cards[0]["text"]  # magnitude tie-break: $2T wins


def test_d_entrance_timing_hard_rules():
    for tname, ts in MAIN_TEXT_STYLE.items():
        assert ts["enter_ms"] <= 250, tname
        assert ts["exit_ms"] < ts["enter_ms"], tname


def test_e_hold_within_bounds():
    scenes = [
        "The company's $68 billion empire collapsed in minutes.",
        "President Sarah Mitchell announced the plan.",
        "The summit is on March 10.",
        'She said, "We will rebuild stronger," to the cheering crowd.',
        "Number three: strengthen the border.",
        "This could trigger a global recession.",
        "Oil prices jumped 15 percent as the market lost $2 trillion in value.",
    ]
    seen = 0
    for s in scenes:
        for card in select_main_text(s, duration=6.0):
            seen += 1
            assert 1.5 <= card["hold"] <= 4.0, (s, card["hold"])
    assert seen > 0


def test_f_all_seven_types_classified():
    cases = [
        ("The budget reached $68 billion this year.", "STAT"),
        ("President Sarah Mitchell announced the plan.", "NAME"),
        ("The summit is on March 10.", "DATE"),
        ("The new NASA telescope launched successfully.", "KEYWORD"),
        ('She said, "We will rebuild stronger," to the crowd.', "QUOTE"),
        ("Number three: strengthen the border.", "CHAPTER"),
        ("This could trigger a global recession.", "OUTCOME"),
    ]
    for text, want in cases:
        got, overlay = classify_main_text(text)
        assert got == want, (want, got, overlay, text)
    assert set(MAIN_TEXT_TYPES) == {
        "STAT", "NAME", "DATE", "KEYWORD", "QUOTE", "CHAPTER", "OUTCOME"}


def test_g_date_deadline_override_beats_name():
    got, overlay = classify_main_text(
        "Sarah Mitchell announced the policy. The deadline is December 25.")
    assert got == "DATE", (got, overlay)


def test_hero_word_lead_with_timings():
    words = "The company's $68 billion empire collapsed".split()
    wt = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
          for i, w in enumerate(words)]
    cards = select_main_text(
        "The company's $68 billion empire collapsed.", duration=6.0,
        word_timings=wt)
    assert cards
    # "$68" is spoken at 0.8s; appear 300ms before -> ~0.5s
    assert cards[0]["appear"] < 1.2


def test_white_default_accent_for_neutral_name():
    cards = select_main_text("President Sarah Mitchell announced the plan.",
                             duration=6.0)
    assert cards
    assert cards[0]["accent"] == "&H00FFFFFF"
    assert cards[0]["accent_words"] == []
