from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QPushButton

from step_explorer.geometry.builder import GeometryBuilder
from step_explorer.llm_report import analyze_processing_pain, build_llm_report, preview_health_score
from step_explorer.step.parser import StepDocument, parse_step
from step_explorer.ui.main_window import MainWindow
from step_explorer.ui.object_tree import document_bodies


ROOT = Path(__file__).resolve().parents[1]


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
