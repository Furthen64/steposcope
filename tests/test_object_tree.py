from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from step_explorer.geometry.builder import GeometryBuilder
from step_explorer.step.parser import StepDocument, parse_step
from step_explorer.ui.main_window import MainWindow
from step_explorer.ui.object_tree import ObjectTree, document_bodies, hidden_entities


EXAMPLE = Path(__file__).resolve().parents[1] / "tests/corpus/planar_only/nonmanifoldAB.step"


def test_nonmanifold_bodies_have_step_names_and_separate_geometry():
    document = StepDocument.from_file(EXAMPLE)
    bodies = document_bodies(document)

    assert [(body.entity_id, body.name) for body in bodies] == [
        (176, "nonmanifold_A"), (327, "nonmanifold_B")
    ]
    assert 66 in bodies[0].entity_ids
    assert 217 not in bodies[0].entity_ids
    assert 217 in bodies[1].entity_ids

    snapshot = GeometryBuilder(document).build()
    hidden = hidden_entities(bodies, {176})
    visible = MainWindow._visible_snapshot(snapshot, hidden)
    assert {face.entity_id for face in visible.faces} == {217, 248, 279, 301, 313, 325}
    assert all(item.entity_id not in hidden for item in visible.polylines)
    assert len(visible.points) < len(snapshot.points)


def test_object_tree_checkboxes_restore_visibility(qapp):
    document = StepDocument.from_file(EXAMPLE)
    tree = ObjectTree()
    tree.set_document(document)
    assert tree.topLevelItemCount() == 2
    assert tree.hidden_entity_ids() == set()

    tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert 66 in tree.hidden_entity_ids()
    assert 217 not in tree.hidden_entity_ids()

    tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    assert tree.hidden_entity_ids() == set()
    tree.close()


def test_object_tree_double_click_event_isolates_body(qapp):
    tree = ObjectTree()
    tree.set_document(StepDocument.from_file(EXAMPLE))
    tree.show()
    qapp.processEvents()

    item = tree.topLevelItem(1)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=tree.visualItemRect(item).center())
    QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=tree.visualItemRect(item).center())

    assert tree.topLevelItem(0).checkState(0) == Qt.CheckState.Unchecked
    assert item.checkState(0) == Qt.CheckState.Checked
    tree.close()


def test_main_window_checkbox_filters_viewport_and_restores_it(qapp):
    window = MainWindow()
    window.open_path(str(EXAMPLE))
    window.select_entity(327)
    window.viewport.show_snapshot = Mock()

    window.object_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    shown = window.viewport.show_snapshot.call_args.args[0]
    assert {face.entity_id for face in shown.faces} == {217, 248, 279, 301, 313, 325}
    assert len(window.current_snapshot.faces) == 12

    window.object_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    shown = window.viewport.show_snapshot.call_args.args[0]
    assert len(shown.faces) == 12
    window.close()


def test_single_body_uses_one_compact_row(qapp):
    window = MainWindow()
    example = Path(__file__).resolve().parents[1] / "tests/corpus/planar_only/box125.step"
    window.open_path(str(example))
    window.show()
    qapp.processEvents()

    assert len(window.object_tree.bodies) == 1
    assert window.objects_group.isVisible()
    assert window.objects_group.height() <= 42
    assert window.show_all_bodies_button.isHidden()
    assert window.object_tree.topLevelItem(0).checkState(0) == Qt.CheckState.Checked

    window.object_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert window.object_tree.bodies[0].entity_id in window.object_tree.hidden_entity_ids()

    window.open_path(str(EXAMPLE))
    qapp.processEvents()
    assert len(window.object_tree.bodies) == 2
    assert window.objects_group.title() == "Bodies"
    assert window.show_all_bodies_button.isVisible()
    assert window.objects_group.height() > 42
    window.close()


def test_double_click_shows_only_chosen_body_and_button_shows_all(qapp):
    window = MainWindow()
    window.open_path(str(EXAMPLE))
    window.select_entity(327)
    window.viewport.show_snapshot = Mock()
    first = window.object_tree.topLevelItem(0)
    second = window.object_tree.topLevelItem(1)

    window.object_tree.itemDoubleClicked.emit(second, 0)
    assert first.checkState(0) == Qt.CheckState.Unchecked
    assert second.checkState(0) == Qt.CheckState.Checked
    assert {face.entity_id for face in window.viewport.show_snapshot.call_args.args[0].faces} == {
        217, 248, 279, 301, 313, 325
    }

    window.object_tree.itemDoubleClicked.emit(first, 0)
    assert first.checkState(0) == Qt.CheckState.Checked
    assert second.checkState(0) == Qt.CheckState.Unchecked
    assert {face.entity_id for face in window.viewport.show_snapshot.call_args.args[0].faces} == {
        66, 97, 128, 150, 162, 174
    }

    window.show_all_bodies_button.click()
    assert first.checkState(0) == Qt.CheckState.Checked
    assert second.checkState(0) == Qt.CheckState.Checked
    assert len(window.viewport.show_snapshot.call_args.args[0].faces) == 12
    assert window.viewport.show_snapshot.call_count == 3
    window.close()


def test_shared_geometry_remains_visible_when_one_body_is_hidden():
    document = parse_step("""ISO-10303-21;
HEADER;ENDSEC;
DATA;
#1=CARTESIAN_POINT('',(0.,0.,0.));
#2=VERTEX_POINT('',#1);
#3=CLOSED_SHELL('',(#2));
#4=CLOSED_SHELL('',(#2));
#5=MANIFOLD_SOLID_BREP('',#3);
#6=MANIFOLD_SOLID_BREP('second',#4);
ENDSEC;END-ISO-10303-21;
""")
    bodies = document_bodies(document)
    assert [body.name for body in bodies] == ["Manifold Solid Brep #5", "second"]
    assert hidden_entities(bodies, {5}) == {3, 5}
