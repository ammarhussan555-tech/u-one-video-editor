"""Project system: new/save/open/autosave/resume + undo/redo."""
import copy
import json
import os
import threading
import time


class Project:
    def __init__(self, name="Untitled"):
        self.name = name
        self.script = ""
        self.settings = {}
        self.timeline = None          # Timeline (set by pipeline)
        self.scenes = []              # list[dict]
        self.assets = {}              # scene_id -> Asset dict
        self.warnings = []
        self.path = None
        self._undo = []
        self._redo = []
        self._stop = threading.Event()
        self._autosave_t = None

    # ---- persistence ----
    def to_dict(self):
        # never persist raw API keys into project files
        clean = {k: v for k, v in (self.settings or {}).items()
                 if "key" not in k.lower()}
        return {"name": self.name, "script": self.script, "settings": clean,
                "timeline": self.timeline.to_dict() if self.timeline else None,
                "scenes": self.scenes, "assets": self.assets, "warnings": self.warnings}

    @staticmethod
    def from_dict(d):
        from src.timeline import Timeline
        p = Project(d.get("name", "Untitled"))
        p.script = d.get("script", "")
        p.settings = d.get("settings", {})
        p.scenes = d.get("scenes", [])
        p.assets = d.get("assets", {})
        p.warnings = d.get("warnings", [])
        if d.get("timeline"):
            p.timeline = Timeline.from_dict(d["timeline"])
        return p

    def save(self, path=None):
        path = path or self.path
        if not path:
            raise ValueError("No path set for project.")
        self.path = path
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as f:
            p = Project.from_dict(json.load(f))
        p.path = path
        return p

    # ---- undo/redo (timeline snapshots) ----
    def snapshot(self):
        if self.timeline:
            self._undo.append(copy.deepcopy(self.timeline.to_dict()))
            self._redo.clear()

    def undo(self):
        if self._undo and self.timeline:
            self._redo.append(copy.deepcopy(self.timeline.to_dict()))
            self.timeline.restore(self._undo.pop())
            return True
        return False

    def redo(self):
        if self._redo and self.timeline:
            self._undo.append(copy.deepcopy(self.timeline.to_dict()))
            self.timeline.restore(self._redo.pop())
            return True
        return False

    # ---- autosave ----
    def start_autosave(self, interval=60, directory=None):
        if self._autosave_t and self._autosave_t.is_alive():
            return

        def _loop():
            while not self._stop.wait(interval):
                try:
                    target = self.path
                    if not target and directory:
                        safe = "".join(c if c.isalnum() or c in " _-" else "_"
                                      for c in (self.name or "Untitled"))[:40]
                        target = os.path.join(directory, f"{safe}.uvp.json")
                    if target:
                        self.save(target + ".autosave")
                except Exception:  # noqa: BLE001
                    pass

        self._stop.clear()
        self._autosave_t = threading.Thread(target=_loop, daemon=True)
        self._autosave_t.start()

    def stop_autosave(self):
        self._stop.set()
