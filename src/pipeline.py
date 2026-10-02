"""U One - CREATE VIDEO orchestrator.

12 stages, progress callbacks, cancel, per-scene regenerate.

Reliability design (requirements 4/5/22):
  * every scene segment renders in isolation - one bad asset can never
    kill the whole video; failures log, fall back and continue;
  * successfully rendered segments are cached by content hash, so a
    re-render or a resume skips scenes that already succeeded;
  * errors are structured (stage, scene, ffmpeg exit code) and logged;
    the GUI keeps the app open, shows a simple message and saves the
    project + log.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.script_analyzer import analyze_script, analyze_sentence, build_search_query
from src.scene_detector import detect_scenes
from src.voice import synthesize_speech, get_sentence_timings
from src.media_search import MediaFinder, Asset
from src.media_relevance import score_candidate, is_acceptable
from src.timeline import Timeline
from src.text_captions import make_overlay_text, build_srt, build_ass
from src.audio_design import (ensure_sfx_library, place_sfx, select_music,
                              synth_ambient_bed, mix_audio)
from src.renderer import (check_ffmpeg, make_segment, concat_segments,
                          render_final, quality_check, RESOLUTIONS)
from src.ffmpeg_util import FFmpegRunError, run_ffmpeg, ffprobe_duration
from src import asset_cache
from src import secure_store
from src import app_log

STAGES = [
    "Analyzing script", "Splitting scenes", "Preparing voiceover",
    "Finding visuals", "Checking visual relevance", "Synchronizing voice",
    "Creating text", "Generating captions", "Adding sound effects",
    "Adding music", "Mixing audio", "Quality checking & rendering",
]


class Cancelled(Exception):
    pass


def _api_key(settings, service):
    """Resolve a stock-media key: env var -> secure OS storage -> settings
    (UI) -> config.yaml api_keys section. The value is never logged."""
    env = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY",
           "google_key": "GOOGLE_API_KEY", "google_cx": "GOOGLE_CX",
           "serper": "SERPER_API_KEY",
           "reddit_id": "REDDIT_CLIENT_ID",
           "reddit_secret": "REDDIT_CLIENT_SECRET"}[service]
    if os.environ.get(env):
        return os.environ[env]
    stored = secure_store.get_key(service)
    if stored:
        return stored
    if settings.get(service + "_key"):
        return settings[service + "_key"]
    nested = settings.get("api_keys", {}) or {}
    return nested.get(service, "")


class Pipeline:
    def __init__(self, project, work_dir, settings):
        self.project = project
        self.work_dir = work_dir
        self.settings = settings
        self.cancel_event = threading.Event()
        self.assets_dir = os.path.join(work_dir, "assets")
        self.seg_dir = os.path.join(work_dir, "segments")
        self.audio_dir = os.path.join(work_dir, "audio")
        for d in (self.assets_dir, self.seg_dir, self.audio_dir):
            os.makedirs(d, exist_ok=True)
        self.pid = getattr(project, "id", None) or uuid.uuid4().hex[:8]
        self.analysis = None
        self.scenes = []
        self.scene_timings = []
        self.sentence_timings = []
        self.assets = {}
        self.voice_path = ""
        self.word_timings = None
        self.sfx_library = {}
        self.warnings = []
        self._lock = threading.Lock()

    # ---- helpers ----
    def _check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def _stage(self, i, msg="", progress_cb=None):
        if progress_cb:
            progress_cb(i, STAGES[i], msg)

    def _log(self, msg, log_cb=None, scene=None, level=logging.INFO,
             error_type="", retry="", exit_code=None):
        app_log.log(self.pid, "pipeline", msg, scene=scene, level=level,
                    error_type=error_type, retry=retry, exit_code=exit_code)
        if log_cb:
            log_cb(msg)

    # ---- scene timing mapping ----
    def _map_scene_timings(self):
        timings = self.sentence_timings
        groups = {}
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
                timed.append((m, round(t, 3), round(t + d, 3)))
                t += d
        order = {sc.id: i for i, (sc, _, _) in enumerate(timed)}
        timed.sort(key=lambda x: order[x[0].id])
        self.scene_timings = [{"scene_id": sc.id, "start": a, "end": b}
                              for sc, a, b in timed]
        return timed

    # ---- main run ----
    def run(self, preview=False, progress_cb=None, log_cb=None):
        S = self.settings
        started = time.time()
        try:
            if not check_ffmpeg():
                raise RuntimeError(
                    "FFmpeg was not found, so the video cannot be rendered.\n\n"
                    "U One normally bundles FFmpeg with its installer. "
                    "Please reinstall U One, or install FFmpeg and restart the app.")

            self._stage(0, progress_cb=progress_cb)
            self.analysis = analyze_script(self.project.script)
            if not self.analysis.sentences:
                raise ValueError("Script is empty.")
            self._log(f"Topic: {self.analysis.topic} | Mood: {self.analysis.mood} | "
                      f"Sentences: {len(self.analysis.sentences)}", log_cb)

            self._stage(1, progress_cb=progress_cb)
            self._check()
            sent_texts = [s.text for s in self.analysis.sentences]
            self.scenes = detect_scenes(sent_texts, S.get("visual_mode", "every_sentence"))

            self._stage(2, progress_cb=progress_cb)
            self._prepare_voice(progress_cb, log_cb)

            self._stage(3, progress_cb=progress_cb)
            self._find_visuals(progress_cb, log_cb)

            self._stage(4, "relevance enforced during search", progress_cb=progress_cb)

            self._stage(5, progress_cb=progress_cb)
            scene_parts = self._map_scene_timings()

            self._stage(6, progress_cb=progress_cb)
            tl = self._build_timeline(scene_parts, S, log_cb)

            self._stage(7, progress_cb=progress_cb)
            fmt = S.get("output_format", "16:9")
            res = S.get("output_resolution", "1080p")
            w, h = RESOLUTIONS[fmt]["1080p"] if preview else RESOLUTIONS[fmt][res]
            overlays = []
            if S.get("captions_enabled", True):
                srt = os.path.join(self.audio_dir, "captions.srt")
                ass = os.path.join(self.audio_dir, "captions.ass")
                overlays = [{"text": c.label, "start": c.start, "end": c.end}
                            for c in tl.by_track("text")]
                build_srt(self.sentence_timings, srt)
                build_ass(self.sentence_timings, self.word_timings, ass,
                          font_size=S.get("caption_font_size", 48),
                          highlight=S.get("caption_highlight", True),
                          overlays=overlays, play_res=(w, h),
                          template=S.get("caption_template"))
                tl.add("captions", 0, tl.duration(), kind="caption",
                       label="Captions", payload={"srt": srt, "ass": ass})

            self._stage(8, progress_cb=progress_cb)
            self.sfx_library = ensure_sfx_library(os.path.join(self.assets_dir, "sfx"))
            sas = [analyze_sentence(sc.text) for sc in self.scenes]
            # Visual-first SFX: clip metadata (tags + query) per scene so
            # the SFX follows what is ON SCREEN, not the narration alone.
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
            events = place_sfx(sas, self.scene_timings, visual_tags=vtags)
            for ev in events:
                lib = self.sfx_library[ev["sfx"]]
                from src.voice import audio_duration as _ad
                d = _ad(lib) or 1.0
                tl.add("sfx", ev["time"], ev["time"] + d, kind="sfx",
                       label=f"SFX: {ev['sfx']}", payload={"path": lib, **ev})

            self._stage(9, progress_cb=progress_cb)
            total_dur = tl.duration()
            music_path = select_music(S.get("music_dir", ""), self.analysis.mood)
            if music_path:
                self._log(f"Music: {os.path.basename(music_path)}", log_cb)
            else:
                music_path = os.path.join(self.audio_dir, "ambient_bed.wav")
                synth_ambient_bed(total_dur + 1, music_path, self.analysis.mood)
                self._log("Music: generated ambient bed (no music folder set)", log_cb)
            tl.add("music", 0, total_dur, kind="music", label="Background music",
                   payload={"path": music_path})

            self._stage(10, progress_cb=progress_cb)
            mixed = os.path.join(self.audio_dir, "mixed.m4a")
            sfx_ev = [{"time": c.payload["time"], "sfx": c.payload["sfx"],
                       "gain_db": c.payload.get("gain_db", -10)}
                      for c in tl.by_track("sfx")]
            mix_audio(self.voice_path, music_path, sfx_ev, self.sfx_library,
                      mixed, total_dur)

            self._stage(11, "rendering", progress_cb=progress_cb)
            fps = int(S.get("output_fps", 30))
            codec = S.get("output_codec", "h264")

            seg_paths = self._render_segments(tl, w, h, fps, progress_cb, log_cb)

            concat = os.path.join(self.work_dir, "concat.mp4")
            self._concat_safe(seg_paths, concat, log_cb)

            srt_p = os.path.join(self.audio_dir, "captions.srt")
            self.warnings = quality_check(
                seg_paths, mixed, timeline=tl, voice_path=self.voice_path,
                srt_path=srt_p if os.path.exists(srt_p) else None)
            for wr in self.warnings:
                self._log("QC warning: " + wr, log_cb)

            ass_path = None
            caps = tl.by_track("captions")
            if caps:
                ass_path = caps[0].payload.get("ass")
            name = "preview.mp4" if preview else "final.mp4"
            out = os.path.join(self.work_dir, name)
            try:
                render_final(concat, mixed, ass_path or "", out, fmt=fmt,
                             res=res, fps=fps, codec=codec, preview=preview,
                             work_dir=self.work_dir)
            except FFmpegRunError as e:
                self._log(f"Final render failed: {e}", log_cb,
                          level=logging.ERROR, error_type="ffmpeg",
                          exit_code=e.exit_code)
                raise RuntimeError(
                    "U One could not finish the final video file.\n"
                    "Your project has been saved - you can try rendering again.\n"
                    "Technical details were written to the log.")

            self._persist(tl, S)
            elapsed = time.time() - started
            self._log(f"Done in {elapsed:.0f}s -> {out}", log_cb)
            return out
        except Cancelled:
            self._log("Render cancelled by user.", log_cb)
            raise
        except Exception:
            # never lose the project on a render failure (crash recovery)
            try:
                self._persist(getattr(self.project, "timeline", None) or Timeline(), S)
            except Exception:  # noqa: BLE001
                pass
            raise

    # ---- isolated, cached segment rendering ----
    def _render_segments(self, tl, w, h, fps, progress_cb, log_cb):
        seg_paths = []
        clips = tl.by_track("video")
        for i, c in enumerate(clips):
            self._check()
            asset = Asset.from_dict(c.payload["asset"])
            dur = max(0.5, c.end - c.start)
            seg = os.path.join(self.seg_dir, f"seg_{i:03d}.mp4")
            cache_key = asset_cache.segment_cache_key(
                asset.id, asset.local_path, dur, w, h, fps, str(i))
            cached = os.path.join(self.seg_dir, f"cached_{cache_key[:16]}.mp4")
            reused = False
            if os.path.isfile(cached) and os.path.getsize(cached) > 1024:
                d = ffprobe_duration(cached)
                if d and abs(d - dur) < 0.4:
                    shutil.copy(cached, seg)
                    reused = True
            if reused:
                self._log(f"Scene {i+1}: reused cached segment.", log_cb, scene=i + 1)
            else:
                try:
                    make_segment(asset, dur, seg, w, h, fps, seed=i, scene=i + 1)
                    shutil.copy(seg, cached)
                except FFmpegRunError as e:
                    self._log(f"Scene {i+1}: segment failed ({e}). "
                              f"Trying fallback visual.", log_cb, scene=i + 1,
                              level=logging.WARNING, error_type="ffmpeg",
                              retry="fallback_asset", )
                    seg = self._scene_fallback(i, dur, w, h, fps, log_cb)
                except Exception as e:  # noqa: BLE001 - never kill the render
                    self._log(f"Scene {i+1}: unexpected error ({type(e).__name__}). "
                              f"Using fallback visual.", log_cb, scene=i + 1,
                              level=logging.WARNING, error_type=type(e).__name__,
                              retry="fallback_asset")
                    seg = self._scene_fallback(i, dur, w, h, fps, log_cb)
            seg_paths.append(seg)
            if progress_cb:
                progress_cb(11, STAGES[11], f"segment {i+1}/{len(clips)}")
        return seg_paths

    def _scene_fallback(self, idx, dur, w, h, fps, log_cb):
        """Last-resort scene visual: solid branded card. Always succeeds."""
        seg = os.path.join(self.seg_dir, f"seg_{idx:03d}.mp4")
        try:
            run_ffmpeg(
                ["-f", "lavfi", "-i",
                 f"color=c=0x0b1020:s={w}x{h}:r={fps}:d={dur:.2f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-an", seg],
                stage="scene_fallback", scene=idx + 1)
        except Exception as e:  # noqa: BLE001
            self._log(f"Scene {idx+1}: fallback also failed ({e}).",
                      log_cb, scene=idx + 1, level=logging.ERROR)
            raise RuntimeError(
                f"Scene {idx+1} could not be rendered and recovery failed. "
                f"Details are in the log.")
        return seg

    def _concat_safe(self, seg_paths, concat, log_cb):
        try:
            concat_segments(seg_paths, concat, work_dir=self.work_dir)
        except FFmpegRunError as e:
            self._log(f"Concat with transitions failed ({e}); "
                      f"retrying plain concat.", log_cb, level=logging.WARNING,
                      error_type="ffmpeg", retry="plain_concat")
            from src.renderer import _concat_list_escape
            from pathlib import Path as _P
            lst = os.path.join(self.work_dir, "concat_list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for p in seg_paths:
                    f.write(f"file '{_concat_list_escape(str(_P(p).resolve()))}'\n")
            try:
                run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", concat], stage="concat_retry")
            except FFmpegRunError as e2:
                raise RuntimeError(
                    "U One could not join the scenes together.\n"
                    "Your project has been saved - details are in the log.") from e2

    def _persist(self, tl, S):
        self.project.timeline = tl
        self.project.scenes = [{"id": s.id, "sentence_index": s.sentence_index,
                                "text": s.text, "locked": s.locked,
                                "sub_index": s.sub_index,
                                "first_sentence": s.first_sentence,
                                "last_sentence": s.last_sentence} for s in self.scenes]
        self.project.assets = {str(k): v.to_dict() for k, v in self.assets.items()}
        self.project.warnings = self.warnings
        # never persist raw API keys into project files
        clean = {k: v for k, v in dict(S).items()
                 if "key" not in k.lower()}
        self.project.settings = clean

    # ---- stages ----
    def _prepare_voice(self, progress_cb, log_cb):
        S = self.settings
        if S.get("voice_upload"):
            src = S["voice_upload"]
            if not os.path.exists(src):
                raise FileNotFoundError(f"Voiceover not found: {src}")
            self.voice_path = os.path.join(
                self.audio_dir, "voice" + os.path.splitext(src)[1])
            shutil.copy(src, self.voice_path)
            self._log("Using uploaded voiceover.", log_cb)
            self.word_timings = None
        else:
            text = S.get("voice_generate_text") or self.project.script
            self.voice_path = os.path.join(self.audio_dir, "voice.wav")
            _, self.word_timings = synthesize_speech(
                text, self.voice_path, S.get("voice_name", "en-US-AriaNeural"))
            self._log("AI voice generated.", log_cb)
        sents = [s.text for s in self.analysis.sentences]
        self.sentence_timings = get_sentence_timings(
            self.voice_path, sents, self.word_timings)
        self._log(f"Voice duration: {self.sentence_timings[-1]['end']:.1f}s", log_cb)

    def _find_visuals(self, progress_cb, log_cb):
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
            project_id=self.pid)

        def accept(cand, analysis):
            return is_acceptable(score_candidate(cand, analysis))

        emergency = None
        try:
            emg_path = finder.graphics.title_card(
                self.project.title or "Video", "emergency")
            if os.path.exists(emg_path):
                emergency = Asset(id="emergency", kind="graphic",
                                  local_path=emg_path, source="Generated",
                                  license="Created by U One",
                                  width=1280, height=720,
                                  query="(emergency fallback)")
        except Exception:  # noqa: BLE001
            emergency = None

        def search_one(sc):
            self._check()
            sa = analyze_sentence(sc.text)
            q1 = build_search_query(sa, self.analysis.topic)
            q2 = " ".join(sa.phrases[:2] + sa.keywords[:4]) or q1
            q3 = self.analysis.topic.replace("_", " ")
            try:
                asset = finder.find_for_scene(sc, [q1, q2, q3], sa, accept)
            except Exception as e:  # noqa: BLE001
                self._log(f"Scene {sc.id+1}: search error ({type(e).__name__}); "
                          f"using fallback card.", log_cb, scene=sc.id + 1,
                          level=logging.WARNING)
                asset = emergency or finder.generated_fallback(sc, sa)
            if asset is None:
                asset = emergency or finder.generated_fallback(sc, sa)
            with self._lock:
                self.assets[sc.id] = asset
            return sc, asset

        # scenes searched in parallel (providers already searched concurrently)
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(search_one, sc): sc for sc in self.scenes}
            done = 0
            for fut in as_completed(futs):
                self._check()
                sc, asset = fut.result()
                done += 1
                self._log(f"Scene {sc.id+1}: [{asset.source}] "
                          f"{(asset.query or '')[:60]}", log_cb, scene=sc.id + 1)
                if progress_cb:
                    progress_cb(3, STAGES[3], f"scene {done}/{len(self.scenes)}")

    def _build_timeline(self, scene_parts, S, log_cb):
        tl = Timeline()
        total = scene_parts[-1][2] if scene_parts else 1.0
        tl.add("voice", 0, total, kind="audio", label="Voiceover",
               payload={"path": self.voice_path})
        hook_fast = S.get("style_preset", "Documentary") in ("Fast-paced", "News")
        for idx, (sc, a, b) in enumerate(scene_parts):
            asset = self.assets[sc.id]
            dur = max(0.6, b - a)
            if hook_fast and idx == 0 and dur > 4:
                mid = a + dur / 2
                tl.add("video", a, mid, kind=asset.kind,
                       label=f"Scene {sc.id+1} (hook A)",
                       payload={"asset": asset.to_dict(), "scene_id": sc.id},
                       locked=sc.locked)
                tl.add("video", mid, b, kind=asset.kind,
                       label=f"Scene {sc.id+1} (hook B)",
                       payload={"asset": asset.to_dict(), "scene_id": sc.id},
                       locked=sc.locked)
            else:
                tl.add("video", a, b, kind=asset.kind,
                       label=f"Scene {sc.id+1}: {asset.source}",
                       payload={"asset": asset.to_dict(), "scene_id": sc.id},
                       locked=sc.locked)
            if S.get("text_overlays", "auto") == "auto":
                sa = analyze_sentence(sc.text)
                ov = make_overlay_text(sa)
                if ov:
                    tl.add("text", a + 0.3, min(b, a + 3.2), kind="text",
                           label=ov, payload={"scene_id": sc.id},
                           locked=sc.locked)
        return tl

    # ---- per-scene regenerate (locked scenes are never touched) ----
    def _finder(self):
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
            project_id=self.pid)

    def regenerate_visual(self, scene_id, log_cb=None):
        sc = next((s for s in self.scenes if s.id == scene_id), None)
        if not sc or sc.locked:
            return None
        sa = analyze_sentence(sc.text)
        q1 = build_search_query(sa, self.analysis.topic) + " alternate"
        finder = self._finder()
        cur = self.assets.get(scene_id)
        exclude = (cur.id,) if cur else ()
        asset = finder.find_for_scene(
            sc, [q1], sa,
            lambda c, a: is_acceptable(score_candidate(c, a)),
            exclude_ids=exclude)
        self.assets[scene_id] = asset
        for c in (self.project.timeline.by_track("video")
                  if self.project.timeline else []):
            if c.payload.get("scene_id") == scene_id and not c.locked:
                c.payload["asset"] = asset.to_dict()
                c.label = f"Scene {scene_id+1}: {asset.source}"
        return asset

    def regenerate_text(self, scene_id):
        sc = next((s for s in self.scenes if s.id == scene_id), None)
        if not sc or sc.locked or not self.project.timeline:
            return
        sa = analyze_sentence(sc.text)
        ov = make_overlay_text(sa)
        for c in self.project.timeline.by_track("text"):
            if c.payload.get("scene_id") == scene_id and not c.locked:
                c.label = ov

    def regenerate_sfx(self, scene_id):
        if not self.project.timeline:
            return
        sc = next((s for s in self.scenes if s.id == scene_id), None)
        if not sc or sc.locked:
            return
        st = next((t for t in self.scene_timings if t["scene_id"] == scene_id), None)
        tl = self.project.timeline
        for c in [c for c in tl.by_track("sfx")
                  if st and st["start"] <= c.start <= st["end"] and not c.locked]:
            tl.remove(c.id)
        order = list(self.sfx_library) or ["whoosh"]
        pick = order[(scene_id + 1) % len(order)]
        if st:
            at = st["start"] + 0.4
            from src.voice import audio_duration as _ad
            d = _ad(self.sfx_library[pick]) or 1.0
            tl.add("sfx", at, at + d, kind="sfx", label=f"SFX: {pick}",
                   payload={"path": self.sfx_library[pick], "time": at,
                            "sfx": pick, "gain_db": -10})

    def regenerate_music(self, mood=None):
        if not self.project.timeline:
            return
        tl = self.project.timeline
        mood = mood or (self.analysis.mood if self.analysis else "neutral")
        for c in [c for c in tl.by_track("music") if not c.locked]:
            tl.remove(c.id)
        music_path = select_music(self.settings.get("music_dir", ""), mood)
        if not music_path:
            music_path = os.path.join(self.audio_dir, "ambient_bed.wav")
            synth_ambient_bed(tl.duration() + 1, music_path, mood)
        tl.add("music", 0, tl.duration(), kind="music",
               label="Background music", payload={"path": music_path})
