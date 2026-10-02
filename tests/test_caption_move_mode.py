"""Caption move-mode on the small preview: event-routing regression tests.

Root cause (2026-10-02): ``_toggle_cap_move_mode`` used to flip the
caption overlay to mouse-opaque in move mode. Qt then delivered presses
to the child overlay instead of the video widget, so the event-filter
drag path -- the only input path that works reliably over QVideoWidget's
native video surface on the small embedded preview -- never fired (its
``obj is self.video_widget`` check failed), while the overlay's own
handlers don't reliably receive events on Windows' native surface.
Result: dragging the caption did nothing on the small preview (it
happened to work in full screen).

The overlay must therefore stay permanently mouse-transparent so ALL
move-mode input flows through the video widget's event filter -- the
same path that already delivers video clicks on the small preview
(click-to-edit-caption). These tests pin that behavior with synthetic
events on the offscreen platform.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent, QPointF, QPoint  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QLabel  # noqa: E402

from src.ui.main_window import MainWindow, _DragCaptionLabel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _harness():
    """Minimal MainWindow stand-in exposing the real methods under test."""
    win = MainWindow.__new__(MainWindow)
    win._cap_move_mode = False
    win._cap_drag_start = None
    win._cap_pos = {"dx": 0.0, "dy": 0.0, "scale": 1.0}
    win.video_widget = QWidget()  # stand-in: filter only needs QWidget API
    win.video_widget.resize(640, 360)
    win.video_hint = QLabel()
    win._edit_session = None
    win.caption_overlay = _DragCaptionLabel(win, win.video_widget)
    # Same as _ensure_caption_overlay(): permanent transparency.
    win.caption_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
    win._log = lambda *a, **k: None
    win._apply_calls = []
    win._apply_cap_pos_to_overlay = lambda: win._apply_calls.append(
        dict(win._cap_pos))
    return win


def _press(x, y):
    p = QPointF(x, y)
    return QMouseEvent(QEvent.MouseButtonPress, p, p, Qt.LeftButton,
                       Qt.LeftButton, Qt.NoModifier)


def _move(x, y):
    p = QPointF(x, y)
    return QMouseEvent(QEvent.MouseMove, p, p, Qt.LeftButton,
                       Qt.LeftButton, Qt.NoModifier)


def _release(x, y):
    p = QPointF(x, y)
    return QMouseEvent(QEvent.MouseButtonRelease, p, p, Qt.LeftButton,
                       Qt.NoButton, Qt.NoModifier)


def test_move_mode_keeps_overlay_transparent(app):
    """REGRESSION: toggling move mode must NOT make the overlay
    mouse-opaque -- that stole hit-testing from the video widget and
    starved the event-filter drag path on the small preview."""
    win = _harness()
    win._toggle_cap_move_mode(True)
    assert win._cap_move_mode is True
    assert win.caption_overlay.testAttribute(
        Qt.WA_TransparentForMouseEvents) is True
    win._toggle_cap_move_mode(False)
    assert win._cap_move_mode is False
    assert win.caption_overlay.testAttribute(
        Qt.WA_TransparentForMouseEvents) is True


def test_drag_moves_caption_through_event_filter(app):
    """Synthetic press/move/release on the video widget updates _cap_pos
    and refreshes the overlay -- the small-preview move path."""
    win = _harness()
    win._toggle_cap_move_mode(True)
    vw = win.video_widget

    assert win.eventFilter(vw, _press(100, 100)) is True
    assert win._cap_drag_start is not None
    assert QWidget.mouseGrabber() is vw

    n_calls = len(win._apply_calls)
    assert win.eventFilter(vw, _move(140, 60)) is True
    # dx = +40/640 px, dy = -40/360 px of the widget size.
    assert win._cap_pos["dx"] == pytest.approx(40 / 640)
    assert win._cap_pos["dy"] == pytest.approx(-40 / 360)
    assert len(win._apply_calls) == n_calls + 1

    assert win.eventFilter(vw, _release(140, 60)) is True
    assert win._cap_drag_start is None
    assert QWidget.mouseGrabber() is None


def test_drag_clamps_to_limits(app):
    """Huge drags clamp dx/dy to [-0.45, 0.45]."""
    win = _harness()
    win._toggle_cap_move_mode(True)
    vw = win.video_widget
    win.eventFilter(vw, _press(10, 10))
    win.eventFilter(vw, _move(630, 350))
    assert win._cap_pos["dx"] == pytest.approx(0.45)
    assert win._cap_pos["dy"] == pytest.approx(0.45)
    win.eventFilter(vw, _release(630, 350))


def test_wheel_rescales_and_dblclick_resets(app):
    win = _harness()
    win._toggle_cap_move_mode(True)
    vw = win.video_widget

    wheel = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0),
                        QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    assert win.eventFilter(vw, wheel) is True
    assert win._cap_pos["scale"] == pytest.approx(1.1)

    win._cap_pos.update(dx=0.2, dy=-0.1, scale=1.5)
    dbl = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(100, 100),
                      QPointF(100, 100), Qt.LeftButton, Qt.LeftButton,
                      Qt.NoModifier)
    assert win.eventFilter(vw, dbl) is True
    assert win._cap_pos == {"dx": 0.0, "dy": 0.0, "scale": 1.0}


def test_non_move_mode_click_keeps_editor_path(app):
    """Outside move mode a press still takes the caption-editor branch
    (no drag state), i.e. normal click behavior is preserved."""
    win = _harness()
    vw = win.video_widget
    assert win.eventFilter(vw, _press(100, 100)) is True
    assert win._cap_drag_start is None
    assert win._cap_pos == {"dx": 0.0, "dy": 0.0, "scale": 1.0}
