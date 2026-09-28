"""Project manager tests: save/load round-trip, failure snapshots,
render-state save/load/clear, autosave wiring."""
import json
import os
import time

from src.project import Project
from src.project_manager import ProjectManager


def test_project_save_load_roundtrip(tmp_path):
    p = Project("My Video")
    p.script = "Hello world."
    p.settings = {"fps": 30}
    p.scenes = [{"id": 0}]
    p.warnings = ["w1"]
    path = str(tmp_path / "p1.uvp")
    p.save(path)
    q = Project.load(path)
    assert q.name == "My Video"
    assert q.script == "Hello world."
    assert q.settings["fps"] == 30
    assert q.scenes == [{"id": 0}]
    assert q.warnings == ["w1"]
    assert "fps" in q.to_dict()["settings"]
    assert "key" not in str(q.to_dict())  # keys never persisted


def test_load_legacy_project(tmp_path):
    legacy = {"name": "Old", "script": "hi", "settings": {},
              "timeline": None, "scenes": [], "updated": 1.0}
    path = str(tmp_path / "legacy.uvp")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    q = Project.load(path)
    assert q.name == "Old"
    assert q.script == "hi"


def test_failure_snapshot_contains_everything(tmp_path):
    p = Project("Fail Vid")
    p.script = "some script"
    pm = ProjectManager(p, str(tmp_path))
    try:
        raise RuntimeError("boom happened")
    except RuntimeError as e:
        snap = pm.save_failure_snapshot(
            str(tmp_path), e, render_log_path="/logs/r.log",
            ffmpeg_commands=["ffmpeg -i a b"],
            scene_states=[{"index": 0, "exists": False}])
    assert os.path.basename(snap) == "last_error.uvp.json"
    with open(snap, encoding="utf-8") as f:
        data = json.load(f)
    assert data["error"]["type"] == "RuntimeError"
    assert data["error"]["reason"] == "boom happened"
    assert data["render_log"] == "/logs/r.log"
    assert data["ffmpeg_commands"] == ["ffmpeg -i a b"]
    assert data["scene_states"][0]["index"] == 0
    assert data["project"]["name"] == "Fail Vid"
    assert "saved_at" in data


def test_render_state_save_load_clear(tmp_path):
    d = str(tmp_path)
    state = {"stages_done": ["s1", "s2"], "voice_path": "/v.wav"}
    ProjectManager.save_render_state(d, state)
    loaded = ProjectManager.load_render_state(d)
    assert loaded["stages_done"] == ["s1", "s2"]
    ProjectManager.clear_render_state(d)
    assert ProjectManager.load_render_state(d) == {}


def test_manager_save_delegates_to_project(tmp_path):
    p = Project("n")
    p.script = "s"
    pm = ProjectManager(p)
    path = str(tmp_path / "m.uvp")
    pm.save(path)
    assert os.path.isfile(path)
    assert Project.load(path).name == "n"
