# U One — AI Automatic Video Editor

**SCRIPT + VOICE IN → COMPLETE EDITED VIDEO OUT.**

U One is a Windows desktop application that turns any script + voiceover
into a fully edited video: visuals per sentence, voice-synced timing, text
overlays, captions, sound effects, background music, transitions, audio
mixing, quality control and final MP4 export. Topic-agnostic — science,
nature, volcanoes, space, history, technology, finance, travel, crime
documentaries, storytelling: the AI derives everything from your script.

## 1. Install (normal users — no technical setup)

1. Download **U_One_Setup.exe**
2. Double-click it and follow the installer
3. Launch **U One** from the Desktop or Start Menu

That's it. Python, pip, packages and FFmpeg are all bundled inside the
installer. No Administrator rights needed. No command line.

## 2. First video

1. Open U One → Settings → **4. API Settings**: paste your Pexels and/or
   Pixabay API keys (free, optional) → **Save** → **Test Connection**
2. Paste your script into **1. Script**
3. **2. Voiceover**: upload your narration, or generate an AI voice
4. **3. Settings**: style, visual mode (Every Sentence / Every 2 / Every 3 /
   AI Automatic), aspect ratio, quality
5. Press **CREATE VIDEO** and watch the 12 stages finish
6. Your MP4 plays in the preview — done

Without API keys U One still works: it builds every visual with its
built-in graphics engine (maps, charts, location cards, title cards).

## 3. Optional stock footage API keys

- Pexels: https://www.pexels.com/api/ (free)
- Pixabay: https://pixabay.com/api/docs/ (free)

Enter them only in the app (Settings → API Settings → Save). They are
stored securely in Windows Credential Manager, never shown again, never
logged, never saved into projects. No config files, no environment
variables, no command line.

Search priority: highly relevant video → highly relevant image →
generated graphic. Broken, irrelevant, duplicate or very low-resolution
results are rejected automatically.

## 4. AI voiceover

- **Upload** your own voiceover (WAV/MP3/M4A) — used as the timing authority, or
- **Generate** inside the app: `edge-tts` first (free, needs internet, gives
  exact word timings), Windows speech via `pyttsx3` as offline fallback.

## 5. Reliability

- Every scene renders in isolation: one bad asset can never kill the video.
  Failures are logged, a fallback visual is used, rendering continues.
- Successfully rendered scenes are cached — re-renders and resumes skip them.
- U One never closes itself on a render failure. You get a short plain
  message, your project is saved, technical details go to the log file
  (`%LOCALAPPDATA%\U One\logs\u_one.log`).
- Autosave + crash recovery: reopen U One and restore the interrupted project.

## 6. For developers (source mode)

```bash
pip install -r requirements.txt
python main.py
```

Headless render:

```bash
python main.py --cli --script myscript.txt --voice narration.mp3 \
  --format 16:9 --res 1080p --fps 30
```

Build the Windows installer (on Windows 10/11) — one double-click,
zero prerequisites (Python and Inno Setup auto-install if missing).
See `BUILD_WINDOWS.md`:

```bat
build_windows.bat            :: project root: builds Output\U_One_Setup.exe
```

Validate the install on the Windows PC:

```powershell
powershell -ExecutionPolicy Bypass -File build\windows\validate_install.ps1
```

Or validate the full render pipeline with one command (no GUI, no keys):

```bat
"U One.exe" --selftest      :: 16:9 + 9:16 renders, validates both MP4s
```

## 7. Project structure

```
main.py                    entry point (GUI + --cli headless mode)
config.yaml                defaults (advanced; API keys now live in secure storage)
src/
  version.py               single source of truth for app name/version
  app_paths.py             per-user OS directories (no CWD/install-dir writes)
  app_log.py               logging with API-key redaction
  secure_store.py          API keys in Windows Credential Manager (fallback: protected file)
  asset_cache.py           URL-hash media cache + scene segment cache
  ffmpeg_util.py           safe FFmpeg layer: argv arrays, filter escaping,
                           filter-script files, structured errors
  script_analyzer.py       topic/entities/keywords/mood/search-query generation
  scene_detector.py        sentence -> scene splitting, pacing modes
  voice.py                 TTS (edge-tts/pyttsx3) + transcription/timing
  media_search.py          Pexels/Pixabay (concurrent, pooled, cached, verified
                           downloads) + generated graphics fallback
  media_relevance.py       relevance scoring, reject-and-retry
  timeline.py              6-track timeline (voice/video/text/captions/sfx/music)
  text_captions.py         overlay text + SRT/ASS captions (ASS top-style
                           overlays replace drawtext entirely)
  audio_design.py          synthesized SFX library, music select/synth, mixing
  renderer.py              segments -> concat -> captions -> MP4 export + QC
  project.py               save/open/autosave/undo/redo (keys never persisted)
  pipeline.py              CREATE VIDEO orchestrator (12 stages, cancel,
                           scene isolation, segment cache, parallel search)
  ui/main_window.py        PySide6 interface ("U One — AI Automatic Video Editor")
  ui/timeline_widget.py    6-track timeline widget
build/windows/            PyInstaller spec + build.bat + version info
installer/u_one.iss        Inno Setup script -> U_One_Setup.exe
assets/                   application icon (PNG + multi-size ICO)
```

## 8. Honest limitations

- The Windows installer (`U_One_Setup.exe`) must be built on Windows —
  `build\windows\build.bat` + `installer\u_one.iss` are provided and ready,
  but the actual `.exe` cannot be produced on Linux/macOS.
- Visual relevance checking is heuristic (keyword/tag overlap + resolution).
  A CLIP-based vision scorer can replace `media_relevance.score_candidate`.
- Transcription uses `faster-whisper` if installed, else TTS word timings,
  else proportional timing fallback.
- SFX/music are synthesized or user-provided unless you add licensed libraries.
- Always verify stock licenses in the SOURCES panel before publishing.

See `CHANGELOG.md` for version history and `TEST_REPORT.md` for the
verified test results.
