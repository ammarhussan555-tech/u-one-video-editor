# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for U One - AI Automatic Video Editor.

Run on Windows:  build.bat   (or: pyinstaller build\\windows\\u_one.spec)

Bundles: Python runtime + all pip dependencies + FFmpeg/FFprobe binaries
+ the application icon. The user installs nothing else.
"""
import os
from pathlib import Path

ROOT = Path(os.path.abspath(SPECPATH)).parent.parent  # -> project root
ASSETS = ROOT / "assets"

# FFmpeg binaries are expected at build\\windows\\ffmpeg\\bin\\ (ffmpeg.exe,
# ffprobe.exe). build.bat downloads them automatically if missing.
FFMPEG_DIR = Path(os.path.abspath(SPECPATH)) / "ffmpeg" / "bin"

# Win32 VERSIONINFO resource file (absolute path: independent of CWD).
VERSION_FILE = Path(os.path.abspath(SPECPATH)) / "version.txt"

binaries = []
for exe in ("ffmpeg.exe", "ffprobe.exe"):
    p = FFMPEG_DIR / exe
    if p.is_file():
        binaries.append((str(p), "ffmpeg"))

datas = [
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / "README.txt"), "."),
    (str(ASSETS / "fonts"), "assets/fonts"),
    (str(ASSETS / "sfx"), "assets/sfx"),
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "matplotlib", "matplotlib.pyplot",
        "PIL", "PIL.Image", "PIL.ImageDraw",
        "edge_tts", "pyttsx3",
        "keyring", "keyring.backends.Windows",
        # U One v2 engine (some are imported lazily inside functions;
        # listing them keeps the frozen build honest)
        "src.ffmpeg_engine", "src.media_probe", "src.scene_validator",
        "src.media_normalizer", "src.scene_generator", "src.render_engine",
        "src.export_engine", "src.qc_engine", "src.project_manager",
        "src.settings", "src.cache_manager", "src.temp_manager",
        "src.log_system", "src.error_recovery", "src.script_engine",
        "src.voiceover", "src.timeline_engine", "src.audio_mixer",
        # yt-dlp is imported lazily inside functions; listing it keeps the
        # frozen build honest so YouTubeProvider works in the installer
        "yt_dlp",
        "src.text_engine",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="U One",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # windowed app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "u_one.ico"),
    version=str(VERSION_FILE),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="U One",                # -> dist\U One\U One.exe (matches installer)
)
