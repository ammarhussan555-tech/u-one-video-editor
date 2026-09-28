# U One — Test Report

**Date:** 2026-09-26
**Version:** U One 1.0.0 ("U One — AI Automatic Video Editor")
**Environment:** Linux sandbox, Python 3.12, FFmpeg 8.1.2 / ffprobe present.
No Pexels/Pixabay API keys available; no Windows machine available.

## Results — 23/23 PASS

| # | Test | Result | Detail |
|---|------|--------|--------|
| T0 | Hostile-path escaping | PASS | `C\:/Users/Gillani Computers/Downloads/my vid (1)/cap\'s\,.ass` — spaces, parens, quotes, commas, backslashes all escaped for the subtitles filter |
| T0 | ASS sanitization | PASS | `\N` override blocks stripped, `café 中文` preserved |
| T1 | 3-sentence 16:9 render | PASS | Valid MP4 (video+audio, 1920×1080, 30fps, H.264/AAC) in 101s |
| T1 | Scene/visual mapping | PASS | 3 scenes → 3 visuals |
| T1 | No duplicate visuals | PASS | g1, g0, g2 — all distinct |
| T1 | Captions produced | PASS | SRT + ASS written |
| T1 | Overlay style in ASS | PASS | Top-positioned style present; drawtext eliminated from final render |
| T2 | 11-sentence documentary | PASS | Valid MP4 in 271s, 11 scenes, zero duplicate visuals |
| T2 | Voice timing authority | PASS | Voice 60.0s = scene timeline 60.0s exactly |
| T3 | Hostile text render | PASS | Apostrophes, quotes, commas, parens, colon, Unicode, long text — valid MP4 in 101s |
| T4 | Paths with spaces | PASS | `dir with spaces (test)/my voice.wav` → valid 1920×1080 MP4 in 75s |
| T5 | Mixed video+image segments | PASS | Both segment types render to valid 1920×1080 MP4s |
| T7 | 9:16 export | PASS | Valid 1080×1920 MP4 in 94s |
| T9a | Missing asset error | PASS | Structured `FFmpegRunError` (stage=scene_segment, exit=254), no crash |
| T9b | Scene fallback | PASS | Broken scene → branded fallback card, valid MP4 |
| T9c | Poisoned-scene isolation | PASS | Mid-render scene failure logged, fallback used, full video completes |
| T10/11 | Empty API keys | PASS | Graceful "Not Connected — No key saved", no crash |
| T12 | Bogus API key | PASS | Graceful "Key rejected (401/403)", key never echoed in message |
| T12b | Live Pexels reachability | PASS | `api.pexels.com` reachable; bogus key → HTTP 401 as expected |
| T13 | Asset URL cache | PASS | Store + lookup hit, no re-download |
| T14 | Autosave + recovery | PASS | `.autosave` written on interval; reload restores script |
| T15 | Segment cache reuse | PASS | Run 2 reused 3/3 cached segments, final MP4 valid |

## Visual verification

- Frame extracted from a captioned render vs. the caption-less concat:
  mean abs diff **5.35** → burn-in confirmed (not a silent no-op).
- Frame inspected: top overlay ("LOCATION / MOUNT ETNA ERUPTED LAST NIGHT"),
  location-card graphic, and bottom caption all visible, wrapped, inside
  safe margins.

## Bugs found and fixed during testing

1. **FFmpeg option ordering** — `-filter_script:v` was placed before `-i`,
   so FFmpeg treated it as an *input* option: "Error opening input files:
   Invalid argument" (exit 234). This is the same failure class as the
   reported Windows `Error opening output file ... Invalid argument`.
   Fixed by placing inputs first, output options after.
2. **`Pipeline._log()` missing `exit_code` kwarg** — masked real errors with
   `TypeError`. Fixed.
3. **Segment cache key used a `hashlib.sha1()` object named `h`** that
   shadowed the height parameter; `str(h)` embedded a memory address, so
   keys were never stable. Replaced with content-hash fingerprinting.
4. **Concat list used CWD-relative paths** — broke when `work_dir` was
   relative (concat demuxer resolves entries against the list file).
   Now writes absolute paths in both `concat_segments` and the pipeline
   fallback.
5. **Audio mixer used bare `"ffmpeg"`** instead of the bundled-binary
   resolver and raised raw stderr text. Now routes through `run_ffmpeg`.
6. **`voice.py` used bare `"ffprobe"`** — now resolves via `find_ffprobe`.
7. **API Test Connection ran on the UI thread** — moved to a QThread worker.

## GUI checks (static — Qt system libs unavailable in sandbox)

- Window title = `U One — AI Automatic Video Editor` (from `version.__display_name__`)
- Pexels/Pixabay fields use `QLineEdit.Password` echo mode
- Save → `secure_store` (Windows Credential Manager via keyring where
  available), fields cleared, status shows "Saved"
- Test Connection → background thread, Connected/Not Connected, keys never shown
- `_settings()` and project serialization verified to exclude raw keys
- Crash-recovery prompt on launch verified in code; autosave round-trip
  tested headless (T14)

## Not tested (environment limits — stated honestly)

- Real Pexels/Pixabay keys (none available); provider failover paths and
  Test Connection logic tested with empty/bogus keys only.
- `edge-tts` network path (sandbox blocked); `pyttsx3` not installed here.
- Actual Windows 10/11 launch, `U_One_Setup.exe` build, Inno Setup run,
  Desktop/Start Menu shortcuts, uninstaller, bundled-FFmpeg discovery
  via `sys._MEIPASS`, Windows Credential Manager, SAPI voices.
- 1:1 / 1440p / 4K / H.265 / 24/60 fps output combos are configured but
  were not rendered in this battery.
- The Windows installer workflow (Test 15 in the user's list, clean-install
  test) cannot be claimed until run on real Windows hardware.

## Self-test module (2026-09-27) — `U One.exe --selftest`

A built-in end-to-end validator (`src/selftest.py`, invoked with
`--selftest`) was added so the **real Windows PC** can prove the full
pipeline works with one command — no Python, pip, FFmpeg, or API keys
needed. It renders the complete chain
script → scene detection → media search (generated fallback, offline) →
timeline → captions → audio mix → final MP4 in **16:9 and 9:16**, using a
work path containing spaces and parentheses, and validates both MP4s with
the bundled FFprobe. Results print to stdout and are written to
`%LOCALAPPDATA%\U One\selftest\report.txt`.

Run headless on Linux 2026-09-27 (same code path the Windows exe uses):

| Check | Result | Detail |
|---|---|---|
| bundled FFmpeg available | PASS | `check_ffmpeg()` True (import fixed during run) |
| synthetic voiceover created | PASS | 22s WAV, spaces in filename |
| 16:9: MP4 valid | PASS | 1920x1080, video+audio streams, 114s render |
| 9:16: MP4 valid | PASS | 1080x1920, video+audio streams, 117s render |
| hostile path escaping | PASS | `C\:/Users/Gillani Computers/Downloads/my vid (1)/cap\'s\,.ass` |

**5/5 PASS** after the `check_ffmpeg` import fix (it lives in
`src.renderer`, not `src.ffmpeg_util` — fixed and re-verified).

## Windows 10/11 build + install validation — STATUS: NOT PERFORMED HERE

The Linux sandbox has no Windows runtime (no Wine/QEMU; PyInstaller cannot
cross-compile). The following user requirements **cannot be honestly
claimed** until run on a real Windows 10/11 PC:

- `build\windows\build.bat` → `dist\U One\U One.exe`
- Inno Setup compile → `Output\U_One_Setup.exe`
- Silent/per-user install, Start Menu + Desktop shortcuts, Add/Remove
  Programs entry, silent uninstall
- Bundled FFmpeg discovery via `sys._MEIPASS` in the frozen app
- Windows Credential Manager key storage
- Real Pexels/Pixabay API keys (none available in this environment)
- GUI render → MP4 plays → copy lands in the user's Videos folder

Everything needed for that final mile ships in this ZIP and is documented
in `BUILD_WINDOWS.md`:

- `build\windows\build.bat` — one double-click: venv → deps → FFmpeg
  download → PyInstaller → `U_One_Portable.zip` → `U_One_Setup.exe`
  (auto-detects Inno Setup 6)
- `build\windows\validate_install.ps1` — automated install/shortcut/
  launch/`--selftest`/uninstall verification with a manual GUI checklist
- `U One.exe --selftest` — built-in full-pipeline validator described above

---

# U One — Test Report (v1.1.0 engine rebuild)

**Date:** 2026-09-27
**Version:** U One 1.1.0 (11-stage render engine)
**Environment:** Linux sandbox, Python 3.12, FFmpeg 8.1.2 / ffprobe present.
No Pexels/Pixabay API keys; no Windows machine (installer not built here).

## Results — 34/34 PASS (full suite, `python3 -m pytest tests/ -q`)

| # | Test | Result | Detail |
|---|------|--------|--------|
| V1 | Valid MP4 accepted | PASS | Real video+audio MP4 passes full validation |
| V2 | Zero-byte rejected | PASS | Empty file → FATAL, no crash |
| V3 | Corrupt MP4 rejected | PASS | Random bytes → unreadable container |
| V4 | Black MP4 rejected | PASS | Pure black 2s video → "black/blank video" FATAL |
| V5 | One-frame too short rejected | PASS | Below minimum duration |
| V6 | Missing file rejected | PASS | No traceback leak |
| V7 | Missing audio (required) rejected | PASS | |
| V8 | Dimension mismatch = warning | PASS | Not fatal |
| V9 | **Dark title card accepted** | PASS | Navy `#0b1020` card + white text: blackdetect fires but bright-pixel census (1.9%) accepts it as intentional dark visuals |
| V10 | True black still rejected | PASS | No bright pixels → FATAL stands |
| E1 | FFmpeg success path | PASS | |
| E2 | Structured failure | PASS | Stage + exit code + stderr tail + command + hint |
| E3 | Missing input | PASS | |
| E4 | Invalid filter | PASS | |
| E5 | Cancellation | PASS | Child terminated, `RenderCancelled` raised |
| N1–N3 | Normalizer | PASS | Resolution/FPS/pixel-format normalization |
| P1–P3 | Project manager | PASS | Save/load, failure snapshots, render-state |
| X1–X3 | Export engine | PASS | 360p–4K profiles, H.264/H.265 |
| G1–G4 | Scene generator | PASS | Retry 0→1→2, fallback duration-matched to scene |
| **31-scene** | **Mandatory broken-scene reproduction** | PASS | Exact 13 reported broken positions fed black/empty/corrupt/one-frame media → **31/31 scenes valid, 13 recovered**, final MP4 640×360, 92.9s, probes clean |
| R1 | 3-scene offline end-to-end | PASS | All 11 stages observed, 3 valid scenes, mixed audio, valid preview MP4 in ~64s |
| R2 | Regenerate buttons | PASS | `regenerate_visual/text/sfx/music` work on the live engine; locked scenes untouched; regenerated segment re-validated and swapped into concat list |

## Bug found and fixed during this battery
- **Black-detector false positive on generated graphics**: the offline
  graphic generator painted `#0b1020` (luma 6.4% < `pix_th=0.08`), so every
  generated title card validated as "99% black" and fell back to the
  emergency card. Two-part fix:
  1. `scene_validator`: bright-pixel census — >0.5% bright pixels →
     accepted as intentional dark visuals (warning, not fatal).
     Pure-black video (0% bright) is still rejected.
  2. `media_search.GraphicGenerator`: `bbox_inches="tight"` was exploding
     cards to 6861×2035 with microscopic text (0.096% bright — correctly
     rejected even by the fixed validator). Cards now save at exactly
     1280×720 with centered wrapped text (title 1.55%, location 2.64%,
     chart 28.75% bright).
- **Cache-hit scenes dropped their Asset**: `eng.assets` stayed empty, so
  the GUI scene panel / Sources tab saw nothing. The asset is now carried
  through the cache-hit path too.

## Still Windows-only (not claimed)
`dist\U One\U One.exe`, `Output\U_One_Portable.zip`,
`Output\U_One_Setup.exe`, install/launch/uninstall, GUI render on Windows,
real API keys. The new `--selftest` exercises the new engine and should be
run on the user's PC after `build_windows.bat`.
