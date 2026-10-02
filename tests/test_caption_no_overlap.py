"""Regression tests for Uzair's 2026-10-02 ~21:59 PKT report:
"pale text abi ha aur uepr sa agala text a jata ha" (the previous text is
still there and the next text comes on top of it) -- see the two photos:
"ABOVEONE OF THE" / "ISVOLCANOFITISVAY".

Root cause: in _render_preset_captions() the Layer-0 phrase card ran
`card_s = first.start + lead` (-20ms) .. `card_e = last.end + hold`
(+200ms). With (estimated) contiguous word timings, adjacent chunks
overlapped by ~220ms, so the old phrase stayed on screen while the new
phrase appeared -- the jumbled look in the photos. Layer-1 word
highlights also overlapped by the 20ms lead (two yellow words at once).

Fix: each card ends exactly when the next card starts (the 200ms hold is
kept for genuine pauses); each word highlight ends exactly when the next
word's highlight starts (clean handoff, incl. across chunk boundaries).
The typewriter renderer gets the same treatment for its hold + tail.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.text_captions import (  # noqa: E402
    _render_preset_captions,
    _render_typewriter_preset,
    chunk_words,
    _norm_cap_words,
    _preset_anchor,
    _clamp_burn_xy,
    CAPTION_TEMPLATES,
)
from src.voice import estimate_word_timings  # noqa: E402


def _layers(lines):
    def ts(s):
        h, m, sec = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)

    cards, overs = [], []
    for ln in lines:
        parts = ln.split(",")
        layer = int(parts[0].split(":")[1].strip())
        (cards if layer == 0 else overs).append(
            (ts(parts[1]), ts(parts[2])))
    return cards, overs


def _contiguous_words(texts, start=10.0, dur=0.35):
    sents, words, t = [], [], start
    for tx in texts:
        sents.append({"text": tx, "start": t, "end": t + dur})
        words.append({"word": tx, "start": t, "end": t + dur})
        t += dur
    return sents, words


def test_no_card_overlap_contiguous():
    sents, words = _contiguous_words(
        ["ABOVE", "ONE", "OF", "THE", "IS", "VOLCANO", "FIT", "IS", "WAY"])
    p = dict(CAPTION_TEMPLATES["bold_01"])
    lines = _render_preset_captions(p, sents, words, 1920, 1080, 1.0, 0, 0)
    cards, _ = _layers(lines)
    assert len(cards) >= 2
    for (s1, e1), (s2, e2) in zip(cards, cards[1:]):
        assert e1 <= s2 + 1e-6, f"card overlap: {e1:.3f} > {s2:.3f}"
        assert e1 > s1  # card still has positive duration


def test_no_word_highlight_overlap():
    sents, words = _contiguous_words(
        ["ABOVE", "ONE", "OF", "THE", "IS", "VOLCANO"])
    p = dict(CAPTION_TEMPLATES["bold_01"])
    lines = _render_preset_captions(p, sents, words, 1920, 1080, 1.0, 0, 0)
    _, overs = _layers(lines)
    assert overs
    for (s1, e1), (s2, e2) in zip(overs, overs[1:]):
        assert e1 <= s2 + 1e-6, f"highlight overlap: {e1:.3f} > {s2:.3f}"


def test_hold_kept_on_genuine_pause():
    # A real 1s silence between phrases keeps the 200ms hold (no trim).
    sents = [{"text": "ABOVE ONE", "start": 10.0, "end": 10.7},
             {"text": "OF THE", "start": 11.7, "end": 12.4}]
    words = estimate_word_timings(sents)
    p = dict(CAPTION_TEMPLATES["bold_01"])
    lines = _render_preset_captions(p, sents, words, 1920, 1080, 1.0, 0, 0)
    cards, _ = _layers(lines)
    # bold_01 uses the pop (metric) animation -> the base layer is now
    # per-word mirrored events, not one card per chunk. The LAST base
    # event of chunk 0 must still reach ~10.9 (word end 10.7 + 200ms
    # hold; ASS timestamps are centisecond-truncated, hence tolerance).
    last0 = max(e for s, e in cards if s < 11.0)
    assert abs(last0 - 10.9) < 0.02, (last0, cards)
    for (s1, e1), (s2, e2) in zip(cards, cards[1:]):
        assert e1 <= s2 + 1e-6, f"base overlap: {e1:.3f} > {s2:.3f}"


def _metric_norm(body):
    import re
    body = re.sub(r"\\[13]?c&H[0-9A-Fa-f]+", "", body)
    body = re.sub(r"\\alpha&H[0-9A-Fa-f]+&", "", body)
    body = re.sub(r"\\[13]a&H[0-9A-Fa-f]+&", "", body)
    # \\bord / \\blur never change glyph advances (burn-verified: a
    # \\bord7 box centers pixel-exact at the same x as the plain line,
    # and the glow-pulse preset renders highlight-over-base perfectly),
    # so they are layout-neutral for the metric-identity check. What
    # MUST match: word order, \\fs, and the scale/move tags (\\fscx,
    # \\fscy, \\t, \\move) that actually move the \\an5 center.
    body = re.sub(r"\\bord\d+", "", body)
    body = re.sub(r"\\blur\d+", "", body)
    return body


def _events_by_layer(lines):
    """(start, end, text-body) per layer; body = after the first {...}."""
    out = {0: [], 1: []}
    for ln in lines:
        parts = ln.split(",")
        layer = int(parts[0].split(":")[1].strip())

        def ts(s):
            h, m, sec = s.split(":")
            return int(h) * 3600 + int(m) * 60 + float(sec)

        txt = ln.split(",,0,0,0,,", 1)[1]
        body = txt.split("}", 1)[1]
        out[layer].append((ts(parts[1]), ts(parts[2]), body))
    return out


def test_metric_identity_pop():
    # Uzair's 2026-10-02 photos, second root cause: the overlay's scaled
    # (pop) word changed the line width, so \\an5 re-centered the overlay
    # and the yellow word collided with its white base word ("ABOVEONE",
    # ghost "S"). For metric anims the Layer-0 base must be
    # metric-identical to the Layer-1 overlay at every instant: same
    # words, same scale tags -- modulo colors/alphas.
    sents, words = _contiguous_words(
        ["ABOVE", "ONE", "OF", "THE", "IS", "VOLCANO"])
    p = dict(CAPTION_TEMPLATES["bold_01"])
    assert p["active_anim"] == "pop"
    lines = _render_preset_captions(p, sents, words, 1920, 1080, 1.0, 0, 0)
    ev = _events_by_layer(lines)
    assert ev[0] and ev[1]
    base_by_start = {}
    for s, e, body in ev[0]:
        base_by_start.setdefault(round(s, 3), []).append((e, body))
    for s, e, obody in ev[1]:
        cands = base_by_start.get(round(s, 3), [])
        assert cands, f"no mirrored base for overlay at {s:.3f}"
        # the base may extend further (200ms hold / gap bridge); the
        # overlay start must match a base start.
        bbody = cands[0][1]
        assert _metric_norm(obody) == _metric_norm(bbody), (
            f"metric mismatch at {s:.3f}:\n{_metric_norm(obody)}\n"
            f"!=\n{_metric_norm(bbody)}")


def test_typewriter_no_overlap():
    sents, words = _contiguous_words(
        ["ABOVE", "ONE", "OF", "THE", "IS", "VOLCANO"])
    p = dict(CAPTION_TEMPLATES["bold_01"])
    _al, ax, ay = _preset_anchor(p, 1920, 1080)
    ax, ay = _clamp_burn_xy(ax, ay, 1920, 1080, 1.0)
    lead = p.get("lead_ms", -20) / 1000.0
    floor_s = p.get("floor_ms", 140) / 1000.0
    hold_s = p.get("hold_ms", 200) / 1000.0
    words_n = _norm_cap_words(words, p.get("text_case"))
    chunks = chunk_words(words_n, max_words=p.get("max_words", 4),
                         max_dur=2.5, gap_break=0.4)
    assert len(chunks) >= 2
    lines = _render_typewriter_preset(p, chunks, ax, ay, 1.0, lead,
                                      floor_s, hold_s)
    _, overs = _layers(lines)
    assert overs
    for (s1, e1), (s2, e2) in zip(overs, overs[1:]):
        assert e1 <= s2 + 1e-6, f"typewriter overlap: {e1:.3f} > {s2:.3f}"
