"""U One - render engine (v2).

The 11-stage render pipeline with clear status per stage, structured
errors, cancellation, resume, and full observability:

   1 Project validation      2 Script processing        3 Voiceover
   4 Scene generation        5 Scene validation         6 Scene recovery
   7 Media normalization     8 Timeline construction    9 Final render
  10 Final MP4 validation   11 Export

Every scene segment is validated before it may enter the render queue;
invalid scenes are recovered automatically (max 3 attempts) and the
last resort is a guaranteed-valid bright fallback card. Render inputs
are pre-flight probed before ffmpeg ever runs. Nothing about a failure
is hidden: the per-render log holds every FFmpeg command with full
stderr, and errors carry stage / scene / exit code / reason / hint.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import cache_manager
from . import secure_store
from .audio_mixer import (ensure_sfx_library, mix_audio, place_sfx,
                           place_caption_sfx,
                          select_music, synth_ambient_bed)
from .error_recovery import RenderCancelled, UOneError, ValidationError
from .export_engine import ExportEngine
from .ffmpeg_engine import FFmpegEngine
from .log_system import RenderLogger
from .media_normalizer import TargetSpec, hw_target_spec, normalize_all, normalize_segment
from .media_search import Asset, MediaFinder
from .project_manager import ProjectManager
from .qc_engine import run_qc
from .scene_generator import SceneGenerator, SceneResult, SceneSpec
from .script_engine import (analyze_script, analyze_sentence,
                            build_search_query, detect_scenes)
from .settings import Settings
from .temp_manager import TempManager, ensure_free_space
from .text_engine import (build_ass, build_srt, make_overlay_text,
                          make_overlay,
                          headline_preset_for, headline_accent_for,
                          detect_niche, select_main_text, story_box_color)
from .timeline_engine import Timeline
from .voiceover import audio_duration, get_sentence_timings, synthesize_speech, whisper_word_timings

STAGES = [
    "Project validation",
    "Script processing",
    "Voiceover",
    "Scene generation",
    "Scene validation",
    "Scene recovery",
    "Media normalization",
    "Timeline construction",
    "Final render",
    "Final MP4 validation",
    "Export",
]


def _api_key(settings: Settings, service: str) -> str:
    env = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY",
           "google_key": "GOOGLE_API_KEY", "google_cx": "GOOGLE_CX",
           "serper": "SERPER_API_KEY", "gemini": "GEMINI_API_KEY",
           "reddit_id": "REDDIT_CLIENT_ID",
           "reddit_secret": "REDDIT_CLIENT_SECRET"}[service]
    if os.environ.get(env):
        return os.environ[env]
    stored = secure_store.get_key(service)
    if stored:
        return stored
    v = settings.get(service + "_key", "")
    if v:
        return v
    return (settings.get("api_keys", {}) or {}).get(service, "")


def _concat_list_escape(p: str) -> str:
    return p.replace("\\", "/").replace("'", "'\\''")


class RenderEngine:
    def __init__(self, pm: ProjectManager, work_dir: str,
                 settings: Settings,
                 render_log: Optional[RenderLogger] = None,
                 cancel_event: Optional[threading.Event] = None,
                 progress_cb: Optional[Callable] = None,
                 log_cb: Optional[Callable] = None):
        self.pm = pm
        self.work_dir = work_dir
        self.settings = settings
        self.cancel_event = cancel_event or threading.Event()
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.rlog = render_log or RenderLogger(
            getattr(pm.project, "id", "project"),
            getattr(pm.project, "name", ""))
        self.engine = FFmpegEngine(cancel_event=self.cancel_event,
                                   log=self.rlog)
        self.assets_dir = os.path.join(work_dir, "assets")
        self.seg_dir = os.path.join(work_dir, "segments")
        self.audio_dir = os.path.join(work_dir, "audio")
        for d in (self.assets_dir, self.seg_dir, self.audio_dir):
            os.makedirs(d, exist_ok=True)

        # stage artifacts (filled as stages complete; used by resume)
        self.analysis = None
        self.scenes: list = []
        self.sentence_timings: list = []
        self.scene_timings: list = []
        self.voice_path = ""
        self.word_timings = None
        self.scene_results: List[SceneResult] = []
        self.normalized: List[str] = []
        self.timeline = Timeline()
        self.mixed_path = ""
        self.warnings: list = []
        self.ffmpeg_commands: list = []
        self.assets: Dict[int, Asset] = {}
        self.sfx_library: dict = {}
        # Main-text engine context (pop-in budget, words/minute ceiling,
        # cross-scene dedupe) — lives for the whole render.
        self._mt_context: dict = {}
        self.out_w = 0
        self.out_h = 0
        self._final_path = ""
        self._edit_session_path = ""

    # -- helpers --
    def _check(self):
        if self.cancel_event.is_set():
            raise RenderCancelled("cancelled by user")

    def _stage(self, i: int, msg: str = ""):
        self.rlog.stage(i + 1, STAGES[i], msg)
        if self.progress_cb:
            self.progress_cb(i, STAGES[i], msg)

    def _msg(self, msg: str):
        self.rlog.write(msg)
        if self.log_cb:
            self.log_cb(msg)

    def _save_state(self, stages_done: List[str], extra: dict = None):
        state = {"stages_done": stages_done,
                 "voice_path": self.voice_path,
                 "sentence_timings": self.sentence_timings,
                 "segments": [r.segment_path for r in self.scene_results],
                 "scene_meta": [{"index": r.spec.index,
                                 "scene_id": r.spec.scene_id,
                                 "asset_id": r.asset_id,
                                 "attempts": r.attempts,
                                 "recovered": r.recovered}
                                for r in self.scene_results],
                 "normalized": self.normalized,
                 "mixed": self.mixed_path}
        if extra:
            state.update(extra)
        ProjectManager.save_render_state(self.work_dir, state)

    # -- main run --
    def run(self, preview: bool = False, resume: bool = False) -> str:
        S = self.settings
        started = time.time()
        self.rlog.write(f"Run started (preview={preview}, resume={resume})")
        TempManager.purge_stale(self.work_dir)
        state = ProjectManager.load_render_state(self.work_dir) if resume else {}
        done = set(state.get("stages_done", []))
        # Unique temp subdir per run: two renders can never delete or
        # reuse each other's temp files (e.g. concat_list_0001.txt).
        run_token = uuid.uuid4().hex[:12]
        try:
            with TempManager(self.work_dir, subdir=f"_tmp/run_{run_token}") as tmp:
                self._tmp = tmp
                self._run_stages(preview, done, state)
        except RenderCancelled:
            self._msg("Render cancelled by user.")
            self.rlog.finish(False, "cancelled")
            self._persist()
            raise
        except Exception as e:  # noqa: BLE001 - snapshot, then re-raise
            self._on_failure(e, preview)
            raise
        elapsed = time.time() - started
        self._msg(f"Done in {elapsed:.0f}s")
        return self._final_path

    @property
    def edit_session_path(self) -> str:
        """Path to caption_edit.json saved after a successful render."""
        return self._edit_session_path

    def _run_stages(self, preview: bool, done: set, state: dict):
        S = self.settings
        w, h = S.resolution
        if preview:
            w, h = (640, 360)
        self.out_w, self.out_h = w, h

        # -- 1. project validation --
        if "s1" not in done:
            self._stage(0)
            problems = S.validate()
            if problems:
                raise ValidationError("Invalid settings: " + "; ".join(problems),
                                      stage=STAGES[0])
            if not (self.pm.project.script or "").strip():
                raise ValidationError("Script is empty.", stage=STAGES[0])
            if not self.engine.check():
                raise ValidationError(
                    "FFmpeg was not found, so the video cannot be rendered. "
                    "U One normally bundles FFmpeg - please reinstall U One.",
                    stage=STAGES[0],
                    recovery_hint="reinstall U One or put ffmpeg on PATH")
            ensure_free_space(self.work_dir, needed_mb=128)
            self._msg("Project validation OK.")
            done.add("s1")

        # -- 2. script processing --
        if "s2" not in done:
            self._stage(1)
            self._check()
            self.analysis = analyze_script(self.pm.project.script)
            sent_texts = [s.text for s in self.analysis.sentences]
            self.scenes = detect_scenes(
                sent_texts, S.get("visual_mode", "every_sentence"))
            self._msg(f"Topic: {self.analysis.topic} | Mood: {self.analysis.mood} | "
                      f"Sentences: {len(sent_texts)} | Scenes: {len(self.scenes)}")
            done.add("s2")

        # -- 3. voiceover --
        if "s3" not in done:
            self._stage(2)
            self._check()
            self._prepare_voice()
            self._save_state(["s1", "s2", "s3"])
            done.add("s3")
        else:
            self.voice_path = state.get("voice_path", "")
            self.sentence_timings = state.get("sentence_timings", [])

        # -- 4/5/6. scene generation + validation + recovery --
        if "s4" not in done:
            self._stage(3, "searching + rendering + validating")
            self._check()
            self._map_scene_timings()
            from .scene_detector import detect_keyword_insert
            specs = [SceneSpec(index=i, scene_id=sc.id, text=sc.text,
                               duration=max(0.6, t["end"] - t["start"]),
                               keyword_insert=detect_keyword_insert(sc.text))
                     for i, (sc, t) in enumerate(
                         zip(self.scenes, self.scene_timings))]
            # disk estimate: ~6 MB per output second covers segments,
            # normalized copies, concat and final at 1080p+, plus headroom
            need_mb = 256 + sum(s.duration for s in specs) * 6
            ensure_free_space(self.work_dir, needed_mb=need_mb)
            gen = SceneGenerator(
                seg_dir=self.seg_dir, assets_dir=self.assets_dir,
                width=w, height=h, fps=S.fps,
                search_fn=self._search_fn(), render_log=self.rlog,
                cancel_event=self.cancel_event, cache=cache_manager)

            def _prog(dn, total, idx):
                self._stage(3, f"scene {dn}/{total} validated")
                if self.progress_cb:
                    self.progress_cb(3, STAGES[3], f"{dn}/{total}")

            self.scene_results = gen.generate_all(specs, progress_cb=_prog)
            self.assets = {r.spec.scene_id: r.asset for r in self.scene_results
                           if r.asset is not None}
            self._save_state(["s1", "s2", "s3", "s4"])
            done.add("s4")
        else:
            self._restore_scene_results(state, w, h)

        self._stage(4, f"{len(self.scene_results)}/{len(self.scene_results)} "
                       f"segments valid")
        for r in self.scene_results:
            rep = r.report.describe() if r.report else "cached-valid"
            self.rlog.scene(r.spec.scene_id + 1,
                            f"segment OK ({r.asset_id}, "
                            f"attempts={r.attempts}): {rep}")
        done.add("s5")

        recovered = [r for r in self.scene_results if r.recovered]
        self._stage(5, f"{len(recovered)} scene(s) recovered via fallback")
        for r in recovered:
            self._msg(f"Scene {r.spec.scene_id + 1}: recovered "
                      f"({'; '.join(r.warnings) or 'retry succeeded'})")
        done.add("s6")

        # -- 7. media normalization --
        if "s7" not in done:
            self._stage(6, "unifying codec/resolution/fps")
            self._check()
            spec = hw_target_spec(width=w, height=h, fps=float(S.fps),
                                  engine=self.engine)
            seg_paths = [r.segment_path for r in self.scene_results]
            self.normalized = normalize_all(seg_paths, spec, self.work_dir,
                                            self.engine)
            self._save_state(["s1", "s2", "s3", "s4", "s7"])
            done.add("s7")
        else:
            self.normalized = [p for p in state.get("normalized", [])
                               if os.path.isfile(p)]

        # -- 8. timeline construction (+captions, sfx, music, mix) --
        if "s8" not in done:
            self._stage(7)
            self._check()
            self._build_timeline(preview)
            self._build_captions(w, h, preview)
            self._build_audio()
            self._save_state(["s1", "s2", "s3", "s4", "s7", "s8"])
            done.add("s8")

        # -- QC gate (fatals block before any final encode) --
        qc = run_qc(self.normalized, self.mixed_path,
                    voice_path=self.voice_path, timeline=self.timeline,
                    srt_path=os.path.join(self.audio_dir, "captions.srt"),
                    engine=self.engine)
        for warn in qc.warnings:
            self.rlog.warning(warn)
            self._msg("QC warning: " + warn)
        self.warnings = qc.warnings
        if not qc.ok:
            raise ValidationError(
                "Quality control blocked the render: " + "; ".join(qc.fatals),
                stage="QC gate",
                recovery_hint="see render log; re-run scene generation")

        # -- 9/10/11. concat -> final render -> validate -> export --
        self._stage(8, "rendering")
        self._check()
        concat = os.path.join(self.work_dir, "concat.mp4")
        self._concat(self.normalized, concat)

        fmt = S.get("output_format", "16:9")
        res = S.get("output_resolution", "1080p")
        ass = os.path.join(self.audio_dir, "captions.ass")
        out = os.path.join(self.work_dir,
                           "preview.mp4" if preview else "final.mp4")
        exporter = ExportEngine(engine=self.engine, render_log=self.rlog,
                                cancel_event=self.cancel_event,
                                progress_cb=self.progress_cb)
        # Documentary 1-click mode (Uzair 2026-10-04): overlays are merged
        # into the caption burn pass inside render_final (single full
        # encode) instead of a separate post pass. Default OFF -- the OFF
        # path is untouched.
        doc_mode = bool(S.get("documentary_mode", False))
        result = exporter.render_final(
            concat, self.mixed_path, ass if os.path.isfile(ass) else "",
            out, fmt=fmt, res=res, fps=S.fps,
            codec=S.get("output_codec", "h264"), preview=preview,
            work_dir=self.work_dir,
            prefer_hw=bool(S.get("prefer_hwaccel", True)),
            doc_script=(getattr(self.pm.project, "script", "") or "")
            if doc_mode else "",
            doc_timings=self.sentence_timings if doc_mode else None,
            doc_cache_dir=os.path.join(self.work_dir, "documentary_assets")
            if doc_mode else "")
        self._msg(f"Final render OK: {result.width}x{result.height}, "
                  f"{result.duration:.1f}s, hwaccel={result.used_hwaccel}, "
                  f"captions={result.captions_burned}")
        done.add("s9")
        self._stage(9, "final MP4 validated")
        done.add("s10")

        self._stage(10, "exporting")
        delivered = exporter.deliver_to_videos(
            out, self.pm.project.name or "U One video")
        self._final_path = out
        self._msg(f"Exported: {out}")
        self._msg(f"Copy in Videos folder: {delivered}")
        # Save the caption edit session so templates/size can be changed
        # after the render without re-rendering the whole video.
        try:
            from .caption_editor import save_edit_session
            session_path = os.path.join(self.work_dir, "caption_edit.json")
            save_edit_session(
                session_path,
                clean_video=result.clean_video_path,
                sentence_timings=self.sentence_timings,
                word_timings=self.word_timings,
                fmt=S.get("output_format", "16:9"),
                res=S.get("output_resolution", "1080p"),
                fps=S.fps,
                template=S.get("caption_template", "karaoke_pop"),
                font_size=S.get("caption_font_size", 48),
                output_video=out)
            self._edit_session_path = session_path
            self._msg("Caption edit session saved.")
        except Exception as e:  # noqa: BLE001 - never fail a good render
            self._msg(f"Note: could not save caption edit session ({e}).")
            self._edit_session_path = ""
        ProjectManager.clear_render_state(self.work_dir)
        self._persist()
        self.rlog.finish(True, out)
        done.add("s11")

    # -- failure path --
    def _on_failure(self, e: Exception, preview: bool):
        if hasattr(e, "describe"):
            self.rlog.write("FAILURE:\n" + e.describe())
        else:
            self.rlog.write(f"FAILURE: {type(e).__name__}: {e}")
        self.rlog.finish(False, type(e).__name__)
        scene_states = [
            {"index": r.spec.index, "scene_id": r.spec.scene_id,
             "segment": r.segment_path,
             "exists": os.path.isfile(r.segment_path),
             "asset_id": r.asset_id, "attempts": r.attempts,
             "recovered": r.recovered,
             "report": (r.report.describe() if r.report else "")}
            for r in self.scene_results]
        snap = self.pm.save_failure_snapshot(
            self.work_dir, e,
            render_log_path=str(self.rlog.path),
            ffmpeg_commands=self.ffmpeg_commands[-20:],
            scene_states=scene_states)
        self._msg(f"Project saved. Failure snapshot: {snap}")
        self._persist()

    def _persist(self):
        try:
            pm = self.pm
            pm.project.timeline = self.timeline
            pm.project.scenes = [
                {"id": s.id, "sentence_index": s.sentence_index,
                 "text": s.text, "locked": s.locked,
                 "sub_index": s.sub_index,
                 "first_sentence": s.first_sentence,
                 "last_sentence": s.last_sentence}
                for s in self.scenes]
            pm.project.warnings = self.warnings
            pm.project.settings = self.settings.to_dict()
            if pm.project.path:
                pm.save()
        except Exception:
            pass

    # -- stages --
    def _prepare_voice(self):
        S = self.settings
        if S.get("voice_upload"):
            src = S.get("voice_upload")
            if not os.path.isfile(src):
                raise ValidationError(f"Voiceover not found: {src}",
                                      stage=STAGES[2])
            self.voice_path = os.path.join(
                self.audio_dir, "voice" + os.path.splitext(src)[1])
            shutil.copy2(src, self.voice_path)
            self._msg("Using uploaded voiceover.")
            self.word_timings = None
        else:
            text = S.get("voice_generate_text") or self.pm.project.script
            self.voice_path = os.path.join(self.audio_dir, "voice.wav")
            if S.get("voice_provider", "edge") == "gemini":
                self._synthesize_gemini_voice(text)
            else:
                _, self.word_timings = synthesize_speech(
                    text, self.voice_path, S.get("voice_name", "en-US-AriaNeural"))
                self._msg("AI voice generated.")
        sents = [s.text for s in self.analysis.sentences]
        self.sentence_timings = get_sentence_timings(
            self.voice_path, sents, self.word_timings)
        # Word animation needs word timings: if the TTS engine did not
        # provide them (uploaded voiceover / pyttsx3 fallback), recover
        # them with Whisper so captions still animate word-by-word
        # instead of rendering as static lines.
        if not self.word_timings:
            self._msg("Recovering word timings for animated captions...")
            self.word_timings = whisper_word_timings(self.voice_path)
        if not self.word_timings:
            # Whisper is optional/unbundled: fall back to proportional
            # estimates so every caption preset still animates instead
            # of degrading to simple static captions.
            from .voiceover import estimate_word_timings as _est_wt
            self.word_timings = _est_wt(self.sentence_timings,
                                       self.voice_path)
            if self.word_timings:
                self._msg(f"Word timings estimated ({len(self.word_timings)} "
                          f"words) -- speech-aligned highlight.")
        self._msg(f"Voice duration: {self.sentence_timings[-1]['end']:.1f}s")

    def _synthesize_gemini_voice(self, text):
        """Google AI Studio TTS voiceover (Uzair 2026-10-04).

        On missing key OR quota exhaustion -> fall back to the default TTS
        engine with a logged warning. Never raises past the render.
        """
        S = self.settings
        key = _api_key(S, "gemini")
        voice = S.get("gemini_voice", "Kore") or "Kore"
        model = S.get("gemini_model") or "gemini-2.5-flash-preview-tts"
        if not key:
            self._msg("WARNING: Google AI Studio selected but no API key "
                      "set -- falling back to the default TTS engine.")
        else:
            try:
                from .tts_gemini import synthesize as _gemini_synth, \
                    TTSQuotaError
                _gemini_synth(text, voice=voice, api_key=key, model=model,
                              out_wav=self.voice_path)
                self.word_timings = None
                self._msg(f"AI voice generated (Google AI Studio, {voice}).")
                return
            except TTSQuotaError as e:
                self._msg(f"WARNING: {e}")
            except Exception as e:  # noqa: BLE001 - any failure -> fallback
                self._msg(f"WARNING: Gemini TTS failed ({e}) -- falling "
                          "back to the default TTS engine.")
        _, self.word_timings = synthesize_speech(
            text, self.voice_path, S.get("voice_name", "en-US-AriaNeural"))
        self._msg("AI voice generated (fallback engine).")

    def _search_fn(self):
        S = self.settings
        finder = MediaFinder(
            self.assets_dir,
            pexels_key=_api_key(S, "pexels"),
            pixabay_key=_api_key(S, "pixabay"),
            google_key=_api_key(S, "google_key"),
            google_cx=_api_key(S, "google_cx"),
            serper_key=_api_key(S, "serper"),
            reddit_id=_api_key(S, "reddit_id"),
            reddit_secret=_api_key(S, "reddit_secret"),
            preference=S.get("media_preference", "ai_auto"),
            repetition=S.get("repetition_limit", "never"),
            safe_mode=S.get("safe_mode", False),
            auto_clean_logos=S.get("auto_clean_logos", True),
            project_id=getattr(self.pm.project, "id", "uone"))
        topic = self.analysis.topic

        def accept(cand, analysis):
            from .media_relevance import is_acceptable, score_candidate
            return is_acceptable(score_candidate(cand, analysis))

        def search(spec: SceneSpec, attempt: int):
            self._check()
            # find the scene object for richer analysis
            sc = next((s for s in self.scenes if s.id == spec.scene_id),
                      None)
            text = sc.text if sc else spec.text
            sa = analyze_sentence(text)
            q1 = build_search_query(sa, topic)
            q2 = " ".join(sa.phrases[:2] + sa.keywords[:4]) or q1
            q3 = topic.replace("_", " ")
            queries = [q1, q2, q3] if attempt == 0 else \
                      [q1 + " alternate", q2, topic]
            try:
                asset = finder.find_for_scene(
                    sc, queries, sa, accept,
                    exclude_ids=())
            except Exception as e:  # noqa: BLE001 - generator recovers
                self.rlog.scene(spec.scene_id + 1,
                                f"search error ({type(e).__name__}); "
                                f"using generated fallback")
                asset = None
            if asset is None:
                asset = finder.generated_fallback(sc, sa)
            return asset

        return search

    def _map_scene_timings(self):
        timings = self.sentence_timings
        groups: Dict[tuple, list] = {}
        for sc in self.scenes:
            groups.setdefault((sc.first_sentence, sc.last_sentence), []).append(sc)
        timed = []
        for (f, l) in sorted(groups):
            s0 = timings[f]["start"] if f < len(timings) else 0.0
            s1 = timings[l]["end"] if l < len(timings) else s0 + 2.0
            members = groups[(f, l)]
            total = sum(len(m.text) for m in members) or 1
            t = s0
            for m in members:
                d = max(0.4, (s1 - s0) * len(m.text) / total)
                timed.append((m, round(t, 3), round(t + d,  3)))
                t += d
        order = {sc.id: i for i, (sc, _, _) in enumerate(timed)}
        timed.sort(key=lambda x: order[x[0].id])
        # Visual lead (Uzair 2026-10-03 rule 7): each visual appears
        # 0.3s BEFORE its matching words are spoken.
        _LEAD = 0.3
        self.scene_timings = [{"scene_id": sc.id,
                               "start": max(0.0, round(a - _LEAD, 3)),
                               "end": b}
                              for sc, a, b in timed]

    def _restore_scene_results(self, state: dict, w: int, h: int):
        self.scene_timings = []  # recomputed below if needed
        self._map_scene_timings()
        results = []
        for meta in state.get("scene_meta", []):
            seg = meta.get("segment", "")
            if not os.path.isfile(seg):
                raise ValidationError(
                    "Resume state references a missing segment; "
                    "re-run without resume.", stage="resume")
            spec = SceneSpec(index=meta["index"], scene_id=meta["scene_id"],
                             text="", duration=1.0)
            from .scene_validator import SegmentReport
            results.append(SceneResult(
                spec=spec, segment_path=seg, asset_id=meta.get("asset_id", "?"),
                attempts=meta.get("attempts", 1),
                recovered=meta.get("recovered", False),
                report=SegmentReport(path=seg, valid=True)))
        results.sort(key=lambda r: r.spec.index)
        self.scene_results = results
        self._msg(f"Resumed: {len(results)} previously rendered segments kept.")

    def _scene_word_timings(self, scene_start, scene_end):
        """Word timings converted to scene-relative seconds for the
        Genspark main-text engine (appear 100-300ms BEFORE the hero word).

        voice-absolute == video-absolute everywhere in this engine, so
        subtracting the scene start is exact. Returns [] when no timings
        are available (scene cut alignment is used instead).
        """
        out = []
        for w in self.word_timings or []:
            ws = w.get("start")
            if ws is None:
                continue
            if scene_start - 0.25 <= ws <= scene_end:
                out.append({"word": w.get("word", ""),
                            "start": max(0.0, ws - scene_start),
                            "end": max(0.0, (w.get("end") or ws) - scene_start)})
        return out

    def _build_timeline(self, preview: bool):
        S = self.settings
        tl = Timeline()
        total = self.scene_timings[-1]["end"] if self.scene_timings else 1.0
        tl.add("voice", 0, total, kind="audio", label="Voiceover",
               payload={"path": self.voice_path})
        for i, r in enumerate(self.scene_results):
            t = self.scene_timings[i]
            sc = next((s for s in self.scenes if s.id == r.spec.scene_id), None)
            tl.add("video", t["start"], t["end"], kind="video",
                   label=f"Scene {r.spec.scene_id + 1}: {r.asset_id}",
                   payload={"asset_id": r.asset_id,
                            "segment": r.segment_path,
                            "scene_id": r.spec.scene_id},
                   locked=sc.locked if sc else False)
            if S.get("text_overlays", "auto") == "auto" and sc:
                sa = analyze_sentence(sc.text)
                dur = max(1.0, t["end"] - t["start"])
                recent = self._mt_context.setdefault("recent", [])
                cards = select_main_text(
                    sc.text, duration=dur,
                    word_timings=self._scene_word_timings(t["start"], t["end"]),
                    recent_texts=recent,
                    keywords=sa.keywords, phrases=sa.phrases,
                    context=self._mt_context)
                recent.extend(c["text"] for c in cards)
                del recent[:-8]
                if cards:
                    hl = headline_preset_for(
                        sc.text, S.get("headline_style", "auto"))
                    box_c = story_box_color(sc.text)
                    for card in cards:
                        tl.add("text", t["start"] + card["appear"],
                               t["start"] + card["appear"] + card["hold"],
                               kind="text", label=card["text"],
                               payload={"scene_id": sc.id,
                                         "style": hl,
                                         "accent": card["accent"],
                                         "accent_words": card["accent_words"],
                                         "hl_type": card["type"],
                                         "enter_ms": card["enter_ms"],
                                         "exit_ms": card["exit_ms"],
                                         "entrance": card["entrance"],
                                         "box_c": box_c},
                               locked=sc.locked)
        self.timeline = tl

    def _build_captions(self, w: int, h: int, preview: bool):
        S = self.settings
        if not S.get("captions_enabled", True):
            return
        srt = os.path.join(self.audio_dir, "captions.srt")
        ass = os.path.join(self.audio_dir, "captions.ass")
        overlays = [{"text": c.label, "start": c.start, "end": c.end,
                     "style": c.payload.get("style"),
                     "accent": c.payload.get("accent"),
                     "accent_words": c.payload.get("accent_words", []),
                     "hl_type": c.payload.get("hl_type", "STAT"),
                     "enter_ms": c.payload.get("enter_ms", 200),
                     "exit_ms": c.payload.get("exit_ms", 150),
                     "entrance": c.payload.get("entrance", "fade"),
                     "box_c": c.payload.get("box_c")}
                    for c in self.timeline.by_track("text")]
        build_srt(self.sentence_timings, srt)
        build_ass(self.sentence_timings, self.word_timings, ass,
                  font_size=S.get("caption_font_size", 48),
                  highlight=S.get("caption_highlight", True),
                  overlays=overlays, play_res=(w, h),
                  template=S.get("caption_template"))
        self.timeline.add("captions", 0, self.timeline.duration(),
                          kind="caption", label="Captions",
                          payload={"srt": srt, "ass": ass})

    def _build_audio(self):
        S = self.settings
        tl = self.timeline
        sfx_lib = ensure_sfx_library(os.path.join(self.assets_dir, "sfx"))
        self.sfx_library = sfx_lib
        sas = [analyze_sentence(sc.text) for sc in self.scenes]
        # Seed from the content so different videos get different SFX
        # variety, while the same project stays reproducible.
        seed = abs(hash(" ".join(sc.text for sc in self.scenes))) % (2 ** 31)
        # Visual-first SFX: clip metadata (tags + query) per scene so the
        # SFX follows what is ON SCREEN, not the narration alone.
        vtags = []
        for sc in self.scenes:
            a = (self.assets or {}).get(sc.id)
            if a is None:
                vtags.append("")
            else:
                tags = a.get("tags", "") if isinstance(a, dict) \
                    else getattr(a, "tags", "")
                query = a.get("query", "") if isinstance(a, dict) \
                    else getattr(a, "query", "")
                vtags.append(f"{tags} {query}".strip())
        # Universal Semantic SFX Engine: decides per scene whether a sound
        # is needed from script + visual content + visual action + mood.
        # Falls back to the legacy keyword placer if it cannot run.
        try:
            from .semantic_sfx import (build_scene_contexts,
                                       plan_video_sfx)
            _sctx = build_scene_contexts(
                self.scenes, sas, self.scene_timings, vtags,
                video_mood=getattr(self.analysis, "mood", "neutral"))
            _decisions, events = plan_video_sfx(_sctx, seed=seed)
            self.sfx_decisions = _decisions
        except Exception as e:  # noqa: BLE001 - legacy path is the fallback
            self._msg(f"Semantic SFX unavailable ({e}); using legacy placer.")
            events = place_sfx(sas, self.scene_timings, seed=seed,
                               visual_tags=vtags)
        # CapCut-style: a subtle sound under each animated caption chunk,
        # matched to its entrance animation (pop/tick/swoosh/punch).
        if S.get("captions_enabled", True):
            tmpl = S.get("caption_template")
            if isinstance(tmpl, str):
                from .text_captions import CAPTION_TEMPLATES
                tmpl = CAPTION_TEMPLATES.get(tmpl, {})
            events += place_caption_sfx(self.sentence_timings, tmpl or {})
            events.sort(key=lambda e: e["time"])
        for ev in events:
            lib = sfx_lib[ev["sfx"]]
            d = ev.get("duration") or (audio_duration(lib) or 1.0)
            tl.add("sfx", ev["time"], ev["time"] + d, kind="sfx",
                   label=f"SFX: {ev['sfx']}",
                   payload={"path": lib, **ev})
        total_dur = tl.duration()
        music_path = select_music(S.get("music_dir", ""), self.analysis.mood)
        if music_path:
            self._msg(f"Music: {os.path.basename(music_path)}")
        else:
            music_path = os.path.join(self.audio_dir, "ambient_bed.wav")
            synth_ambient_bed(total_dur + 1, music_path, self.analysis.mood)
            self._msg("Music: generated ambient bed (no music folder set)")
        tl.add("music", 0, total_dur, kind="music", label="Background music",
               payload={"path": music_path})
        mixed = os.path.join(self.audio_dir, "mixed.m4a")
        sfx_ev = [{"time": c.payload["time"], "sfx": c.payload["sfx"],
                   "gain_db": c.payload.get("gain_db", -10),
                   "duration": c.payload.get("duration"),
                   "fade_in": c.payload.get("fade_in"),
                   "fade_out": c.payload.get("fade_out")}
                  for c in tl.by_track("sfx")]
        mix_audio(self.voice_path, music_path, sfx_ev, sfx_lib,
                  mixed, total_dur)
        self.mixed_path = mixed
        self._msg(f"Audio mixed: {mixed}")

    def _concat(self, seg_paths: List[str], out_path: str):
        if len(seg_paths) == 1:
            shutil.copy2(seg_paths[0], out_path)
            return
        lst = self._tmp.temp_path("concat_list", ".txt")
        # Defensive: make sure the temp dir still exists (never assume).
        os.makedirs(os.path.dirname(lst), exist_ok=True)
        with open(lst, "w", encoding="utf-8") as f:
            for p in seg_paths:
                f.write(f"file '{_concat_list_escape(str(Path(p).resolve()))}'\n")
        self.ffmpeg_commands.append(f"concat demuxer ({len(seg_paths)} files)")
        self.engine.run(["-f", "concat", "-safe", "0", "-i", lst,
                         "-c", "copy", out_path],
                        stage="concat", inputs=seg_paths, output=out_path,
                        timeout=1200)
        if not os.path.isfile(out_path):
            raise UOneError("Concatenated video was not created.",
                            stage="concat",
                            recovery_hint="see render log")
        self._msg(f"Concatenated {len(seg_paths)} segments.")

    # -- per-scene regeneration (GUI buttons; old Pipeline API preserved) --
    def _finder(self) -> MediaFinder:
        S = self.settings
        return MediaFinder(
            self.assets_dir,
            pexels_key=_api_key(S, "pexels"),
            pixabay_key=_api_key(S, "pixabay"),
            google_key=_api_key(S, "google_key"),
            google_cx=_api_key(S, "google_cx"),
            serper_key=_api_key(S, "serper"),
            reddit_id=_api_key(S, "reddit_id"),
            reddit_secret=_api_key(S, "reddit_secret"),
            preference=S.get("media_preference", "ai_auto"),
            repetition=S.get("repetition_limit", "never"),
            safe_mode=S.get("safe_mode", False),
            auto_clean_logos=S.get("auto_clean_logos", True),
            project_id=getattr(self.pm.project, "id", "uone"))

    def _scene(self, scene_id):
        return next((s for s in self.scenes if s.id == scene_id), None)

    def regenerate_visual(self, scene_id, log_cb=None):
        """Find a new visual for one scene, re-render, re-validate,
        re-normalize and swap it in. Returns the new Asset (None if locked)."""
        from .media_relevance import is_acceptable, score_candidate
        sc = self._scene(scene_id)
        if not sc or getattr(sc, "locked", False):
            return None
        res = next((r for r in self.scene_results
                    if r.spec.scene_id == scene_id), None)
        if res is None:
            return None
        sa = analyze_sentence(sc.text)
        q1 = build_search_query(sa, self.analysis.topic) + " alternate"
        finder = self._finder()
        cur = self.assets.get(scene_id)
        exclude = (cur.id,) if cur and getattr(cur, "id", None) else ()
        asset = finder.find_for_scene(
            sc, [q1], sa,
            lambda c, a: is_acceptable(score_candidate(c, a)),
            exclude_ids=exclude)
        if asset is None:
            asset = finder.generated_fallback(sc, sa)
        t = next((x for x in self.scene_timings
                  if x["scene_id"] == scene_id), None)
        dur = max(0.6, t["end"] - t["start"]) if t else res.spec.duration
        gen = SceneGenerator(
            seg_dir=self.seg_dir, assets_dir=self.assets_dir,
            width=self.out_w, height=self.out_h, fps=self.settings.fps,
            search_fn=lambda spec, attempt: asset,
            render_log=self.rlog, cancel_event=self.cancel_event,
            cache=None)
        new_res = gen.generate_one(
            SceneSpec(index=res.spec.index, scene_id=scene_id,
                      text=sc.text, duration=dur,
                      keyword_insert=getattr(res.spec, "keyword_insert",
                                            None)))
        tspec = hw_target_spec(width=self.out_w, height=self.out_h,
                               fps=float(self.settings.fps),
                               engine=self.engine)
        norm = normalize_segment(
            new_res.segment_path, tspec,
            os.path.join(self.seg_dir, f"seg_{res.spec.index:03d}_r.mp4"),
            self.engine)
        self.scene_results[res.spec.index] = new_res
        if res.spec.index < len(self.normalized):
            self.normalized[res.spec.index] = norm
        self.assets[scene_id] = asset
        for c in self.timeline.by_track("video"):
            if c.payload.get("scene_id") == scene_id and not c.locked:
                c.payload["segment"] = norm
                c.label = f"Scene {scene_id + 1}: {asset.id}"
        if log_cb:
            log_cb(f"Scene {scene_id + 1} visual regenerated ({asset.id})")
        return asset

    def regenerate_text(self, scene_id):
        sc = self._scene(scene_id)
        if not sc or getattr(sc, "locked", False) or not self.timeline:
            return
        st = next((t for t in self.scene_timings
                   if t["scene_id"] == scene_id), None)
        if not st:
            return
        sa = analyze_sentence(sc.text)
        dur = max(1.0, st["end"] - st["start"])
        recent = self._mt_context.setdefault("recent", [])
        cards = select_main_text(
            sc.text, duration=dur,
            word_timings=self._scene_word_timings(st["start"], st["end"]),
            recent_texts=recent,
            keywords=sa.keywords, phrases=sa.phrases,
            context=self._mt_context)
        hl = headline_preset_for(
            sc.text, self.settings.get("headline_style", "auto"))
        box_c = story_box_color(sc.text)
        tl = self.timeline
        for c in [c for c in tl.by_track("text")
                  if c.payload.get("scene_id") == scene_id and not c.locked]:
            tl.remove(c.id)
        for card in cards:
            tl.add("text", st["start"] + card["appear"],
                   st["start"] + card["appear"] + card["hold"],
                   kind="text", label=card["text"],
                   payload={"scene_id": sc.id,
                             "style": hl,
                             "accent": card["accent"],
                             "accent_words": card["accent_words"],
                             "hl_type": card["type"],
                             "enter_ms": card["enter_ms"],
                             "exit_ms": card["exit_ms"],
                             "entrance": card["entrance"],
                             "box_c": box_c},
                   locked=sc.locked)

    def regenerate_sfx(self, scene_id):
        sc = self._scene(scene_id)
        if not sc or getattr(sc, "locked", False) or not self.timeline:
            return
        st = next((t for t in self.scene_timings
                   if t["scene_id"] == scene_id), None)
        tl = self.timeline
        for c in [c for c in tl.by_track("sfx")
                  if st and st["start"] <= c.start <= st["end"]
                  and not c.locked]:
            tl.remove(c.id)
        order = list(self.sfx_library) or ["whoosh"]
        pick = order[(scene_id + 1) % len(order)]
        if st and pick in self.sfx_library:
            at = st["start"] + 0.4
            d = audio_duration(self.sfx_library[pick]) or 1.0
            tl.add("sfx", at, at + d, kind="sfx", label=f"SFX: {pick}",
                   payload={"path": self.sfx_library[pick], "time": at,
                            "sfx": pick, "gain_db": -10})

    def regenerate_music(self, mood=None):
        tl = self.timeline
        if not tl:
            return
        mood = mood or (self.analysis.mood if self.analysis else "neutral")
        for c in [c for c in tl.by_track("music") if not c.locked]:
            tl.remove(c.id)
        music_path = select_music(self.settings.get("music_dir", ""), mood)
        if not music_path:
            music_path = os.path.join(self.audio_dir, "ambient_bed.wav")
            synth_ambient_bed(tl.duration() + 1, music_path, mood)
        tl.add("music", 0, tl.duration(), kind="music",
               label="Background music", payload={"path": music_path})
