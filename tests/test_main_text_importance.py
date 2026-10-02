"""Main-text importance-selection tests (2026-10-02 rebuild).

The AI must pick ONLY genuinely important words/phrases from the
narration script — never random words or filler lines. Priority:
named entities > numbers-with-magnitude > outcome/decision words >
attributed quotes > dates. Candidates below _IMPORTANCE_FLOOR emit
NOTHING rather than a random-looking overlay.
"""
from src.text_captions import (  # noqa: E402
    _IMPORTANCE_FLOOR,
    _MT_CYAN,
    _MT_RED,
    _MT_WHITE,
    MAIN_TEXT_COLORS,
    MAIN_TEXT_POSITION,
    MAIN_TEXT_SAFE_ZONE,
    main_text_color_for,
    main_text_position_for,
    select_main_text,
)


def test_entities_beat_bare_dates():
    cards = select_main_text("NASA revealed the plan. In 2024, funding "
                             "was approved.")
    assert cards, "expected at least one card"
    # the entity (NASA) must beat the bare year "2024"
    assert "NASA" in cards[0]["text"]


def test_numbers_with_magnitude_win():
    cards = select_main_text("The deal was worth $50 million. He smiled.")
    assert cards, "expected at least one card"
    assert cards[0]["type"] == "STAT"
    assert "50" in cards[0]["text"]


def test_weak_scene_emits_nothing():
    # no named entity, no number, no quote, no outcome -> floor
    assert select_main_text("The cat sat quietly on the mat.") == []


def test_quote_said_fallback():
    cards = select_main_text("The minister announced that the borders "
                             "would reopen by Friday.")
    assert cards, "expected at least one card"
    assert cards[0]["type"] == "QUOTE"
    assert "borders" in cards[0]["text"].lower()


def test_chapter_label_comes_from_after_marker():
    cards = select_main_text("Chapter two: strengthen the border controls "
                             "now.")
    chap = [c for c in cards if c["type"] == "CHAPTER"]
    assert chap, "expected a CHAPTER card"
    assert "STRENGTHEN" in chap[0]["text"]


def test_multi_word_name_beats_single_token_name():
    cards = select_main_text("Apple fell. Sarah Mitchell resigned.")
    names = [c for c in cards if c["type"] == "NAME"]
    assert names, "expected NAME cards"
    assert names[0]["text"] == "Sarah Mitchell"


def test_filler_capitalised_words_not_entities():
    # "Breaking" / "Live" are news labels, not names.
    cards = select_main_text("Breaking: the rescue effort continues.")
    names = [c for c in cards if c["type"] == "NAME"]
    assert all("Breaking" not in n["text"] for n in names)


def test_importance_floor_value():
    assert _IMPORTANCE_FLOOR == 45.0


def test_crisis_red_only_for_crisis_types():
    accent, _ = main_text_color_for("STAT", "the crash wiped out $50M",
                                   "")
    assert accent == _MT_RED
    # NAME cards stay white even when the scene is about a crisis
    accent, _ = main_text_color_for("NAME", "Sarah Mitchell",
                                   "the deadly attack shocked everyone")
    assert accent == _MT_WHITE
    accent, _ = main_text_color_for("NAME", "Sarah Mitchell", "quiet day")
    assert accent == _MT_WHITE


def test_keyword_technical_card_cyan():
    accent, heroes = main_text_color_for("KEYWORD", "Quantum Tunneling",
                                        "")
    assert accent == _MT_CYAN
    assert heroes


def test_explicit_color_map_covers_all_types():
    for t in ("STAT", "NAME", "DATE", "KEYWORD", "QUOTE", "CHAPTER",
              "OUTCOME"):
        assert t in MAIN_TEXT_COLORS, t
        spec = MAIN_TEXT_COLORS[t]
        assert {"fill", "accent_alt", "crisis", "rule"} <= set(spec)


def test_position_zones_explicit():
    assert main_text_position_for("QUOTE")["zone"] == "center"
    assert main_text_position_for("STAT")["zone"] == "upper-center"
    assert main_text_position_for("DATE")["zone"] == "top-right"


def test_safe_zone_bottom_reserved():
    assert MAIN_TEXT_SAFE_ZONE["bottom_reserved_px"] == 250
    assert MAIN_TEXT_SAFE_ZONE["y_max"] == 972


def test_hold_time_formula_kept():
    cards = select_main_text("Sarah Mitchell announced a $50 million "
                             "rescue package for the flood victims.")
    assert cards
    for c in cards:
        assert 1.5 <= c["hold"] <= 4.0


def test_density_kept():
    # 6s scene -> 1 card, 16s -> 2, 31s -> 3
    cards = select_main_text("Sarah Mitchell announced the rescue plan.",
                             duration=6.0)
    assert len(cards) <= 1
    cards = select_main_text("Sarah Mitchell announced the $50 million "
                             "rescue plan after the floods hit the city.",
                             duration=31.0)
    assert len(cards) <= 3
