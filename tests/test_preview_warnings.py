from types import SimpleNamespace
from pathlib import Path

from PySide6.QtWidgets import QApplication

from step_explorer.geometry.builder import Mesh
from step_explorer.geometry.completeness import FacePreviewGap
from step_explorer.ui.main_window import MainWindow


def test_compact_preview_warning_opens_copyable_full_details(qapp, monkeypatch):
    window = MainWindow()
    window.document = object()
    gaps = tuple(
        FacePreviewGap(face_id, "CYLINDRICAL_SURFACE", f"could not triangulate face #{face_id}")
        for face_id in (290, 350, 640, 710)
    )
    monkeypatch.setattr("step_explorer.ui.main_window.find_face_preview_gaps", lambda *_args, **_kwargs: gaps)

    window._update_preview_warnings(SimpleNamespace(faces=[], render_updates=None))

    assert window.preview_warning_button.text() == "⚠ Preview incomplete · 4 faces"
    details = window.preview_warning_details.toPlainText()
    assert all(f"#{gap.face_id}" in details for gap in gaps)
    assert window.details_tabs.isHidden()
    window.preview_warning_button.click()
    assert not window.details_tabs.isHidden()
    window.preview_warning_details.selectAll()
    window.preview_warning_details.copy()
    assert QApplication.clipboard().text() == details

    monkeypatch.setattr("step_explorer.ui.main_window.find_face_preview_gaps", lambda *_args, **_kwargs: ())
    window._update_preview_warnings(SimpleNamespace(faces=[], render_updates=None))
    assert window.preview_warning_button.isHidden()
    assert window.preview_warning_group.isHidden()
    assert window.preview_warning_details.toPlainText() == ""
    window.close()


def test_preview_notes_share_the_compact_warning(qapp, monkeypatch):
    window = MainWindow()
    window.document = object()
    monkeypatch.setattr("step_explorer.ui.main_window.find_face_preview_gaps", lambda *_args, **_kwargs: ())
    face = Mesh(17, (), (), preview_note="trim was approximated")

    window._update_preview_warnings(SimpleNamespace(faces=[face], render_updates=None))

    assert window.preview_warning_button.text() == "⚠ Preview approximation · 1 note"
    assert "#17: trim was approximated" in window.preview_warning_details.toPlainText()
    window.close()


def test_double_click_face_opens_interpretation_details(qapp):
    window = MainWindow()
    example = Path(__file__).resolve().parents[1] / "tests/corpus/planar_only/box125.step"
    window.open_path(str(example))
    window._set_playback_order(len(window.document.entities) - 1)
    face_id = window.current_snapshot.faces[0].entity_id
    assert window.details_tabs.isHidden()

    window.viewport.face_double_clicked.emit(face_id)

    assert not window.details_tabs.isHidden()
    assert window.details_tabs.currentWidget().findChild(type(window.details)) is window.details
    assert window.current_id == face_id
    assert f"Entity #{face_id}" in window.details.toPlainText()
    window.viewport.face_double_clicked.emit(face_id)
    assert not window.details_tabs.isHidden()
    window.close()
