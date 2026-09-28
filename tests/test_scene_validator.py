"""Media tests: validator must accept good media and reject every
broken kind (valid / zero-byte / corrupt / black / one-frame /
missing-audio / missing file). Mirrors user requirement §27."""
import os
import subprocess

from src.scene_validator import validate_segment


def _m(media_dir, name):
    return os.path.join(media_dir, name)


def test_valid_mp4_accepted(media_dir, engine):
    rep = validate_segment(_m(media_dir, "valid.mp4"), engine=engine,
                           expect_w=640, expect_h=360, min_duration=0.4)
    assert rep.valid, rep.describe()
    assert rep.metrics["decoded_frames"] > 0
    assert rep.metrics["black_ratio"] < 0.1


def test_zero_byte_rejected(media_dir, engine):
    rep = validate_segment(_m(media_dir, "empty.mp4"), engine=engine)
    assert not rep.valid
    assert any("empty" in f for f in rep.fatals)


def test_corrupt_mp4_rejected(media_dir, engine):
    rep = validate_segment(_m(media_dir, "corrupt.mp4"), engine=engine)
    assert not rep.valid
    assert rep.fatals, "corrupt file must produce a fatal"


def test_black_mp4_rejected(media_dir, engine):
    rep = validate_segment(_m(media_dir, "black.mp4"), engine=engine)
    assert not rep.valid
    assert any("black" in f for f in rep.fatals), rep.describe()
    assert rep.metrics["black_ratio"] > 0.9


def test_one_frame_too_short_rejected(media_dir, engine):
    rep = validate_segment(_m(media_dir, "oneframe.mp4"), engine=engine,
                           min_duration=0.4)
    assert not rep.valid
    assert any("duration" in f for f in rep.fatals)


def test_missing_file_rejected(engine, tmp_path):
    rep = validate_segment(str(tmp_path / "nope.mp4"), engine=engine)
    assert not rep.valid
    assert any("not exist" in f for f in rep.fatals)


def test_missing_audio_when_required(media_dir, engine):
    rep = validate_segment(_m(media_dir, "valid.mp4"), engine=engine,
                           require_audio=True)
    assert not rep.valid
    assert any("audio" in f for f in rep.fatals)


def test_dimension_mismatch_is_warning_not_fatal(media_dir, engine):
    rep = validate_segment(_m(media_dir, "valid.mp4"), engine=engine,
                           expect_w=1920, expect_h=1080)
    assert rep.valid, rep.describe()
    assert rep.warnings, "expected a dimension warning"


def test_dark_title_card_accepted(tmp_path, engine):
    """Dark navy card (#0b1020) with white text must NOT be rejected as
    black/blank: it carries bright content. Guards the false positive
    that sent generated title cards to the fallback."""
    from PIL import Image, ImageDraw
    card = str(tmp_path / "card.png")
    img = Image.new("RGB", (640, 360), (11, 16, 32))
    d = ImageDraw.Draw(img)
    d.text((60, 160), "A single bright sentence about the ocean",
           fill=(255, 255, 255))
    img.save(card)
    vid = str(tmp_path / "darkcard.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-loop", "1", "-i", card, "-t", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    vid], check=True, timeout=60)
    rep = validate_segment(vid, engine=engine, expect_w=640, expect_h=360,
                           expect_fps=30.0)
    assert rep.valid, rep.describe()
    assert rep.metrics.get("bright_fraction", 0) > 0.005


def test_true_black_still_rejected(media_dir, engine):
    rep = validate_segment(_m(media_dir, "black.mp4"), engine=engine)
    assert not rep.valid
    assert any("black" in f for f in rep.fatals)
