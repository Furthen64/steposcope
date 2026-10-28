from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox

from step_explorer.step.parser import parse_step
from step_explorer.ui.entity_tree import EntityTree
from step_explorer.ui.main_window import MainWindow


def _document():
    return parse_step(
        "ISO-10303-21;\nDATA;\n"
        "#20=CARTESIAN_POINT('',(0.,0.,0.));\n"
        "#3=APPROVAL_STATUS('ok');\n"
        "#11=VERTEX_POINT('',#20);\n"
        "ENDSEC;\nEND-ISO-10303-21;\n"
    )


def _face_document():
    return parse_step(
        "ISO-10303-21;\nDATA;\n"
        "#20=CARTESIAN_POINT('',(0.,0.,0.));\n"
        "#42=ADVANCED_FACE('',(),#20,.T.);\n"
        "ENDSEC;\nEND-ISO-10303-21;\n"
    )


def test_turbo_keeps_paused_and_early_point_only_preview_visible(qapp):
    window = MainWindow()
    window.fast_preview_checkbox.setChecked(False)
    source = Path(__file__).resolve().parents[1] / "tests/corpus/planar_only/multiple_cubes.step"
    window.open_path(str(source))
    assert window.current_snapshot.points
    assert window.viewport._last_show_points

    window.viewport.show_snapshot = Mock(wraps=window.viewport.show_snapshot)
    window.fast_preview_checkbox.setChecked(True)
    window.viewport.show_snapshot.assert_not_called()
    assert window.viewport._last_show_points
    assert not window.viewport._last_fast_preview

    window.play_button.setChecked(True)
    window._show_snapshot(window.current_snapshot, window.current_id, update_surface_controls=False)
    assert window.viewport._last_show_points
    assert window.viewport._last_fast_preview

    first_face = next(entity for entity in window.document.entities if entity.type_name == "ADVANCED_FACE")
    window._set_playback_order(first_face.order)
    assert window.current_snapshot.faces
    assert not window.viewport._last_show_points
    window.play_button.setChecked(False)
    window.close()


def test_playback_listing_shows_every_record_in_file_order(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    tree = EntityTree()
    tree.set_document(_document(), "Playback", geometry_only=True)

    assert [tree.topLevelItem(index).text(0) for index in range(tree.topLevelItemCount())] == [
        "3", "4", "5",
    ]
    assert tree.headerItem().text(0) == "Line"
    assert tree.headerItem().text(1) == "Type"
    assert tree.headerItem().text(2) == "STEP source"
    assert tree.headerItem().text(3) == "Step (ms)"
    assert tree.topLevelItem(1).text(2) == "#3=APPROVAL_STATUS('ok');"
    tree.focus_playback_entity(11)
    assert tree.currentItem().text(0) == "5"
    tree.close()
    assert app is not None


def test_geometry_filter_keeps_spline_entities_and_skips_units(qapp):
    document = parse_step(
        "ISO-10303-21;DATA;"
        "#1=B_SPLINE_SURFACE_WITH_KNOTS();"
        "#2=(B_SPLINE_SURFACE() RATIONAL_B_SPLINE_SURFACE());"
        "#3=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));"
        "ENDSEC;END-ISO-10303-21;"
    )
    tree = EntityTree()
    tree.set_document(document, "File order", geometry_only=True)
    assert tree.visible_entity_ids == [1, 2]
    tree.close()


def test_tree_refresh_restores_current_row_highlight(qapp):
    document = _document()
    tree = EntityTree()
    mode = QComboBox()
    mode.addItems(["Playback", "File order"])
    mode.setCurrentText("File order")
    window = SimpleNamespace(
        document=document,
        tree=tree,
        mode=mode,
        current_id=11,
        geometry_only_action=SimpleNamespace(isChecked=Mock(return_value=False)),
        select_entity=Mock(),
    )
    MainWindow._refresh_tree(window)
    assert tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == 11
    mode.setCurrentText("Playback")
    MainWindow._refresh_tree(window)
    assert tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == 11
    window.select_entity.assert_not_called()
    tree.close()
    mode.close()


def test_play_switches_to_playback_listing(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    mode = QComboBox()
    mode.addItems(["File order", "Semantic", "Playback"])
    mode.setCurrentText("Semantic")
    tree = SimpleNamespace(focus_playback_entity=Mock())
    window = SimpleNamespace(
        document=_document(), playback_order=0, mode=mode, tree=tree,
        playback_speed=100,
        playback_timer=SimpleNamespace(start=Mock()),
        play_button=SimpleNamespace(setText=Mock()),
    )

    MainWindow._playback_toggled(window, True)

    assert mode.currentText() == "Playback"
    tree.focus_playback_entity.assert_called_once_with(20)
    window.playback_timer.start.assert_called_once()
    mode.close()
    assert app is not None


def test_clicking_playback_record_pauses_and_seeks(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    mode = QComboBox()
    mode.addItems(["File order", "Semantic", "Playback"])
    mode.setCurrentText("Playback")
    window = SimpleNamespace(
        document=_document(), mode=mode,
        play_button=SimpleNamespace(setChecked=Mock()),
        _set_playback_order=Mock(),
    )

    MainWindow._tree_entity_selected(window, 11)

    window.play_button.setChecked.assert_called_once_with(False)
    window._set_playback_order.assert_called_once_with(2)
    mode.close()
    assert app is not None


def test_playback_timing_includes_work_between_steps(monkeypatch):
    monkeypatch.setattr("step_explorer.ui.main_window.monotonic", lambda: 10.8)
    tree = SimpleNamespace(set_playback_step_time=Mock())
    window = SimpleNamespace(
        document=_document(), playback_order=0, tree=tree,
        _last_playback_step_finished=10.0,
        play_button=SimpleNamespace(setChecked=Mock()),
        fast_preview_checkbox=SimpleNamespace(isChecked=Mock(return_value=False)),
    )

    def advance(order):
        window.playback_order = order

    window._set_playback_order = advance
    MainWindow._playback_tick(window)

    entity_id, elapsed_ms = tree.set_playback_step_time.call_args.args
    assert entity_id == 3
    assert round(elapsed_ms) == 800
    assert window._last_playback_step_finished == 10.8


def test_playback_timing_survives_mode_changes_and_resets_for_new_document(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    tree = EntityTree()
    document = _document()
    tree.set_document(document, "Playback")
    tree.set_playback_step_time(3, 800.125)
    assert tree.playback_items[3].text(3) == "800.12 ⚠"
    tree.set_document(document, "File order")
    assert tree.columnCount() == 2
    tree.set_document(document, "Playback")
    assert tree.playback_items[3].text(3) == "800.12 ⚠"
    tree.set_document(_document(), "Playback")
    assert tree.playback_items[3].text(3) == ""
    tree.close()
    assert app is not None


def test_playback_type_column_and_independent_appearance_options(qapp):
    tree = EntityTree()
    tree.set_document(_document(), "Playback")
    point = tree.playback_items[20]
    assert point.text(1) == "●"
    assert point.toolTip(1) == "CARTESIAN_POINT"
    assert not tree.isColumnHidden(1)
    assert point.foreground(1).color().name() == "#4fc3f7"
    metadata = tree.playback_items[3]
    assert metadata.text(1) == ""
    assert metadata.foreground(2).color().name() == "#90a4ae"

    tree.set_step_line_appearance("Soft", False, True)
    assert tree.isColumnHidden(1)
    assert point.foreground(1).color().name() == "#a6d7e8"
    assert point.foreground(2).color().name() == "#a6d7e8"
    assert metadata.foreground(2).color().name() == "#b5c0c9"
    tree.set_document(_document(), "File order")
    assert not tree.isColumnHidden(1)
    tree.set_document(_document(), "Playback")
    assert tree.isColumnHidden(1)
    point = tree.playback_items[20]

    tree.set_step_line_appearance("High contrast", True, False)
    assert not tree.isColumnHidden(1)
    assert point.text(1) == "●"
    assert point.foreground(1).color().name() == "#59e1ff"
    assert point.foreground(2).style() == Qt.BrushStyle.NoBrush
    tree.close()


def test_analyzable_face_has_distinct_marker_and_double_click_signal(qapp):
    tree = EntityTree()
    document = _face_document()
    tree.set_document(document, "Playback")
    face = tree.playback_items[42]
    assert face.text(1) == "F"
    assert face.font(1).pointSize() == 16
    assert face.font(1).bold()
    assert "double-click to toggle face analysis" in face.toolTip(1)
    requested = []
    tree.face_analysis_requested.connect(requested.append)

    tree._double_clicked(tree.playback_items[20], 1)
    assert requested == []
    tree._double_clicked(face, 2)
    assert requested == [42]

    tree.set_document(document, "File order")
    tree._double_clicked(tree.topLevelItem(1), 1)
    assert requested == [42]
    tree.close()


def test_playback_face_request_starts_analysis_and_opens_sidebar(qapp):
    document = _face_document()
    mode = QComboBox()
    mode.addItems(["File order", "Playback"])
    mode.setCurrentText("Playback")
    window = SimpleNamespace(
        document=document,
        mode=mode,
        current_id=None,
        discovered_order=-1,
        analyzed_face_id=None,
        play_button=SimpleNamespace(setChecked=Mock()),
        details_tabs=SimpleNamespace(isHidden=Mock(return_value=True)),
        _set_playback_order=Mock(),
        _analyze_current_face=Mock(),
        _exit_face_analysis=Mock(),
        _toggle_interpretation_panel=Mock(),
    )

    MainWindow._tree_face_analysis_requested(window, 20)
    window._analyze_current_face.assert_not_called()
    MainWindow._tree_face_analysis_requested(window, 42)
    window.play_button.setChecked.assert_called_once_with(False)
    window._set_playback_order.assert_called_once_with(document.entity(42).order)
    window._analyze_current_face.assert_called_once()
    window._exit_face_analysis.assert_not_called()
    window._toggle_interpretation_panel.assert_called_once()

    window.current_id = 42
    window.discovered_order = document.entity(42).order
    window.analyzed_face_id = 42
    window._analyze_current_face.reset_mock()
    window._toggle_interpretation_panel.reset_mock()
    window._set_playback_order.reset_mock()
    MainWindow._tree_face_analysis_requested(window, 42)
    window._exit_face_analysis.assert_called_once()
    window._analyze_current_face.assert_not_called()
    window._toggle_interpretation_panel.assert_not_called()
    window._set_playback_order.assert_not_called()

    window.analyzed_face_id = 99
    window._exit_face_analysis.reset_mock()
    MainWindow._tree_face_analysis_requested(window, 42)
    window._analyze_current_face.assert_called_once()
    window._exit_face_analysis.assert_not_called()
    mode.close()
