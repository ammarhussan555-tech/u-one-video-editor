"""Sentence/scene detection with visual pacing modes."""
import re
from dataclasses import dataclass

CONJ = re.compile(r",\s*(and|but|while|whereas|which|who)\s+", re.I)


@dataclass
class Scene:
    id: int
    sentence_index: int
    text: str
    sub_index: int = 0
    locked: bool = False
    first_sentence: int = 0   # ordinal of first sentence covered (into sentence_texts)
    last_sentence: int = 0    # ordinal of last sentence covered


def _split_long(sentence, max_words=22):
    words = sentence.split()
    if len(words) <= max_words:
        return [sentence]
    m = CONJ.search(sentence)
    if m:
        a, b = sentence[:m.start()], sentence[m.start() + 2:]
        if len(a.split()) >= 6 and len(b.split()) >= 6:
            return [a.strip(" ,"), b.strip(" ,")]
    mid = len(words) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def detect_scenes(sentence_texts, mode="every_sentence"):
    """Modes: every_sentence | every_2 | every_3 | ai_auto.

    every_sentence and ai_auto now use phrase-level visual pacing
    (Uzair 2026-10-03 rules): sentences are split at natural pauses
    into 2-3s clips.
    """
    # Phrase-level pacing for the standard modes.
    if mode in ("every_sentence", "ai_auto"):
        return plan_phrase_scenes(sentence_texts)
    scenes, sid = [], 0
    n = len(sentence_texts)
    if mode == "every_2":
        spans = [(i, min(i + 1, n - 1)) for i in range(0, n, 2)]
        groups = [sentence_texts[i:i + 2] for i in range(0, n, 2)]
    elif mode == "every_3":
        spans = [(i, min(i + 2, n - 1)) for i in range(0, n, 3)]
        groups = [sentence_texts[i:i + 3] for i in range(0, n, 3)]
    else:
        spans = [(i, i) for i in range(n)]
        groups = [[s] for s in sentence_texts]
    for gi, (group, (f, l)) in enumerate(zip(groups, spans)):
        if mode == "ai_auto" and len(group) == 1:
            parts = _split_long(group[0])
            if len(parts) > 1:
                for pi, p in enumerate(parts):
                    scenes.append(Scene(id=sid, sentence_index=gi, text=p, sub_index=pi,
                                        first_sentence=f, last_sentence=l))
                    sid += 1
                continue
        scenes.append(Scene(id=sid, sentence_index=gi, text=" ".join(group),
                            first_sentence=f, last_sentence=l))
        sid += 1
    return scenes

# -- Visual pacing rules (Uzair 2026-10-03) --
# Rule 2: split sentences into phrases at natural pauses.
# Rule 3: every clip 2-3s (at ~2.5 words/sec: 5-8 words).
#         <2s -> merge with next; >3s -> split into two.
# Rule 7: visual appears 0.3s before the matching words.

_WORDS_PER_SEC = 2.5
_MIN_CLIP_SEC = 2.0
_MAX_CLIP_SEC = 3.0
_VISUAL_LEAD_SEC = 0.3

# Split on commas, semicolons, colons, dashes, parentheses.
_PHRASE_SPLIT = re.compile(r"\s*(,|;|:|--|—|–|\(|\))\s*")


def split_into_phrases(sentence):
    """Split a sentence into short phrases at natural pauses.

    Returns a list of phrase strings (delimiters stripped).
    """
    parts = _PHRASE_SPLIT.split(sentence)
    phrases, current = [], ""
    for p in parts:
        if not p or not p.strip():
            continue
        if _PHRASE_SPLIT.fullmatch(p.strip() + " " * 0) or p.strip() in (
                ",", ";", ":", "--", "—", "–", "(", ")"):
            # Delimiter: end the current phrase here.
            if current.strip():
                phrases.append(current.strip())
                current = ""
        else:
            current = (current + " " + p).strip()
    if current.strip():
        phrases.append(current.strip())
    # Also split very long phrases on 'and'/'but' breath points.
    out = []
    for ph in phrases:
        words = ph.split()
        if len(words) > 12:
            m = re.search(r"\s+(and|but|while|whereas)\s+", ph, re.I)
            if m:
                a, b = ph[:m.start()].strip(), ph[m.end():].strip()
                if len(a.split()) >= 3 and len(b.split()) >= 3:
                    out.extend([a, b])
                    continue
        out.append(ph)
    return [p for p in out if p]


def _est_sec(phrase):
    """Estimate spoken seconds for a phrase."""
    return len(phrase.split()) / _WORDS_PER_SEC


def enforce_clip_duration(phrases):
    """Merge <2s phrases with the next; split >3s phrases in two.

    Returns a new list of phrase strings.
    """
    # Merge short phrases forward.
    merged = []
    i = 0
    while i < len(phrases):
        ph = phrases[i]
        # Merge with next while under 2s and a next exists.
        while _est_sec(ph) < _MIN_CLIP_SEC and i + 1 < len(phrases):
            i += 1
            ph = (ph + " " + phrases[i]).strip()
        merged.append(ph)
        i += 1
    # Split long phrases (>3s) into two at the midpoint.
    # Only split if BOTH halves stay >=2s (5+ words each); otherwise
    # keep the slightly-long phrase rather than making a <2s clip.
    out = []
    for ph in merged:
        if _est_sec(ph) > _MAX_CLIP_SEC:
            words = ph.split()
            if len(words) >= 10:
                mid = len(words) // 2
                # Try to split at a comma/pause near the middle.
                best = mid
                for j in range(mid - 2, mid + 3):
                    if 0 < j < len(words) and words[j - 1].endswith(","):
                        best = j
                        break
                # Ensure both halves are >=5 words.
                if best < 5:
                    best = 5
                if len(words) - best < 5:
                    best = len(words) - 5
                a = " ".join(words[:best]).strip(" ,")
                b = " ".join(words[best:]).strip(" ,")
                if a and b:
                    out.extend([a, b])
                    continue
        out.append(ph)
    return out


def plan_phrase_scenes(sentence_texts):
    """Build phrase-level Scenes following the visual pacing rules.

    Each sentence -> phrases at natural pauses -> 2-3s clips.
    Returns a list of Scene (text=phrase).
    """
    scenes, sid = [], 0
    for si, sent in enumerate(sentence_texts):
        phrases = enforce_clip_duration(split_into_phrases(sent))
        if not phrases:
            phrases = [sent]
        for pi, ph in enumerate(phrases):
            scenes.append(Scene(id=sid, sentence_index=si, text=ph,
                                sub_index=pi, first_sentence=si,
                                last_sentence=si))
            sid += 1
    return scenes
