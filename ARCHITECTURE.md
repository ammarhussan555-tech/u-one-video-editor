# U One v2 — Clean Architecture Blueprint

Rebuild of "U One — AI Automatic Video Editor" addressing the 31-scene
final-render failure. Existing proven logic (script analysis, timeline,
ASS captions, audio design — 23/23 Linux tests PASS) is **reorganized,
not retyped**; genuinely new code covers validation, recovery,
normalization, engine robustness, and observability.

## Root cause of the reported failure (ranked hypotheses)

The reported failure: 31 scenes, 13 flagged black/blank, then
`final_render_no_captions` failed with exit code `2880417800`
(0xABAFB008 — not a standard FFmpeg or Windows failure code).

1. **Render inputs were never validated.** `render_final()` received
   `concat.mp4` + `mixed.m4a` blindly. If either was corrupt/empty
   (e.g. concat demuxer `-c copy` over 31 heterogeneous segments, some
   black/blank), the no-captions render fails exactly as observed —
   three times in a row (captions attempt → safe-copy retry →
   no-captions), which points at the *inputs*, not the subtitles filter.
2. **Black scenes passed straight into the render.** QC ran *after*
   segment rendering and its warnings were non-blocking; nothing
   rejected or regenerated a black segment before concat.
3. **Fallback visuals are near-black.** The emergency fallback card is
   solid `0x0b1020` (luma ≈ 0.064 < blackdetect `pix_th=0.10`), so even
   recovered scenes get flagged black.
4. **No stderr surfaced.** `FFmpegRunError` kept only a 15-line tail in
   memory; the user-facing message hid the actual FFmpeg reason.

v2 eliminates all four: pre-flight input validation, blocking scene
validation + recovery, a guaranteed-valid *bright* fallback card, and
full stderr capture in per-render logs and the error dialog.

## Module map

```
src/
  version.py            version constants (1.0.1 -> 2.0.0)
  app_paths.py          %LOCALAPPDATA%\U One layout (logs, projects, cache)
  settings.py           NEW: typed settings + defaults + validation
  secure_store.py       OS credential storage for API keys (unchanged)
  log_system.py         from app_log.py: RenderLogger -> logs/render_*.log
                        (stages, scene status, FFmpeg cmd + FULL stderr,
                         retries, result)

  ffmpeg_engine.py      from ffmpeg_util.py:
                          FFmpegEngine.run(argv, stage, scene, timeout,
                                           cancel_event) -> FFmpegResult
                            (cmd, stdout, stderr, exit_code, duration_s)
                          Popen-based so Cancel truly kills the child.
                          detect_hw_encoder() -> h264_nvenc|h264_qsv|
                            h264_amf|None; CPU fallback always.
                          Never hides stderr. Structured FFmpegError.
  media_probe.py        NEW: probe(path) -> MediaInfo (single ffprobe call,
                        cached): container, duration, video{codec,w,h,fps,
                        pix_fmt, nb_frames}, audio{codec, sr, channels,
                        duration}
  scene_validator.py    NEW: validate_segment(path, spec) -> SegmentReport
                        (valid, fatals[], warnings[], metrics{black_ratio,
                        frozen_ratio, decoded_frames}).
                        Checks: exists, size>0, container, video stream,
                        codec, w/h>0, duration>0, frames decode,
                        black/frozen/zero-frame/corrupt detection.
  media_normalizer.py   NEW: normalize_segment(path, target_spec) -> path.
                        Fast path: already compliant -> return as-is
                        (stream copy, no re-encode). Else re-encode only
                        what mismatches (res/fps/pix_fmt/vcodec/acodec/
                        sample rate/channels/timebase).
  cache_manager.py      from asset_cache.py + validation metadata
                        (hash, duration, res, codec, validation JSON);
                        invalidate on source change.

  script_engine.py      script_analyzer.py + scene_detector.py
  voiceover.py          voice.py (upload or synthesize + timings)
  media_search.py       slimmed: search/download/fallback asset acquisition
  scene_generator.py    NEW: per scene -> search asset -> render segment ->
                        validate -> recover (max 3: alternate query,
                        regenerate, fallback card) -> guaranteed-valid
                        segment or SceneFailed. Parallel across scenes
                        (bounded workers from CPU/RAM).
  timeline_engine.py    timeline.py (unchanged logic)
  audio_mixer.py        audio_design.py (SFX dedup warning, optional music)
  text_engine.py        text_captions.py (ASS burn-in, overflow-safe)

  qc_engine.py          from renderer.quality_check + detectors:
                        returns (fatals[], warnings[]); fatals block render.
  export_engine.py      from renderer.render_final + validate_final_mp4:
                        pre-flight input validation -> render (hwaccel w/
                        CPU fallback) -> full §12 final-MP4 checklist ->
                        copy to Videos folder.
  render_engine.py      NEW: 11-stage orchestrator (replaces pipeline.py):
                          1 project validation   2 script processing
                          3 voiceover            4 scene generation
                          5 scene validation     6 scene recovery
                          7 media normalization  8 timeline construction
                          9 final render        10 final MP4 validation
                         11 export
                        Per-stage status callbacks, cancel, resume
                        (skip completed stages from saved state).
  error_recovery.py     NEW: UOneError(stage, scene, exit_code, reason,
                        recovery_hint); RetryPolicy(max_attempts=3);
                        render-state persistence for resume.
  temp_manager.py       NEW: TempManager context; owns temp files; cleanup
                        on success / cancel / error; never leaks.
  project_manager.py    project.py + autosave (debounced) + save-on-failure
                        (scene metadata, asset paths, logs, FFmpeg cmds,
                        failure info) + resume_render().

  ui/
    main_window.py      async render worker (QThread), Cancel wired to
                        engine.cancel(), Logs dock, Dashboard, per-stage
                        progress ("Validating scenes... 17/31")
    timeline_widget.py  (as-is)
```

## Data flow (happy path)

```
Project -> RenderEngine.run()
  1. validate project/settings            (error_recovery)
  2. script_engine -> sentences/scenes
  3. voiceover -> voice_path + timings
  4. scene_generator (parallel, bounded):
       asset = media_search.find(...)      (cache_manager)
       seg   = ffmpeg_engine.render segment
       rep   = scene_validator.validate(seg)
       if invalid: recover (x3) else fallback card (bright, pre-validated)
  5. qc_engine over segments -> fatals block, warnings logged
  6. media_normalizer -> uniform segments
  7. timeline_engine.build(...)
  8. audio_mixer.mix(...)
  9. export_engine.render_final (pre-flight probe of inputs)
 10. export_engine.validate_final_mp4 (full checklist)
 11. copy to ~/Videos + project_manager.persist(success)
```

Every FFmpeg call: `ffmpeg_engine.run()` captures argv, stdout, stderr,
exit code, duration; log_system writes all of it to the render log; the
error dialog shows stage/scene/exit code/reason/recovery hint + stderr tail.

## Performance

- Bounded ThreadPoolExecutor for scenes: `min(4, cpu_count)` workers.
- Segment cache keyed by content hash + params + validation result.
- MediaInfo probe results cached per path+mtime (no repeated ffprobe).
- Normalizer fast path avoids re-encode when compliant.
- HW encoder auto-detect with CPU fallback; failure -> log + CPU retry.

## Tests (tests/)

- test_media_probe.py / test_scene_validator.py: valid, zero-byte,
  corrupt, black, one-frame, missing-audio, bad-codec fixtures.
- test_ffmpeg_engine.py: success, failure (+stderr capture), missing
  input, cancel kills process.
- test_render_pipeline.py: 1/3/10 scenes; mixed valid+invalid scenes;
  missing scene; resume after simulated crash.
- test_31_scene_recovery.py (MANDATORY §28): 31 scenes with 13
  deliberately broken (black/corrupt/zero-byte) -> all detected,
  recovered, final MP4 valid with correct duration.
- test_project_manager.py: save/load/autosave/recovery/resume.

## Build system (unchanged contract)

- `build_windows.bat` (one-click) -> `dist\U One\U One.exe`
  -> `Output\U_One_Portable.zip` -> `Output\U_One_Setup.exe`
- `u_one.spec` (one-folder, version resource fixed),
  `installer\u_one.iss`, `build\windows\validate_install.ps1`
  (+ `--selftest` smoke test of the new pipeline).
