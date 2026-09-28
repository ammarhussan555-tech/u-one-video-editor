# U One — Changelog

## 1.1.0 (2026-09-27) — Engine rebuild: 11-stage render pipeline

Complete architectural rebuild of the render path (old `Pipeline` retired;
GUI, CLI and `--selftest` now drive the new engine).

### New architecture (src/)
- **ffmpeg_engine.py** — every FFmpeg call goes through one engine:
  structured `FFmpegError` (stage, exit code, stderr tail, full command,
  recovery hint), real child-process cancellation (terminate → kill),
  hardware-encoder detection with automatic CPU fallback.
- **media_probe.py** — single FFprobe gateway: `MediaInfo` with
  exists/size/streams/codec/dimensions/fps/duration, never raises on
  broken media.
- **scene_validator.py** — full-stream decode + `blackdetect`/`freezedetect`
  per scene segment. A dark-but-real frame check (bright-pixel census)
  distinguishes intentional dark visuals (navy title cards, night scenes)
  from truly blank video — dark cards no longer false-positive to the
  fallback.
- **media_normalizer.py** — every segment is normalized to the output
  resolution/FPS/pixel-format/codec before concat.
- **scene_generator.py** — parallel scene rendering with per-scene retry
  (attempts 0→1→2), new asset per attempt, branded fallback card only as
  a last resort, segment cache with validation-aware keys.
- **render_engine.py** — 11 explicit stages: validate → script → voiceover
  → scene generation → scene validation → scene recovery → normalization
  → timeline/audio/captions → final render → final validation → export.
  Final render is preflighted by the full segment validator (black,
  corrupt, empty, missing, undecodable all rejected before concat).
- **error_recovery.py** — `UOneError` carries a simple user message plus
  technical detail; every failure writes a snapshot
  (`work_dir/last_error.uvp.json`: scene states, FFmpeg commands, log
  path) and the app never closes on a render failure.
- **project_manager.py / cache_manager.py / temp_manager.py / log_system.py**
  — atomic project saves, failure snapshots, render-state persistence and
  resume, one render log per run, temp-dir lifecycle management.
- **export_engine.py / qc_engine.py** — export profiles (360p–4K, H.264/H.265)
  and final MP4 QC (video+audio streams, dimensions, FPS, duration).

### Fixes found by the 34-test battery (all 34 PASS on Linux)
- Scene retries reused attempt 0 forever — now advance 0→1→2.
- Fallback card was copied at a fixed 6s into every scene — now
  trimmed/looped to the scene's requested duration.
- Final-render preflight was a shallow FFprobe check that let black video
  through — now runs the full segment validator.
- Black-frame detector false-positived on dark navy title cards
  (generated graphics are `#0b1020`) — bright-pixel census added, and the
  graphic generator fixed (`bbox_inches="tight"` was producing 6861×2035
  cards with microscopic text; cards are now exactly 1280×720).
- Cache-hit scene results dropped the Asset — GUI scene panel and Sources
  tab now always see the asset.
- GUI/CLI/selftest migrated from the old `Pipeline` to `RenderEngine`;
  scene regenerate buttons (visual/text/SFX/music) re-implemented on the
  new engine; cancel now stops the real FFmpeg child process.
- `Settings.visual_mode` validation now matches the GUI's actual options.

### Windows build kit
- `u_one.spec` hidden imports extended for all new engine modules.
- `U One.exe --selftest` and `--cli` exercise the new `RenderEngine`.
- Version bumped to 1.1.0 (version.py, installer .iss, version.txt).

### Honest limits (unchanged)
- No Windows machine in this sandbox: `U_One_Setup.exe` can only be built
  and install-tested on the user's real Windows 10/11 PC via
  `build_windows.bat`. Linux results below are from the real FFmpeg
  pipeline, not the frozen app.

## 1.0.0 (2026-09-26) — "U One" release (test-fix update)

### Fixes found by the 23-test battery (all 23 now PASS)
- **FFmpeg option ordering**: `-filter_script:v` was placed before `-i`,
  so FFmpeg misread it as an input option → "Error opening input files:
  Invalid argument" (the same failure class as the reported Windows error).
  Inputs now always precede output options.
- **Segment cache keys were never stable**: a `hashlib.sha1()` object named
  `h` shadowed the height parameter and `str(h)` embedded a memory address.
  Keys now use content-hash fingerprinting; run 2 of an identical project
  reuses 3/3 cached segments.
- **Concat list used CWD-relative paths**: broke when `work_dir` was
  relative. Both concat paths now write absolute entries.
- **`Pipeline._log()` missing `exit_code` kwarg** masked real errors with
  `TypeError`.
- Audio mixer and `voice.py` used bare `"ffmpeg"`/`"ffprobe"` strings;
  both now resolve the bundled binary via `ffmpeg_util`.
- API "Test Connection" moved off the UI thread (QThread worker).
- Finished videos are now also copied to the user's Videos folder as
  `U One <project> <timestamp>.mp4`.

## 1.0.0 (2026-09-26) — "U One" release

Complete rebuild, rebrand and hardening of the AI video editor as
**U One — AI Automatic Video Editor**.

### Product
- Official application name is now **U One** everywhere: window title,
  installer, shortcuts, icon metadata, About screen, README, build files.
- New application icon (`assets/u_one.ico`, multi-size ICO + PNG).
- `src/version.py` is the single source of truth for name/version.

### Windows installer (new)
- `build/windows/build.bat`: one-command Windows build — venv, pip deps,
  downloads FFmpeg/FFprobe binaries, builds `U One.exe` with PyInstaller
  (windowed, no console), version metadata + icon.
- `build/windows/u_one.spec`: PyInstaller spec (bundles FFmpeg/FFprobe).
- `installer/u_one.iss`: Inno Setup 6 script producing **U_One_Setup.exe**
  with Desktop shortcut, Start Menu shortcut and Uninstaller.
- Install is per-user (`%LOCALAPPDATA%\U One`), no admin rights required.
- End users never install Python, pip, packages or FFmpeg.

### FFmpeg root-cause fix
- New `src/ffmpeg_util.py`: every FFmpeg call uses argv arrays (no shell,
  no command strings); filter-level escaping helpers; complex graphs go
  through `-filter_script:v` files; structured errors (stage/scene/exit
  code) instead of dumping giant commands.
- **drawtext eliminated**: all on-screen text now goes through libass
  (new "Top" ASS style for overlays). Long text, Unicode, commas, colons,
  quotes, apostrophes and parentheses can no longer break rendering.
- Subtitle paths escaped per the subtitles-filter rules, with automatic
  retry from a sanitized temp copy, then graceful caption-less fallback.
- Concat list files escape single quotes; Windows paths normalized.
- Final MP4 is validated (file, video+audio streams, codec, resolution,
  duration) — "Completed" is never reported unless it passes.

### Reliability
- Scene failure isolation: one bad asset/scene can never kill the video —
  failures log, fall back to a generated card, rendering continues.
- U One never closes itself on a render failure: short plain message,
  project auto-saved, technical details to the log.
- Segment-level render cache: re-renders/resumes skip scenes that already
  succeeded (content-hash keyed).
- API keys never appear in logs, error messages, exported projects or the
  UI after saving (redacting log filter + secure storage + key stripping
  in project serialization).

### Speed
- Pexels + Pixabay searched concurrently (thread pool), shared pooled
  HTTP session with retries, per-scene parallel search.
- URL-hash media cache: the same asset is never downloaded twice
  (even across projects); 2 GB cap with pruning.
- Scene render cache reuses successful segments on re-render/resume.

### Media safety
- Downloads verify HTTP status, content-type, minimum size, stall
  timeouts, and decode/probe the file before acceptance; broken files
  are rejected and the next candidate is tried; safe temp filenames.

### API settings UX
- New "4. API Settings" section: Pexels/Pixabay key fields (masked),
  **Save**, **Test Connection**, **Connected / Not Connected** status.
- Keys stored in Windows Credential Manager via keyring (protected-file
  fallback); resolution order: env var → secure storage → UI/config.
- No config.yaml editing, no environment variables, no command line needed.

### Platform correctness
- All writable data lives in per-user OS directories
  (`%LOCALAPPDATA%\U One\...`), never the install folder or CWD.
- Finished videos default to the user's Videos folder.

### Docs
- README.md / README.txt rewritten for U One (installer-first workflow).
- TEST_REPORT.md with the full verified test battery.

## 1.0.1 (2026-09-27) — Windows build kit

### Added
- `U One.exe --selftest` (`src/selftest.py`): built-in end-to-end validator.
  Renders script+voice → scenes → visuals → captions → audio mix → final MP4
  in 16:9 and 9:16 with a spaces/parens work path, validates both MP4s with
  the bundled FFprobe, writes `%LOCALAPPDATA%\U One\selftest\report.txt`.
  5/5 PASS headless (fixed a `check_ffmpeg` import during the run).
- `build\windows\build.bat`: now one double-click — Python 3.10+ check,
  venv, deps, FFmpeg download, PyInstaller, `U_One_Portable.zip`, and
  `U_One_Setup.exe` via auto-detected Inno Setup 6.
- `build\windows\validate_install.ps1`: automated install → shortcuts →
  launch → `--selftest` → optional uninstall verification + manual GUI
  checklist (API keys, Videos-folder copy, hostile-path voiceover).
- `BUILD_WINDOWS.md`: exact build/validate steps for a real Windows 10/11 PC.

### Honest status
- No Windows machine exists in this environment, so `U_One_Setup.exe`
  could not be produced or install-tested here. The final mile (build,
  install, launch, render, MP4 playback, uninstall on Windows 10/11, plus
  real Pexels/Pixabay keys) must run on the user's PC per `BUILD_WINDOWS.md`.

### Build hardening (2026-09-27)
- `build.bat` now deletes any stale `dist\` folder before running PyInstaller,
  so leftovers from a manual/older build (e.g. a wrongly named
  `dist\UniversalAI Video Editor.exe`) can never contaminate the package.
  It then asserts `dist\U One\U One.exe` exists and aborts loudly otherwise.
- Verified statically: `u_one.spec` names EXE and COLLECT `U One`
  (→ `dist\U One\U One.exe`); `u_one.iss` consumes `..\dist\U One\*`,
  installs `U One.exe`, and emits `Output\U_One_Setup.exe`. No occurrence of
  the name "UniversalAI Video Editor" exists anywhere in the source tree.

### One-click builder (2026-09-27)
- **Root cause of `dist\UniversalAIVideoEditor.exe` found**: a stale
  `build_windows.bat` in the project root (leftover from the original
  upstream project) ran `pyinstaller --name "UniversalAIVideoEditor"`,
  bypassing the spec and Inno Setup entirely. It has been **replaced** by
  the real one-click builder - the old config can no longer be executed.
- New `build_windows.bat` (project root) is now the single build
  implementation with **zero prerequisites**: auto-installs Python 3.11
  (per-user, silent, no admin) and Inno Setup 6 (per-user, silent) when
  missing; sanity-checks that `u_one.spec` targets `U One` before building;
  wipes stale `dist\`; aborts loudly unless `dist\U One\U One.exe` is
  produced; verifies `Output\U_One_Setup.exe` exists before declaring
  success. `build\windows\build.bat` is now a thin forwarder to it.
- `validate_install.ps1` now offers to open the rendered test MP4 in the
  default player to confirm real playback (Install -> Launch -> Create
  Video -> Render -> MP4 opens).

### Batch syntax fix (2026-09-27)
- `build_windows.bat` failed on Windows with "was unexpected at this time"
  right after step [0/7]: the `if (...)` blocks contained `for /f` loops
  with parentheses inside their command strings
  (e.g. `print(sys.executable)`), which breaks CMD's block parser.
- Rewrote the builder as plain linear batch: single-line `if ... goto`
  conditionals only, no nested blocks, no delayed expansion, no quoted
  subroutine arguments. All `for /f` loops sit at top level. Safe with
  spaces in the path (verified by structural audit: zero block constructs).

### Version-info fix (2026-09-27)
- PyInstaller failed with `NameError: name 'StringStructXXX' is not defined`:
  an earlier sed version-bump had mangled the
  `StringStruct('FileVersion', ...)` line in `build/windows/version.txt`
  into the invalid token `StringStructXXX`. Rewrote `version.txt` cleanly
  with valid VSVersionInfo syntax (verified by evaluating it exactly as
  PyInstaller's loader does - no NameError; branding and 1.0.1 versions
  asserted).
- `u_one.spec` now passes an absolute path for the version resource
  (`VERSION_FILE` derived from SPECPATH, like the icon path) instead of
  the CWD-relative `"version.txt"`.
