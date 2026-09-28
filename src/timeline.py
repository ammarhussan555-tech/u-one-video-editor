"""6-track timeline model: voice / video / text / captions / sfx / music."""
import copy
from dataclasses import dataclass, field

TRACKS = ["voice", "video", "text", "captions", "sfx", "music"]


@dataclass
class Clip:
    id: str
    track: str
    start: float
    end: float
    kind: str = ""
    label: str = ""
    payload: dict = field(default_factory=dict)
    locked: bool = False

    def to_dict(self):
        return {"id": self.id, "track": self.track, "start": self.start,
                "end": self.end, "kind": self.kind, "label": self.label,
                "payload": self.payload, "locked": self.locked}

    @staticmethod
    def from_dict(d):
        return Clip(**d)


class Timeline:
    def __init__(self):
        self.clips: list[Clip] = []
        self._n = 0

    def add(self, track, start, end, kind="", label="", payload=None, locked=False):
        assert track in TRACKS, f"unknown track {track}"
        self._n += 1
        c = Clip(id=f"c{self._n}", track=track, start=round(float(start), 3),
                 end=round(float(end), 3), kind=kind, label=label,
                 payload=payload or {}, locked=locked)
        self.clips.append(c)
        return c

    def by_track(self, track):
        return sorted([c for c in self.clips if c.track == track], key=lambda c: c.start)

    def get(self, clip_id):
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    def duration(self):
        return round(max([c.end for c in self.clips] + [0.0]), 3)

    def remove(self, clip_id):
        self.clips = [c for c in self.clips if not (c.id == clip_id and not c.locked)]

    def move(self, clip_id, new_start):
        c = self.get(clip_id)
        if c and not c.locked:
            d = new_start - c.start
            c.start = round(new_start, 3)
            c.end = round(c.end + d, 3)

    def trim(self, clip_id, new_start=None, new_end=None):
        c = self.get(clip_id)
        if c and not c.locked:
            if new_start is not None and new_start < c.end:
                c.start = round(new_start, 3)
            if new_end is not None and new_end > c.start:
                c.end = round(new_end, 3)

    def to_dict(self):
        return {"clips": [c.to_dict() for c in self.clips], "_n": self._n}

    @staticmethod
    def from_dict(d):
        tl = Timeline()
        tl._n = d.get("_n", 0)
        tl.clips = [Clip.from_dict(c) for c in d.get("clips", [])]
        return tl

    def snapshot(self):
        return copy.deepcopy(self.to_dict())

    def restore(self, snap):
        other = Timeline.from_dict(copy.deepcopy(snap))
        self.clips, self._n = other.clips, other._n
