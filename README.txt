================================================================
 U ONE - AI AUTOMATIC VIDEO EDITOR - README
 Script + Voice In -> Complete Edited Video Out
================================================================

WHAT THIS IS
-----------
U One is a Windows desktop application that turns a script (any
topic) plus a voiceover into a complete, edited MP4 video
automatically:

  script analysis -> scene detection -> voice timing ->
  Pexels/Pixabay search -> relevance check -> voice sync ->
  timeline -> captions -> SFX + music -> mixing ->
  quality control -> final MP4 export

You do NOT need to upload images, videos, B-roll, music, or sound
effects. U One finds or generates everything itself.

----------------------------------------------------------------
1. HOW TO INSTALL (NORMAL USERS)
----------------------------------------------------------------
  1. Download  U_One_Setup.exe
  2. Double-click it and follow the installer.
  3. Launch "U One" from the Desktop or Start Menu.

That is all. You do NOT need to install Python, pip, packages,
FFmpeg, or anything else - everything is bundled inside the
installer. No Administrator rights are needed. No command line.

----------------------------------------------------------------
2. HOW TO CREATE YOUR FIRST VIDEO
----------------------------------------------------------------
  1. Open U One.
  2. (Optional, once) Settings -> "4. API Settings": paste your
     Pexels and/or Pixabay API keys -> Save -> Test Connection.
  3. Paste your script into "1. Script".
  4. "2. Voiceover": upload your narration, or choose
     "Generate AI voice from script".
  5. "3. Settings": pick style, visual mode, format, quality.
  6. Click the big CREATE VIDEO button.
  7. Wait for the 12 stages to finish.
  8. Your MP4 is ready and plays in the preview.

----------------------------------------------------------------
3. API KEYS (OPTIONAL, FREE)
----------------------------------------------------------------
Pexels (https://www.pexels.com/api/) and Pixabay
(https://pixabay.com/api/docs/) give you real stock video and
photos. Keys are OPTIONAL - without them U One builds every video
with its built-in graphics engine (maps, charts, title cards,
location cards).

Enter keys ONLY in the app: Settings -> "4. API Settings" ->
paste -> Save -> Test Connection. Done. No config files, no
environment variables, no command line.

Keys are stored securely in Windows Credential Manager (or a
protected file if that is unavailable). They are NEVER shown
again, NEVER written to logs, and NEVER saved into projects.

Priority when keys exist: relevant video -> relevant image ->
generated graphic. Broken, irrelevant, duplicate or very
low-resolution results are automatically rejected.

----------------------------------------------------------------
4. WHICH FEATURES WORK WITHOUT ANY API KEYS
----------------------------------------------------------------
Everything except real stock footage:

  - AI voice generation (edge-tts, free, needs internet)
  - Offline voice fallback (Windows speech)
  - Voiceover timing (word timestamps when available, robust
    proportional fallback otherwise)
  - Scene detection (every sentence / every 2 / every 3 / AI auto)
  - Contextual media search + relevance scoring
  - Generated visuals: title cards, location/map cards, number charts
  - Subtle Ken Burns zoom/pan motion on still images
  - Short on-screen text overlays (never the full script)
  - Timed captions with word highlighting (SRT + ASS)
  - Contextual sound effects (restrained, no spam)
  - Background music with speech ducking (generated ambient bed,
    or your own folder of royalty-free tracks)
  - Professional audio mixing with clipping prevention
  - Gentle transitions
  - Full quality-control pass (black frames, duplicates, low-res,
    clipping, silent gaps, subtitle errors, text overflow)
  - 6-track timeline, per-scene regenerate, scene locking
  - YouTube title/description/chapters/tags/thumbnail ideas
  - Save / open / autosave / crash recovery / undo / redo
  - Export 16:9, 9:16, 1:1 - 1080p/1440p/4K - 24/30/60fps - MP4 H.264/H.265

There are NO mandatory paid APIs. Nothing requires a credit card.

----------------------------------------------------------------
5. WHERE THINGS ARE STORED
----------------------------------------------------------------
U One keeps its writable data in your personal Windows folders:

  %LOCALAPPDATA%\U One\      projects, work files, media cache, logs
  %USERPROFILE%\Videos\      default folder for finished videos

Uninstalling U One leaves your projects and logs in place.

----------------------------------------------------------------
6. IF SOMETHING GOES WRONG
----------------------------------------------------------------
U One never closes itself because a render failed. If a scene's
media fails, U One logs it, picks a fallback visual and continues
the video. If the whole render cannot finish, you get a short
plain message (not a giant FFmpeg command), your project is
saved, and technical details go to:

  %LOCALAPPDATA%\U One\logs\u_one.log

"FFmpeg was not found"
  -> Reinstall U One (FFmpeg is bundled with the installer).

"AI voice failed"
  -> Needs internet on first use. Offline? U One falls back to
     Windows speech automatically; or upload a recorded voiceover.

Video shows generated cards instead of real footage
  -> Add a free Pexels/Pixabay key (section 3). Without keys,
     generated graphics are used by design.

U One closed unexpectedly
  -> Reopen it: it autosaves and offers to restore the
     interrupted project.

----------------------------------------------------------------
7. FOR DEVELOPERS (RUNNING FROM SOURCE)
----------------------------------------------------------------
Developers can run U One from source instead of the installer:

  pip install -r requirements.txt
  python main.py

Headless render:

  python main.py --cli --script myscript.txt --voice narration.mp3
  Extra flags: --title, --workdir, --format 16:9|9:16|1:1,
               --res 1080p|1440p|4K, --fps 30

Build the Windows installer (on Windows 10/11):

  build\windows\build.bat      (builds U One.exe, bundles FFmpeg)
  iscc installer\u_one.iss     (builds U_One_Setup.exe, needs Inno Setup 6)

================================================================
