import pytest

from step_explorer.visualization.vtk_view import vtk


def test_clearing_absent_issue_does_not_render(view, monkeypatch):
    renders = []
    view._initialized = True
    monkeypatch.setattr(view, '_profiled_render', lambda: renders.append(True))
    view._issue_edge_id = 42
    view._issue_blink_steps.append(('edge', True))
    view.set_issue_edge(None)
    view.set_issue_edge(None)
    assert renders == []
    assert view._issue_edge_id is None
    assert view._issue_blink_steps == []


@pytest.mark.parametrize('highlight', ['edge', 'face', 'overview_edge', 'overview_face'])
def test_clearing_existing_issue_renders_once_and_restores_scene(view, monkeypatch, highlight):
    renders = []
    view._initialized = True
    monkeypatch.setattr(view, '_profiled_render', lambda: renders.append(True))
    actor = vtk.vtkActor()
    view.renderer.AddActor(actor)
    if highlight == 'edge':
        view._issue_edge_actor = actor
    elif highlight == 'overview_edge':
        view._issue_overview_edge_actors.append(actor)
    else:
        original = vtk.vtkProperty()
        original.SetColor(0.1, 0.2, 0.3)
        actor.GetProperty().SetColor(1, 0, 1)
        backface = vtk.vtkProperty()
        saved = (actor, original, backface)
        if highlight == 'face':
            view._issue_face_properties.append(saved)
        else:
            view._issue_overview_properties.append(saved)
    view.set_issue_edge(None)
    assert renders == [True]
    assert view._issue_edge_actor is None
    assert not view._issue_face_properties
    assert not view._issue_overview_properties
    assert not view._issue_overview_edge_actors
    if highlight in {'edge', 'overview_edge'}:
        assert not view.renderer.HasViewProp(actor)
    else:
        assert actor.GetProperty().GetColor() == pytest.approx((0.1, 0.2, 0.3))
        assert actor.GetBackfaceProperty() is backface
    view.set_issue_edge(None)
    assert renders == [True]
