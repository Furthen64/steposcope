from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage, QColor

from step_explorer.ui.main_window import MainWindow
from step_explorer.visualization.vtk_view import vtk


def test_capture_reads_rendered_frame_as_png_without_overlays(view):
    render_window = vtk.vtkRenderWindow()
    render_window.SetOffScreenRendering(1)
    render_window.SetSize(120, 80)
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(1, 0, 0)
    render_window.AddRenderer(renderer)
    render_window.Render()
    real_widget = view.widget
    view.widget = SimpleNamespace(GetRenderWindow=lambda: render_window)
    view._initialized = True
    try:
        image = view.capture_frame()
        assert image.width() == 120
        assert image.height() == 80
        assert image.pixelColor(60, 40) == QColor('red')
    finally:
        view._initialized = False
        view.widget = real_widget
        render_window.Finalize()


def test_resume_preview_is_static_resizes_and_clears_on_pause_or_open(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr('step_explorer.ui.main_window.QSettings',
                        lambda *_: QSettings(str(tmp_path / 'preview.ini'), QSettings.Format.IniFormat))
    source = tmp_path / 'points.step'
    source.write_text("ISO-10303-21;DATA;#1=CARTESIAN_POINT('',(0.,0.,0.));"
                      "#2=CARTESIAN_POINT('',(1.,0.,0.));#3=CARTESIAN_POINT('',(2.,0.,0.));ENDSEC;END-ISO-10303-21;")
    window = MainWindow()
    try:
        window.open_path(str(source))
        canvas = window.viewport.widget
        canvas.resize(1000, 600)
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        image.fill(QColor('red'))
        captures = []

        def capture():
            captures.append(True)
            return image

        monkeypatch.setattr(window.viewport, 'capture_frame', capture)
        window.play_button.setChecked(True)
        assert not window.paused_preview_panel.isHidden()
        assert window.paused_preview_caption.text() == 'Last paused view · step 1'
        panel = window.paused_preview_panel.geometry()
        assert panel.right() == 985
        assert panel.bottom() == 585
        assert panel.width() * panel.height() / (1000 * 600) == pytest.approx(0.15, abs=0.01)
        saved = window._paused_preview_pixmap.cacheKey()
        window._playback_tick()
        assert window._paused_preview_pixmap.cacheKey() == saved
        assert len(captures) == 1
        canvas.resize(800, 500)
        window._position_canvas_overlays()
        assert window.paused_preview_panel.geometry().right() == 785
        window.play_button.setChecked(False)
        assert window.paused_preview_panel.isHidden()
        image.fill(QColor('blue'))
        window.play_button.setChecked(True)
        assert len(captures) == 2
        assert window._paused_preview_pixmap.toImage().pixelColor(0, 0) == QColor('blue')
        window.open_path(str(source))
        assert window.paused_preview_panel.isHidden()
        assert window._paused_preview_pixmap is None
    finally:
        window.close()
