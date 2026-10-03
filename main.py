"""U One - AI Automatic Video Editor. Entry point: GUI by default, headless pipeline with --cli."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from src.version import __version__  # noqa: E402


def run_cli(args):
    import yaml
    from src.project import Project
    from src.project_manager import ProjectManager
    from src.render_engine import RenderEngine, STAGES
    from src.settings import Settings
    from src import app_paths

    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        file_settings = yaml.safe_load(f) or {}
    with open(args.script, encoding="utf-8") as f:
        script = f.read()

    proj = Project(name=args.title or "CLI Project")
    proj.script = script
    work = args.workdir or str(app_paths.work_root() / "cli")
    os.makedirs(work, exist_ok=True)
    settings = Settings({
        "visual_mode": "every_sentence",
        "media_preference": "ai_auto",
        "repetition_limit": "never",
        "text_overlays": "auto",
        "captions_enabled": True,
        "voice_upload": args.voice or "",
        "voice_generate_text": "" if args.voice else script,
        "voice_name": "en-US-AriaNeural",
        "output_format": args.format, "output_resolution": args.res,
        "output_fps": args.fps, "output_codec": "h264",
        "music_dir": "",
        # API keys: env vars and secure OS storage take precedence
        "api_keys": (file_settings.get("api_keys") or {}),
    })
    pm = ProjectManager(proj, os.path.dirname(work))
    eng = RenderEngine(pm, work, settings,
                       progress_cb=lambda i, t, s: print(f"[{i+1}/{len(STAGES)}] {t}: {s}"),
                       log_cb=print)
    out = eng.run(preview=False)
    print("DONE:", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true", help="headless run")
    ap.add_argument("--script", default="", help="script .txt (cli)")
    ap.add_argument("--voice", default="", help="voiceover audio (cli)")
    ap.add_argument("--title", default="", help="project title (cli)")
    ap.add_argument("--workdir", default="", help="working directory (cli)")
    ap.add_argument("--format", default="16:9", choices=["16:9", "9:16", "1:1"],
                    help="aspect ratio (cli)")
    ap.add_argument("--res", default="1080p", choices=["1080p", "1440p", "4K"],
                    help="resolution (cli)")
    ap.add_argument("--fps", type=int, default=30, help="frame rate (cli)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in end-to-end validation "
                         "(script+voice -> scenes -> visuals -> captions -> "
                         "audio mix -> MP4, 16:9 and 9:16) and write a report")
    args = ap.parse_args()
    if args.selftest:
        from src.selftest import run_selftest
        ok = run_selftest()
        sys.exit(0 if ok else 1)
    if args.cli:
        if not args.script:
            print("usage: python main.py --cli --script script.txt [--voice voice.mp3]")
            sys.exit(2)
        run_cli(args)
        return
    from PySide6.QtWidgets import QApplication, QMessageBox
    from src.ui.main_window import MainWindow
    from src.renderer import check_ffmpeg
    app = QApplication(sys.argv)
    app.setApplicationName("U One")
    app.setOrganizationName("U One")
    app.setApplicationVersion(__version__)
    app.setApplicationDisplayName("U One — AI Automatic Video Editor")
    # Bundled OFL caption fonts (Anton, Bebas Neue, ...): register with Qt
    # so the preview overlay renders the SAME font the burn uses (ffmpeg
    # gets them via fontsdir in the subtitles filter). Without this the
    # preview fell back to a system font while the burn used Anton --
    # "font sahi nahi lagta" (Uzair 2026-10-03).
    try:
        from PySide6.QtGui import QFontDatabase
        from src.app_paths import fonts_dir as _fonts_dir
        _fd = _fonts_dir()
        if _fd:
            for _ttf in sorted(_fd.glob("*.ttf")):
                QFontDatabase.addApplicationFont(str(_ttf))
    except Exception:  # noqa: BLE001
        pass
    win = MainWindow()
    win.show()
    if not check_ffmpeg():
        QMessageBox.warning(win, "FFmpeg not found",
            "FFmpeg was not found.\n\nRendering needs FFmpeg.\n"
            "U One normally bundles FFmpeg with its installer - please reinstall U One.\n\n"
            "You can still write scripts and build timelines; rendering stays disabled.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
