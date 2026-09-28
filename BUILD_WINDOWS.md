# Build U One on Windows — one double-click, zero prerequisites

You are holding the complete source of **U One — AI Automatic Video Editor**.
The Linux machine that prepared it cannot run Windows, so the final
Windows build runs on a real Windows 10/11 PC — as **one automated
command** with nothing to install first.

## The one command

Copy the whole `universal-ai-video-editor` folder to the Windows PC
(anywhere — paths with spaces are fine), then double-click:

    build_windows.bat          <- project root, the one-click builder

That's it. It automatically, in order:

0. Uses Python 3.10+ if present, otherwise **downloads and installs
   Python 3.11 for the current user** (python.org, silent, no admin)
1. Creates a virtual environment, installs all dependencies
2. Downloads FFmpeg + FFprobe for Windows and **bundles them into the app**
3. Sanity-checks that `build\windows\u_one.spec` targets `U One`,
   wipes any stale `dist\` output, and builds `dist\U One\U One.exe`
   (aborts loudly if the exe name is wrong — a wrongly named exe can
   never slip into the installer)
4. Builds `Output\U_One_Portable.zip` (portable build)
5. Uses Inno Setup 6 if present, otherwise **downloads and installs it
   for the current user** (silent, no admin), then compiles
   **`Output\U_One_Setup.exe`** — and verifies the file exists before
   declaring success

When it finishes you have the finished consumer artifacts:

| File | What it is |
|---|---|
| `Output\U_One_Setup.exe` | The real installer. End users double-click it — no Python, pip, FFmpeg, Inno Setup, CMD, or PowerShell ever needed by them |
| `Output\U_One_Portable.zip` | Portable build — unzip anywhere, run `U One.exe` |

> The old root-level `build_windows.bat` that produced
> `dist\UniversalAIVideoEditor.exe` has been replaced by this builder.
> There is exactly one build implementation now
> (`build\windows\build.bat` just forwards to it).

## Validate automatically (on the same PC)

In PowerShell, from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File build\windows\validate_install.ps1
```

It will:

1. Install `U_One_Setup.exe` silently (per-user, no admin)
2. Verify `U One.exe`, bundled `ffmpeg.exe`/`ffprobe.exe`
3. Verify the Start Menu shortcut and the Add/Remove Programs entry
   (Settings → Apps → Installed Apps)
4. Launch U One from the shortcut and confirm the process stays up
5. Run `U One.exe --selftest` — a **full end-to-end render on your PC**:
   script + voice → scenes → visuals → captions → audio mix → final MP4,
   in **16:9 and 9:16**, using a work path with spaces and parentheses,
   then validate both MP4s with the bundled FFprobe. A report is written to
   `%LOCALAPPDATA%\U One\selftest\report.txt` and printed to the console.
6. Offer to **open the rendered MP4 in your default player** to confirm
   real playback.
7. Offer to uninstall silently and verify complete removal
   (app, shortcuts, and Add/Remove Programs entry gone; user data kept).

No Python, pip, FFmpeg, or API keys are needed for any of this.

## Manual checks in the GUI (recommended)

1. Launch **U One** from the Start Menu / Desktop shortcut.
2. **Settings → API Settings**: paste your Pexels and/or Pixabay keys,
   press **Save**, then **Test Connection** for each. Keys are masked,
   stored in Windows Credential Manager, and never written to logs.
3. Paste a script, **upload a voiceover** (or **Generate AI voice**),
   press **CREATE VIDEO**.
4. Confirm:
   - the finished MP4 **plays** in Movies & TV / VLC,
   - a copy lands in your **Videos** folder as
     `U One <project> <timestamp>.mp4`,
   - captions and the branded intro/outro look right.
5. Torture test: use a voiceover from
   `C:\Users\Gillani Computers\Downloads\my vid (1)\voice.wav`
   (spaces + parentheses) and a script with quotes, apostrophes, commas,
   colons, and Unicode (café 中文). Path handling was fixed and is covered
   by the automated self-test.
6. Uninstall from **Settings → Apps → U One → Uninstall** and confirm the
   app, shortcuts, and Add/Remove Programs entry are gone
   (your projects and API keys are intentionally left in place).

## Troubleshooting

- **No internet on the build PC** — the builder downloads Python (if
  missing), dependencies, FFmpeg, and Inno Setup (if missing). Without
  internet it cannot proceed; pre-install Python 3.10+ and Inno Setup 6
  and re-run (it reuses everything already downloaded).
- **SmartScreen warning on U_One_Setup.exe** — expected for a new,
  unsigned installer: click *More info → Run anyway*.
- **"FFmpeg not found" in the app** — reinstall from `U_One_Setup.exe`;
  the portable ZIP must keep its folder structure intact
  (`U One\ffmpeg\ffmpeg.exe` next to `U One.exe`).

## What the installer does

- Installs to `%LOCALAPPDATA%\U One` (**current user only, no admin**)
- Creates a **Start Menu** shortcut and offers a **Desktop** shortcut
- Registers an **uninstaller** (Settings → Apps → Installed Apps)
- Bundles **Python runtime, all dependencies, FFmpeg + FFprobe** —
  the user installs nothing else
- Leaves user data (projects, cache, logs, API keys) on uninstall
