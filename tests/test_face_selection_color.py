import pytest

from step_explorer.geometry.builder import Mesh, Point3


@pytest.mark.parametrize("mode", ("all-the-same", "random", "normals"))
def test_selection_brightens_each_mode_and_pulses_once(view, mode):
    view.face_color_mode = mode
    mesh = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))
    view._add_mesh(mesh, selected=True, labels=False, label_scale=1.0, label_text="")

    actor = view._face_actors[7]
    base, back_base = view._face_base_colors(7)
    steady = view._brighten_color(base, 0.4)
    assert actor.GetProperty().GetColor() == pytest.approx(steady)
    assert all(chosen >= original for chosen, original in zip(steady, base))
    assert steady != pytest.approx((1.0, 0.4, 0.2))
    if back_base is not None:
        assert actor.GetBackfaceProperty().GetColor() == pytest.approx(view._brighten_color(back_base, 0.4))

    view._last_selected_id = 7
    view._flash_selected_face(7)
    assert actor.GetProperty().GetColor() == pytest.approx(view._brighten_color(base, 0.72))
    if back_base is not None:
        assert actor.GetBackfaceProperty().GetColor() == pytest.approx(view._brighten_color(back_base, 0.72))
    view._selection_flash_timer.stop()
    view._finish_selection_flash()
    assert actor.GetProperty().GetColor() == pytest.approx(steady)
    if back_base is not None:
        assert actor.GetBackfaceProperty().GetColor() == pytest.approx(view._brighten_color(back_base, 0.4))
