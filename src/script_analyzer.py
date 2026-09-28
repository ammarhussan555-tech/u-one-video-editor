"""Universal script analysis. No hard-coded topic: everything is derived from the script."""
import re
from collections import Counter
from dataclasses import dataclass, field

STOPWORDS = set(
    "a,an,the,and,or,but,if,then,else,when,while,of,at,by,for,with,about,into,through,"
    "during,before,after,above,below,to,from,up,down,in,out,on,off,over,under,again,"
    "further,once,here,there,all,any,both,each,few,more,most,other,some,such,no,nor,not,"
    "only,own,same,so,than,too,very,can,will,just,should,now,is,are,was,were,be,been,"
    "being,have,has,had,having,do,does,did,doing,would,could,ought,i,you,he,she,it,we,they,"
    "them,his,her,its,our,their,this,that,these,those,am,as,also,between,because,until,"
    "per,via,within,without,may,might,must,shall,one,two,first,second".split(","))

TOPIC_KEYWORDS = {
    "space": "space nasa planet galaxy star orbit astronaut moon mars telescope cosmos satellite rocket launch".split(),
    "science": "scientist scientists research study experiment laboratory discovery theory data evidence quantum particle detected monitor".split(),
    "disaster": "earthquake volcano tsunami hurricane tornado flood eruption seismic magnitude aftershock disaster lava".split(),
    "weather": "weather storm rain snow temperature climate forecast wind cloud lightning".split(),
    "history": "century war empire king queen ancient medieval battle treaty revolution civilization historic".split(),
    "finance": "market stock economy inflation bank investment dollar revenue profit trade crypto price".split(),
    "health": "health disease virus vaccine doctor hospital symptom treatment pandemic medicine patient".split(),
    "technology": "technology software robot computer internet chip startup digital algorithm network".split(),
    "nature": "animal wildlife forest ocean river mountain bird fish lion elephant nature wild".split(),
    "geopolitics": "government president minister election treaty sanction border military army conflict".split(),
    "aviation": "airplane flight airport pilot airline runway aircraft".split(),
}

MOOD_WORDS = {
    "dramatic": "dramatic shocking unbelievable crisis breaking urgent deadly catastrophic".split(),
    "suspense": "mystery unknown secret hidden suddenly strange unusual".split(),
    "calm": "peaceful calm gentle serene quiet slow".split(),
    "emotional": "tragic heartbreaking emotional tears loss hope".split(),
    "scientific": "detected measured analysis evidence hypothesis observed recorded".split(),
    "news": "breaking reported officials announced according sources".split(),
}


@dataclass
class SentenceAnalysis:
    text: str
    keywords: list = field(default_factory=list)
    phrases: list = field(default_factory=list)
    dates: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    topic: str = "general"
    topic_scores: dict = field(default_factory=dict)
    mood: str = "neutral"


@dataclass
class ScriptAnalysis:
    sentences: list = field(default_factory=list)
    topic: str = "general"
    mood: str = "neutral"
    total_words: int = 0


def split_sentences(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []
    for abbr in ["Mr.", "Mrs.", "Ms.", "Dr.", "St.", "vs.", "etc.", "Inc.", "Ltd.",
                 "Jr.", "Sr.", "e.g.", "i.e.", "U.S.", "U.K."]:
        text = text.replace(abbr, abbr.replace(".", "<DOT>"))
    parts = re.split(r'(?<=[.!?…])\s+(?=[A-Z0-9"\'“‘(\[])', text)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


def _tokens(text):
    return re.findall(r"[a-zA-Z']+", text.lower())


def _keywords(sentence, top=8):
    counts = Counter(t for t in _tokens(sentence) if t not in STOPWORDS and len(t) > 2)
    return [w for w, _ in counts.most_common(top)]


def _phrases(sentence):
    found = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", sentence)
    seen, out = set(), []
    for f in found:
        fl = f.lower()
        if fl in STOPWORDS or fl in seen:
            continue
        seen.add(fl)
        out.append(f)
    return out[:6]


def _topic_scores(sentence):
    toks = set(_tokens(sentence))
    scores = {}
    for topic, words in TOPIC_KEYWORDS.items():
        s = sum(1 for w in words if w in toks)
        if s:
            scores[topic] = s
    return scores


def _mood(sentence):
    toks = set(_tokens(sentence))
    best, bestn = "neutral", 0
    for mood, words in MOOD_WORDS.items():
        n = sum(1 for w in words if w in toks)
        if n > bestn:
            best, bestn = mood, n
    return best


def analyze_sentence(text):
    scores = _topic_scores(text)
    topic = max(scores, key=scores.get) if scores else "general"
    return SentenceAnalysis(
        text=text,
        keywords=_keywords(text),
        phrases=_phrases(text),
        dates=re.findall(
            r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\b\d{4}\b|"
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s+\d{4})", text),
        numbers=re.findall(r"\b\d+(?:\.\d+)?%?\b", text),
        topic=topic, topic_scores=scores, mood=_mood(text),
    )


def analyze_script(text):
    sents = [analyze_sentence(s) for s in split_sentences(text)]
    total = Counter()
    for s in sents:
        for t, n in s.topic_scores.items():
            total[t] += n
    topic = total.most_common(1)[0][0] if total else "general"
    moods = Counter(s.mood for s in sents if s.mood != "neutral")
    mood = moods.most_common(1)[0][0] if moods else "neutral"
    return ScriptAnalysis(sentences=sents, topic=topic, mood=mood,
                          total_words=sum(len(_tokens(s.text)) for s in sents))


def build_search_query(sa: SentenceAnalysis, script_topic="general", max_terms=10):
    """Rich multi-concept query (entities + keywords + topic context), never one keyword."""
    terms = []
    for p in sa.phrases:
        terms.append(p)
    joined = " ".join(terms).lower()
    for k in sa.keywords:
        if k not in joined:
            terms.append(k)
            joined += " " + k
    for w in TOPIC_KEYWORDS.get(sa.topic, [])[:4] + TOPIC_KEYWORDS.get(script_topic, [])[:2]:
        if w not in joined:
            terms.append(w)
            joined += " " + w
    seen, out = set(), []
    for t in terms:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(t)
    return " ".join(out[:max_terms])
