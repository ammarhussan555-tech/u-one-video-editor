"""U One - typed settings (v2).

Single source of truth for every render setting: defaults, types, and
validation. The GUI edits these; the render engine consumes them.
Unknown keys are preserved (forward compatibility) but never trusted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    # script / scenes
    "visual_mode": "every_sentence",      # every_sentence | every_2 | key_moments
    "style_preset": "Documentary",        # Documentary | Fast-paced | News | Calm
    # output
    "output_format": "16:9",              # 16:9 | 9:16 | 1:1
    "output_resolution": "1080p",         # 1080p | 1440p | 4K
    "output_fps": 30,                     # 24 | 30 | 60
    "output_codec": "h264",               # h264 | h265
    "prefer_hwaccel": True,
    # captions / text
    "captions_enabled": True,
    "caption_font_size": 48,
    "caption_highlight": True,
    "caption_template": "spec_highlight",
    "text_overlays": "auto",              # auto | off
    # media
    "media_preference": "ai_auto",        # ai_auto | video_only | image_only
    "repetition_limit": "never",          # never | allow_once
    "music_dir": "",
    "safe_mode": False,                   # only copyright-safe providers
    "auto_clean_logos": True,             # crop/blur logos on risky clips
    # voice
    "voice_upload": "",
    "voice_generate_text": "",
    "voice_name": "en-US-AriaNeural",
}

_VALID = {
    "output_format": {"16:9", "9:16", "1:1"},
    "output_resolution": {"1080p", "1440p", "4K"},
    "output_fps": {24, 30, 60},
    "output_codec": {"h264", "h265"},
    "visual_mode": {"every_sentence", "every_2", "every_3", "ai_auto"},
    "text_overlays": {"auto", "off"},
}


@dataclass
class Settings:
    values: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Settings":
        merged = dict(DEFAULTS)
        for k, v in (d or {}).items():
            merged[k] = v
        return cls(merged)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def to_dict(self) -> Dict[str, Any]:
        # never persist raw API keys
        return {k: v for k, v in self.values.items()
                if "key" not in k.lower()}

    def validate(self) -> list:
        """Return a list of problems (empty = valid)."""
        problems = []
        for key, allowed in _VALID.items():
            v = self.values.get(key, DEFAULTS[key])
            if v not in allowed:
                problems.append(
                    f"Setting '{key}' has invalid value {v!r}; "
                    f"allowed: {sorted(allowed)}")
        fps = self.values.get("output_fps", 30)
        if not isinstance(fps, int) or fps not in (24, 30, 60):
            problems.append(f"Invalid fps: {fps!r}")
        return problems

    # -- typed accessors used by the render engine --
    @property
    def resolution(self):
        from .export_engine import RESOLUTIONS
        fmt, res = self.get("output_format"), self.get("output_resolution")
        return RESOLUTIONS.get(fmt, RESOLUTIONS["16:9"]).get(res, (1920, 1080))

    @property
    def fps(self) -> int:
        return int(self.get("output_fps", 30))
