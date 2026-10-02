"""120-preset caption library tests (2026-10-02 rebuild).

Covers the user's spec:
  * 24 categories x 5 presets = 120 presets, ids like dynamic_01
  * every preset generates valid ASS
  * active-word-only animation: previous words automatically revert
    (asserted by inspecting ASS override groups per layer-1 event)
  * preset-field purity: the renderer emits nothing not in the preset
    (no invented emoji / motion / glow / colors)
  * spoken-word timing drives the active word (word-level timestamps)
  * migration map for every retired id
  * gallery defaults (Qt-guarded: runs in Windows CI)
"""
import os
import re

import pytest

from src.text_captions import (
    CAPTION_TEMPLATES,
    CAPTION_TEMPLATE_CATEGORIES,
    DEFAULT_CAPTION_TEMPLATE,
    _CATEGORY_DEFS,
    build_ass,
    caption_category_labels,
    caption_template_labels,
    migrate_caption_template_id,
)

WORDS = [
    {"word": w, "start": 0.5 + i * 0.4, "end": 0.5 + i * 0.4 + 0.35}
    for i, w in enumerate("Yellowstone is hiding something".split())
]
SENTS = [{"text": "Yellowstone is hiding something", "start": 0.5, "end": 2.9}]
N_WORDS = len(WORDS)


def _ass_text(tid, tmp_path, **kw):
    p = str(tmp_path / ("cap_%s.ass" % tid.replace("/", "_")))
    build_ass(SENTS, WORDS, p, font_size=48, highlight=True,
              template=tid, **kw)
    return open(p, encoding="utf-8").read()


def _layer1_events(ass_text):
    """[(start, end, body)] for Dialogue layer 1 events."""
    out = []
    for line in ass_text.splitlines():
        m = re.match(r"Dialogue: 1,(\d+:\d\d:\d\d\.\d\d),"
                     r"(\d+:\d\d:\d\d\.\d\d),Cap,,0,0,0,,(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def _ts(sec):
    h, rem = int(sec // 3600), sec % 3600
    m, rem = int(rem // 60), rem % 60
    return "%d:%02d:%02d.%02d" % (h, m, int(rem), int(rem % 1 * 100))


def _word_groups(body):
    """[(tags, text)] override-group pairs in an event body."""
    return re.findall(r"\{([^{}]*)\}([^{}]*)", body)


# ---------------------------------------------------------------- library
def test_preset_count():
    assert len(CAPTION_TEMPLATES) == 120


def test_category_count_and_five_each():
    assert len(CAPTION_TEMPLATE_CATEGORIES) == 24
    assert len(_CATEGORY_DEFS) == 24
    for slug, label, _concept, _base, variants in _CATEGORY_DEFS:
        assert len(variants) == 5, slug
        for i in range(1, 6):
            pid = "%s_%02d" % (slug, i)
            assert pid in CAPTION_TEMPLATES, pid
            assert CAPTION_TEMPLATES[pid]["category"] == label


def test_preset_ids_unique_and_labeled():
    ids = list(CAPTION_TEMPLATES)
    assert len(set(ids)) == 120
    for pid, p in CAPTION_TEMPLATES.items():
        assert p["id"] == pid
        assert p["label"]
        assert p["meta"].get("source") == "visual_recreation"
        assert p.get("preset_schema") == 2


def test_template_labels_cover_all():
    labels = caption_template_labels()
    assert len(labels) == 120
    assert dict(labels)[DEFAULT_CAPTION_TEMPLATE]


def test_category_labels_counts():
    cats = caption_category_labels()
    assert len(cats) == 24
    assert all(c == 5 for _l, c in cats)
    assert sum(c for _l, c in cats) == 120


def test_default_preset_valid():
    assert DEFAULT_CAPTION_TEMPLATE in CAPTION_TEMPLATES
    assert DEFAULT_CAPTION_TEMPLATE == "dynamic_01"


# ---------------------------------------------------------------- ASS build
def test_all_presets_build_valid_ass(tmp_path):
    for pid in CAPTION_TEMPLATES:
        txt = _ass_text(pid, tmp_path)
        assert "[Script Info]" in txt
        assert "Style: Cap," in txt
        evs = _layer1_events(txt)
        assert evs, pid  # every preset must emit active-word events


def test_typewriter_emits_char_events(tmp_path):
    txt = _ass_text("typewriter_01", tmp_path)
    evs = _layer1_events(txt)
    # 4 words -> many char events (more than one per word)
    assert len(evs) > N_WORDS * 2


# ------------------------------------------------- active-word-only behavior
def _active_word(body):
    """The single visible word of a layer-1 overlay event."""
    groups = [(t, x.strip()) for t, x in _word_groups(body) if x.strip()]
    visible = [x for t, x in groups if "\\alpha&HFF&" not in t
               and "\\1a&HFF&" not in t]
    return visible


def test_only_spoken_word_visible_per_event(tmp_path):
    """Each layer-1 event shows exactly ONE word; the rest are fully
    transparent layout placeholders. Previous words revert automatically
    because every event is independent. (Multi-phase animations emit
    several events per word — each still shows just the one word.)"""
    txt = _ass_text("dynamic_01", tmp_path)
    evs = _layer1_events(txt)
    assert len(evs) >= N_WORDS
    for _s, _e, body in evs:
        groups = [(t, x.strip()) for t, x in _word_groups(body) if x.strip()]
        visible = _active_word(body)
        hidden = [x for t, x in groups if "\\alpha&HFF&" in t]
        assert len(visible) == 1, body[:120]
        assert len(hidden) == N_WORDS - 1, body[:120]


def test_previous_word_reverts_to_normal(tmp_path):
    """Word N's event styles word N; word N+1's event styles word N+1 and
    leaves word N transparent (normal base shows through)."""
    txt = _ass_text("dynamic_01", tmp_path)
    evs = _layer1_events(txt)
    seen_active = []
    for _s, _e, body in evs:
        active = _active_word(body)
        assert len(active) == 1
        if not seen_active or seen_active[-1] != active[0].upper():
            seen_active.append(active[0].upper())
    assert seen_active == [w["word"].upper() for w in WORDS]


def test_active_word_gets_highlight_color(tmp_path):
    txt = _ass_text("dynamic_01", tmp_path)
    evs = _layer1_events(txt)
    # dynamic_01: highlight=color, active yellow &H0000E6FF
    for _s, _e, body in evs:
        assert "\\c&H0000E6FF" in body


def test_dim_preset_shows_upcoming_grey(tmp_path):
    txt = _ass_text("karaoke_03", tmp_path)  # highlight=dim
    assert "\\c&H00A0A0A0" in txt  # dim-upcoming grey present


# ------------------------------------------------- libass safety invariants
def test_no_double_transform_per_block(tmp_path):
    """libass (U One's burn-in renderer) silently drops an ENTIRE event
    when two \\t transforms share one override block. No event from any
    of the 120 presets may do that."""
    for pid in CAPTION_TEMPLATES:
        txt = _ass_text(pid, tmp_path)
        for line in txt.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            for block in re.findall(r"\{([^{}]*)\}", line):
                assert block.count("\\t(") <= 1, (pid, block[:90])


def test_no_alpha_poisoning(tmp_path):
    """ASS tags are stateful: {\\alpha&HFF&} (transparent placeholder)
    would keep every later word invisible, because \\c changes color
    but NOT alpha. Any group showing text after a transparent group
    must reset alpha explicitly."""
    for pid in CAPTION_TEMPLATES:
        txt = _ass_text(pid, tmp_path)
        for _s, _e, body in _layer1_events(txt):
            poisoned = False
            for tags, text in _word_groups(body):
                if "\\alpha&HFF&" in tags or "\\1a&HFF&" in tags:
                    poisoned = True
                if text.strip() and poisoned:
                    assert ("\\alpha" in tags or "\\1a" in tags), (
                        pid, tags[:80], text.strip()[:20])


# ------------------------------------------------------- spoken-word timing
def test_word_timing_drives_active_word(tmp_path):
    """Event windows follow the word-level audio timestamps (+ lead).
    Multi-phase animations emit several events per word; the FIRST
    event for each word (animation phase 0) starts at the word's
    spoken time."""
    txt = _ass_text("dynamic_01", tmp_path)  # lead_ms = -20
    evs = _layer1_events(txt)
    first_starts = []
    seen = set()
    for s, _e, body in evs:
        active = _active_word(body)
        assert len(active) == 1
        key = active[0].upper()
        if key not in seen:
            seen.add(key)
            first_starts.append((key, s))
    assert [k for k, _s in first_starts] == [w["word"].upper()
                                            for w in WORDS]
    for i, (key, s) in enumerate(first_starts):
        expect = _ts(max(0.0, WORDS[i]["start"] - 0.02))
        assert s == expect, (key, s, expect)


def test_no_overlapping_active_windows(tmp_path):
    """Active windows are sequential: word i+1 starts at/after word i."""
    txt = _ass_text("dynamic_01", tmp_path)
    evs = _layer1_events(txt)

    def _sec(ts):
        h, m, rest = ts.split(":")
        s, cs = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0

    starts = [_sec(s) for s, _e, _b in evs]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


# ------------------------------------------------------- preset-field purity
def test_purity_no_invented_extras(tmp_path):
    """minimal_04: highlight=none, anim=fade, entry/exit=fade,
    background=none, emoji="". The ASS must contain no motion, no glow,
    no box drawing, no emoji — nothing not in the preset."""
    txt = _ass_text("minimal_04", tmp_path)
    assert "\\blur" not in txt
    assert "\\frz" not in txt
    assert "\\move(" not in txt
    assert "\\u1" not in txt
    for em in ("\U0001F525", "\u2728", "\u26A1", "\U0001F4A1",
               "\U0001F3AF"):
        assert em not in txt


def test_purity_motion_only_when_preset_says(tmp_path):
    txt_jitter = _ass_text("gaming_01", tmp_path)   # jitter
    assert "\\move(" in txt_jitter
    txt_neon = _ass_text("neon_01", tmp_path)       # glowpulse + glow
    assert "\\blur" in txt_neon
    txt_plain = _ass_text("karaoke_01", tmp_path)   # none anim
    assert "\\move(" not in txt_plain
    assert "\\blur" not in txt_plain


def test_purity_emoji_only_in_emoji_presets(tmp_path):
    txt = _ass_text("emoji_01", tmp_path)
    assert "\U0001F525" in txt
    txt2 = _ass_text("dynamic_01", tmp_path)
    assert "\U0001F525" not in txt2


def test_purity_underline_only_when_specified(tmp_path):
    txt = _ass_text("karaoke_05", tmp_path)  # highlight=underline
    assert "\\u1" in txt
    txt2 = _ass_text("dynamic_01", tmp_path)
    assert "\\u1" not in txt2


# ---------------------------------------------------------------- migration
def test_migration_targets_valid():
    from src.text_captions import _MIGRATION_MAP, _LEGACY_STYLE_IDS
    for old, new in _MIGRATION_MAP.items():
        assert new in CAPTION_TEMPLATES, (old, new)
    for old in _LEGACY_STYLE_IDS:
        assert migrate_caption_template_id(old) == DEFAULT_CAPTION_TEMPLATE
    # every current preset id survives migration untouched
    for pid in CAPTION_TEMPLATES:
        assert migrate_caption_template_id(pid) == pid


def test_migration_unknown_and_garbage():
    assert migrate_caption_template_id("no_such_style") == \
        DEFAULT_CAPTION_TEMPLATE
    assert migrate_caption_template_id(None) == DEFAULT_CAPTION_TEMPLATE
    assert migrate_caption_template_id("") == DEFAULT_CAPTION_TEMPLATE


def test_migration_spot_checks():
    assert migrate_caption_template_id("submagic_hormozi") == "hormozi_01"
    assert migrate_caption_template_id("karaoke_pop") == "dynamic_01"
    assert migrate_caption_template_id("typewriter") == "typewriter_01"
    assert migrate_caption_template_id("opus_mozi") == "advanced_03"


# ---------------------------------------------------------------- gallery
def test_gallery_default_selection():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.ui.caption_gallery import CaptionGalleryDialog
    app = QApplication.instance() or QApplication([])
    dlg = CaptionGalleryDialog("", None)
    assert dlg.selected_key == DEFAULT_CAPTION_TEMPLATE
    # click-to-apply updates the selection immediately
    dlg._on_card_clicked("neon_03")
    assert dlg.selected_key == "neon_03"
    assert dlg._cards["neon_03"].applied.isVisible()
