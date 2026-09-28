"""Heuristic visual relevance scoring.

Swap `score_candidate` with a CLIP/vision model scorer later -- the interface
(candidate dict, SentenceAnalysis) -> float stays the same.
"""


def score_candidate(cand, analysis):
    text = ((cand.get("tags") or "") + " " + (cand.get("query") or "")).lower()
    kws = [k.lower() for k in (analysis.keywords or [])]
    kws += [p.lower() for p in (analysis.phrases or [])]
    kws = [k for k in kws if k]
    hit = sum(1 for k in kws if k in text)
    kw_score = min(1.0, (hit / max(1, len(kws))) * 2.0)
    w = cand.get("w", 0) or 0
    res_score = 1.0 if w >= 1280 else 0.6 if w >= 640 else 0.25
    topic_bonus = 0.12 if analysis.topic and analysis.topic in text else 0.0
    return round(0.68 * kw_score + 0.22 * res_score + topic_bonus, 3)


def is_acceptable(score, threshold=0.30):
    return score >= threshold
