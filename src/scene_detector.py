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
    """Modes: every_sentence | every_2 | every_3 | ai_auto."""
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
