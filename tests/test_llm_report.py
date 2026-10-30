from pathlib import Path

from pytest import approx

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPlainTextEdit, QPushButton

import step_explorer.llm_report as llm_report_module
import step_explorer.ui.main_window as main_window_module
from step_explorer.geometry.builder import GeometryBuilder
from step_explorer.llm_report import (
    PLAYBACK_STEP_FLOOR_SECONDS,
    analyze_processing_pain, build_llm_report, describe_playback_estimate,
    estimate_playback_duration, format_duration, long_playback_warning,
    preview_health_score,
)
from step_explorer.step.parser import StepDocument, parse_step
from step_explorer.ui.main_window import MainWindow
from step_explorer.ui.object_tree import document_bodies


ROOT = Path(__file__).resolve().parents[1]


def test_degree_angle_cones_and_unequal_periodic_cylinder_rims():
    document = StepDocument.from_file(ROOT / "tests/corpus/subaru_degree_units.step")
    snapshot = GeometryBuilder(document).build()
    assert {mesh.entity_id for mesh in snapshot.faces} == {
        entity.entity_id for entity in document.entities if entity.type_name == "ADVANCED_FACE"
    }


def test_missing_third_of_preview_makes_report_bad():
    source = ROOT / "tests/corpus/subaru_degree_units.step"
    document = StepDocument.from_file(source)
    snapshot = GeometryBuilder(document).build()
    snapshot.faces = snapshot.faces[:16]
    score, deductions = preview_health_score(document, snapshot)
    report = build_llm_report(source, document, snapshot)
    assert score < 80
    assert (33, "8 face(s) lack a preview mesh") in deductions
    assert "[BAD]" in report
    assert "Report button: RED" in report


def test_report_explains_dense_spline_and_slow_face():
    source = ROOT / "tests/corpus/TOUGH_stresstests/sheet_thickened.step"
    document = StepDocument.from_file(source)
    snapshot = GeometryBuilder(document).build()
    score, _deductions = preview_health_score(document, snapshot)
    pain = analyze_processing_pain(document, {5566: 7.033}, {5566: 7.033})
    report = build_llm_report(source, document, snapshot, {5566: 7033.0},
                              {5566: 7.033}, {5566: 7.033})

    assert score == 75
    assert pain.score == 100
    assert any("Face #5566 geometry handler: 7.033 s" in item for item in pain.warnings)
    assert any("Surface #5559: 5,104 control-point references" in item for item in pain.warnings)
    assert "Processing pain: 100/100 (RED)" in report
    assert "Report button: RED" in report
    assert "Preview and topology health: 75/100" in report
    assert str(source) in report
    assert "5,481 CARTESIAN_POINT entities" in report
    assert "#5559: 116 x 44 = 5,104 references" in report
    assert "Faces meshed: 6/6" in report
    assert "BAD: 5; JUDGEMENT CALL: 1" in report
    assert "sliver triangles" in report
    assert "#5566 ADVANCED_FACE: 7,033.00 ms" in report
    assert "including playback delay, UI updates, rendering" in report
    assert "not a CAD/BREP validity certificate" in report


def test_any_slow_face_or_body_turns_pain_red_without_playback_delay():
    document = StepDocument.from_file(ROOT / "tests/corpus/planar_only/multiple_cubes.step")
    first_body = document_bodies(document)[0]
    first_face = next(entity.entity_id for entity in document.entities
                      if entity.type_name == "ADVANCED_FACE" and entity.entity_id in first_body.entity_ids)
    face_pain = analyze_processing_pain(document, {first_face: 2.001}, {first_face: 2.001})
    assert any(f"Face #{first_face}" in item for item in face_pain.warnings)

    first_body_points = [entity.entity_id for entity in document.entities
                         if entity.type_name == "CARTESIAN_POINT"
                         and entity.entity_id in first_body.entity_ids]
    body_pain = analyze_processing_pain(
        document, {entity_id: 61 / len(first_body_points) for entity_id in first_body_points}, {}
    )
    assert any(f"Body #{first_body.entity_id}" in item for item in body_pain.warnings)
    assert len([item for item in body_pain.body_times if item[2] > 60]) == 1


def test_dense_edge_controls_trigger_processing_pain():
    references = ",".join("#1" for _ in range(257))
    document = parse_step(
        "ISO-10303-21;DATA;"
        f"#300=B_SPLINE_CURVE_WITH_KNOTS('',3,({references}));"
        "#301=EDGE_CURVE('',#1,#1,#300,.T.);"
        f"#302=POLYLINE('',({references}));"
        "ENDSEC;END-ISO-10303-21;"
    )
    pain = analyze_processing_pain(document)
    assert any("Edge #301 / curve #300: 257 control points" in item for item in pain.warnings)
    assert any("Polyline #302: 257 Cartesian-point references" in item for item in pain.warnings)


SURFACE_REFERENCE = ("ISO-10303-21;DATA;"
                     "#500=B_SPLINE_SURFACE_WITH_KNOTS('',1,3,{grid},"
                     ".UNSPECIFIED.,.F.,.F.,.U.,(2,2),(4,4),(0.,1.),(0.,1.));"
                     "#501=POLYLINE('',({curve}));"
                     "ENDSEC;END-ISO-10303-21;")


def test_playback_estimate_costs_entity_steps_and_dense_geometry():
    surface_grid = "(" + ",".join(
        "(" + ",".join("#1" for _ in range(44)) + ")" for _ in range(116)
    ) + ")"
    curve_refs = ",".join("#1" for _ in range(257))
    document = parse_step(SURFACE_REFERENCE.format(grid=surface_grid, curve=curve_refs))
    fastest = estimate_playback_duration(document)
    assert fastest.step_cost_seconds == PLAYBACK_STEP_FLOOR_SECONDS
    assert fastest.remaining_entities == 1  # the first entity is already shown
    assert fastest.surface_seconds == approx(5104 * 0.002)
    assert fastest.edge_seconds == approx(257 * 0.002)
    assert fastest.dense_surfaces == 1
    assert fastest.dense_edges == 1
    assert fastest.max_surface_refs == 5104
    assert fastest.max_edge_refs == 257
    assert fastest.seconds == approx(fastest.baseline_seconds + 10.208 + 0.514)

    slowed = estimate_playback_duration(document, interval_ms=100)
    assert slowed.step_cost_seconds == 0.1
    assert slowed.baseline_seconds == approx(0.1)
    assert slowed.seconds == approx(slowed.surface_seconds + slowed.edge_seconds + 0.1)


def test_playback_estimate_description_is_itemized():
    surface_grid = "(" + ",".join(
        "(" + ",".join("#1" for _ in range(44)) + ")" for _ in range(116)
    ) + ")"
    document = parse_step(SURFACE_REFERENCE.format(
        grid=surface_grid, curve=",".join("#1" for _ in range(257))
    ))
    estimate = estimate_playback_duration(document, interval_ms=100)
    text = describe_playback_estimate(estimate, turbo=True)
    assert "Estimated play-through:" in text
    assert "1 entities x 0.10 s per step = 0 s" in text
    assert "largest grid 5,104 points" in text
    assert "dense curve(s); largest uses 257 points" in text
    assert "Turbo batches rendering" in text
    assert "varies by machine" in text


def test_format_duration_progresses_through_units():
    assert format_duration(45.4) == "45 s"
    assert format_duration(59.6) == "1 min"
    assert format_duration(3 * 3600 + 7 * 60) == "3 h 07 min"


def test_long_playback_warning_stays_quiet_below_threshold(monkeypatch):
    document = parse_step(
        "ISO-10303-21;DATA;"
        "#600=B_SPLINE_CURVE_WITH_KNOTS('',3,(#1));"
        "ENDSEC;END-ISO-10303-21;"
    )
    assert long_playback_warning(document) is None
    surface_grid = "(" + ",".join(
        "(" + ",".join("#1" for _ in range(44)) + ")" for _ in range(116)
    ) + ")"
    heavy = parse_step(SURFACE_REFERENCE.format(
        grid=surface_grid, curve=",".join("#1" for _ in range(257))
    ))
    monkeypatch.setattr(llm_report_module, "PLAYBACK_ESTIMATE_WARN_SECONDS", 0.5)
    warning = long_playback_warning(heavy)
    assert warning is not None
    assert "estimated interactive play-through" in warning
    assert "1 entities" in warning


def test_report_button_requires_completed_file_and_copies_report(qapp, monkeypatch):
    window = MainWindow()
    try:
        assert not hasattr(window, "processing_intensity_dock")
        assert all(action.text() != "Processing intensity" for action in window.view_menu.actions())
        assert window.llm_report_button.text() == "Report"
        assert window.llm_report_button.width() == window.play_button.width()
        source = ROOT / "tests/corpus/planar_only/box125.step"
        window.open_path(str(source))
        assert not window.llm_report_button.isEnabled()
        window._set_playback_order(len(window.document.entities) - 1)
        assert window.llm_report_button.isEnabled()
        assert window._report_health_score == 100
        assert window._report_pain.score == 0
        assert "#a9d8b0" in window.llm_report_button.styleSheet()
        window._report_pain = analyze_processing_pain(window.document, {}, {66: 2.001})
        window._update_primary_action_styles(True)
        assert "#c83d3d" in window.llm_report_button.styleSheet()
        window._set_playback_order(0)
        assert window.llm_report_button.isEnabled()  # The full file was already inspected.
        captured = {}

        def inspect_dialog(dialog):
            captured["report"] = dialog.findChild(QPlainTextEdit).toPlainText()
            copy_button = next(button for button in dialog.findChildren(QPushButton)
                               if button.text() == "Copy report")
            copy_button.click()

        monkeypatch.setattr(QDialog, "exec", inspect_dialog)
        window.llm_report_button.click()
        assert "STEPOSCOPE REPORT FOR LLM" in captured["report"]
        assert str(source) in captured["report"]
        assert QApplication.clipboard().text() == captured["report"]
        window.open_path(str(source))
        assert not window.llm_report_button.isEnabled()
    finally:
        window.close()


def test_playback_duration_gate_warns_cancels_and_snoozes(qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        main_window_module, "QSettings",
        lambda *_: QSettings(str(tmp_path / "duration.ini"), QSettings.Format.IniFormat),
    )
    window = MainWindow()
    try:
        source = ROOT / "tests/corpus/planar_only/box125.step"
        window.open_path(str(source))
        key = window._playback_estimate_ack_key()
        window.settings.remove(key)
        window.settings.sync()

        # Estimate far below the installed threshold: no dialog, play starts.
        assert window._confirm_playback_duration(0) is True
        assert window.show_positions_action.isChecked()

        monkeypatch.setattr(main_window_module.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(main_window_module.QMessageBox, "clickedButton",
                            lambda self: self.button(QMessageBox.StandardButton.No))
        window.settings.remove(key)
        monkeypatch.setattr(main_window_module, "PLAYBACK_ESTIMATE_WARN_SECONDS", 60)
        monkeypatch.setattr(llm_report_module, "PLAYBACK_STEP_FLOOR_SECONDS", 5)
        window.play_button.setChecked(True)
        assert not window.play_button.isChecked()
        assert not window.playback_timer.isActive()
        assert window.settings.value(key, False, type=bool) is False
        assert window.show_positions_action.isChecked()

        monkeypatch.setattr(main_window_module.QMessageBox, "exec",
                            lambda self: self.checkBox().setChecked(True))
        monkeypatch.setattr(main_window_module.QMessageBox, "clickedButton",
                            lambda self: self.button(QMessageBox.StandardButton.Yes))
        window.play_button.setChecked(True)
        assert window.play_button.isChecked()
        assert window.playback_timer.isActive()
        assert window.settings.value(key, False, type=bool) is True
        assert not window.show_positions_action.isChecked()
        assert not window.viewport._last_show_points
        assert window.settings.value("show_positions", True, type=bool) is False
        window.play_button.setChecked(False)

        # Once acknowledged for the file, the gate stops asking.
        monkeypatch.setattr(main_window_module.QMessageBox, "exec",
                            lambda self: 1 / 0)  # must never open again
        monkeypatch.setattr(main_window_module.QMessageBox, "clickedButton",
                            lambda self: 1 / 0)
        window.play_button.setChecked(True)
        assert window.play_button.isChecked()
        assert window.playback_timer.isActive()
        window.play_button.setChecked(False)
    finally:
        window.settings.remove(window._playback_estimate_ack_key())
        window.settings.sync()
        window.close()


def test_continue_without_permanent_opt_out_is_remembered_until_next_open(qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(main_window_module, "QSettings",
                        lambda *_: QSettings(str(tmp_path / 'session.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(main_window_module, 'PLAYBACK_ESTIMATE_WARN_SECONDS', -1)
    shown = []
    monkeypatch.setattr(QMessageBox, 'exec', lambda dialog: shown.append(True))
    monkeypatch.setattr(QMessageBox, 'clickedButton', lambda dialog: dialog.button(QMessageBox.StandardButton.Yes))
    window = MainWindow()
    try:
        source = str(ROOT / 'tests/corpus/planar_only/box125.step')
        window.open_path(source)
        assert window._confirm_playback_duration(0)
        assert window._confirm_playback_duration(0)
        assert len(shown) == 1
        assert not window._playback_estimate_acknowledged()
        assert not window.show_positions_action.isChecked()
        assert window.viewport._last_show_points  # Paused preview remains inspectable.
        window.open_path(source)
        assert window._confirm_playback_duration(0)
        assert len(shown) == 2
    finally:
        window.close()
