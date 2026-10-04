"""Documentary 1-click mode: script analysis unit tests (niche-agnostic)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.documentary import (analyze_script, extract_quotes,
                              extract_lowerthirds, plan_documentary)

SAMPLE = """THE RISE

In 1992, Imran Khan lifted the Cricket World Cup in Melbourne. It was a
historic victory that made him a national hero across Pakistan.

"The nation needs a leader who will never give up," he declared at a rally
in Lahore. His supporters chanted his name for hours.

THE FALL

On 9 May 2023, he was arrested outside the Islamabad High Court. The arrest
triggered massive protests in every major city. "This is a shocking betrayal
of democracy," his party announced.

THE STREETS

By November 2022, thousands marched toward the capital. The convoy reached
Wazirabad on 3 November 2022, where shots were fired at the container.
"""


def test_analyze_script_chapters():
    ch = analyze_script(SAMPLE)
    assert len(ch) == 3, f"expected 3 chapters, got {len(ch)}: {ch}"
    fracs = [f for f, _ in ch]
    assert fracs == sorted(fracs), "chapters must be in script order"
    assert all(0.0 <= f <= 1.0 for f in fracs)
    titles = [t for _, t in ch]
    assert "THE RISE" in titles[0], titles
    assert all(t == t.upper() for t in titles), "titles must be ALL CAPS"
    assert all(len(t.split()) <= 4 for t in titles), "titles <= 4 words"


def test_analyze_script_fallback_no_headings():
    flat = " ".join([f"Sentence number {i} about global markets." for i in range(40)])
    ch = analyze_script(flat)
    assert 2 <= len(ch) <= 6, f"fallback chapters out of range: {len(ch)}"


def test_extract_quotes_hero_words_not_random():
    qs = extract_quotes(SAMPLE)
    assert len(qs) >= 2, f"expected quoted speech found, got {qs}"
    for q in qs:
        assert q["text"], "quote text must not be empty"
        for hw in q["hero_words"]:
            assert hw.lower() in q["text"].lower(), (
                f"hero word {hw!r} not in quote {q['text']!r} -- "
                "hero words must come from the quote itself")


def test_extract_lowerthirds():
    lts = extract_lowerthirds(SAMPLE)
    assert lts, "expected lower thirds from people/dates"
    for lt in lts:
        assert lt["kicker"] and lt["headline"], lt
        assert 0.0 <= lt["frac"] <= 1.0


def test_plan_documentary_timings():
    # Fake sentence timings: 10 sentences across 100s.
    sents = [s for s in SAMPLE.replace("\n", " ").split(". ") if s.strip()]
    n = len(sents)
    timings = [{"text": s, "start": i * 100.0 / n, "end": (i + 1) * 100.0 / n}
               for i, s in enumerate(sents)]
    plan = plan_documentary(SAMPLE, timings, 100.0,
                            cache_dir=os.path.join(os.path.dirname(__file__),
                                                   "..", "uone_test_out",
                                                   "doc_geo_cache"))
    assert plan.has_any()
    assert len(plan.chapters) == 3
    for c in plan.chapters:
        assert 0.0 <= c.start <= 100.0, c
    for q in plan.quotes:
        assert 0.0 <= q.start < q.end <= 106.0, q
    for lt in plan.lower_thirds:
        assert 0.0 <= lt.start <= 100.0, lt
