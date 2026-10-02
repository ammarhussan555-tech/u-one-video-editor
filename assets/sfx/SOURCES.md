# Bundled SFX — sources & licenses

All files in this directory are **public domain or CC0** and may be
redistributed inside the U One Windows installer (verified per-file on
2026-10-02 via the Wikimedia Commons API `extmetadata` license field).

Normalized at bundle time: 44.1 kHz mono 16-bit WAV, leading silence trimmed
(≤50 ms), peak-normalized. Total: ~0.9 MB.

| File | Source file (Wikimedia Commons) | License | Used as |
|---|---|---|---|
| `camera_real.wav` | [File:Holga shuttersound.ogg](https://commons.wikimedia.org/wiki/File:Holga_shuttersound.ogg) | Public Domain | `camera` — photo/archival image lands |
| `crowd_real.wav` | [File:Applause i.ogg](https://commons.wikimedia.org/wiki/File:Applause_i.ogg) | Public Domain | `crowd` — crowd/protest/rally visual, applause beats |
| `siren_real.wav` | [File:Toy siren alarm.ogg](https://commons.wikimedia.org/wiki/File:Toy_siren_alarm.ogg) | Public Domain | `alert` — emergency/alert visual |
| `boom_real.wav` | [File:Explosion 10.ogg](https://commons.wikimedia.org/wiki/File:Explosion_10.ogg) | Public Domain | `boom` — explosion visual, big reveal |
| `ding_real.wav` | [File:Zajeci zvony kostel 2025.ogg](https://commons.wikimedia.org/wiki/File:Zajeci_zvony_kostel_2025.ogg) | CC0 | `ding` — key metric + held zoom, success beat |

SFX with **no suitable PD/CC0 file found** (2026-10-02) keep the built-in
numpy synthesis in `src/audio_design.py` as the source — synthesis is also
the automatic fallback if a bundled file is missing:
`whoosh`, `swoosh`, `impact`, `riser`, `thunder`, `wind`, `digital`,
`pop`, `tick`, `sparkle`, `punch`, `glitch`.

## Deliberately NOT bundled (license forbids redistribution)

Mixkit (SFX Free License: no redistribution "in a tool"), Pixabay Content
License (no standalone distribution), Sonniss GDC bundle (custom EULA: may
not ship as a library), ZapSplat free tier (attribution required +
no redistribution), YouTube Audio Library (redistribution unverified),
BBC ReMArc (non-commercial). BVKER CC0 pack and Kenney Interface Sounds
(CC0) were unreachable from this network (Cloudflare / JS download wall) —
skipped, not bundled.
