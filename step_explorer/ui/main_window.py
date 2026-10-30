from __future__ import annotations

from dataclasses import replace
from math import isfinite
from pathlib import Path
from time import monotonic

from ..config import AppConfig, load_config
from ..geometry import GeometryBuilder, GeometrySnapshot, save_mesh_as_obj, save_mesh_as_stl
from ..geometry.completeness import assess_completeness, find_face_preview_gaps
from ..llm_report import (
    PLAYBACK_ESTIMATE_WARN_SECONDS,
    analyze_processing_pain, build_llm_report, describe_playback_estimate,
    estimate_playback_duration, preview_health_score, report_red_reasons,
)
from ..step.parser import StepDocument
from ..step.export import save_geometry_only_step
from ..step.values import StepReference
from .entity_details import EntityDetails
from .entity_tree import EntityTree
from .object_tree import ObjectTree
from .preferences import PreferencesDialog
from .step_lines import STEP_LINE_PALETTES
from ..visualization.vtk_view import VtkView

from PySide6.QtCore import QEvent, QPropertyAnimation, QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QComboBox,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QColorDialog,
    QTabWidget,
    QToolBar,
    QToolButton,
    QScrollArea,
    QHBoxLayout,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def _label_for_entity_type(type_name: str) -> str:
    """Return a compact, useful type label for rendered STEP entities."""

    name = type_name.upper()
    if "BREP" in name or "MANIFOLD_SOLID" in name:
        return "BREP"
    if "NURBS" in name or "B_SPLINE" in name:
        return "NURBS"
    if name == "CARTESIAN_POINT":
        return "Point"
    if name == "VERTEX_POINT":
        return "Vertex"
    if name == "ORIENTED_EDGE":
        return "Oriented Edge"
    if name == "EDGE_CURVE" or "EDGE" in name:
        return "Edge"
    if "FACE" in name:
        return "Face"
    if "SHELL" in name:
        return "Shell"
    if "CURVE" in name:
        return "Curve"
    if "SURFACE" in name:
        return "Surface"
    return type_name.replace("_", " ").title()


class MainWindow(QMainWindow):
    # Turbo batches consecutive playback steps between viewport refreshes.
    # Entities in files like corpus/TOUGH_stresstests/AL39.STEP are dominated by
    # cheap CARTESIAN_POINT/DIRECTION records, so a small cap would refresh the
    # viewport after every 8 near-free steps. The cap stays high, while the time
    # budget below keeps heavy faces from monopolizing the UI thread; either
    # end of the loop stops the batch, whichever comes first.
    FAST_PREVIEW_BATCH_SIZE = 256
    FAST_PREVIEW_BATCH_BUDGET_SECONDS = 0.020
    NAVIGATION_PRESET_DEFAULTS = {
        "Blender": (False, True),
        "CAD": (True, False),
        "Plasticity": (False, True),
        "FreeCAD": (True, True),
    }

    def __init__(self, initial_path: str | None = None, config: AppConfig | None = None):
        super().__init__()
        self.setWindowTitle("steposcope — STEP walker")
        self.resize(1500, 850)
        self.document: StepDocument | None = None
        self.geometry_builder: GeometryBuilder | None = None
        self.config = config or load_config()
        self.settings = QSettings("steposcope", "steposcope")
        self.navigation_preset = self._load_navigation_preset()
        self._migrate_navigation_preset_values()
        self.invert_mouse_horizontal = self._load_invert_mouse_axis("horizontal")
        self.invert_mouse_vertical = self._load_invert_mouse_axis("vertical")
        self.orbit_method = self._load_orbit_method()
        self.show_entity_labels = self._load_show_entity_labels()
        self.show_xyz_origin = self._load_show_xyz_origin()
        self.show_xy_grid = self._load_show_xy_grid()
        self.show_mesh_edges = bool(self.settings.value("show_mesh_edges", False, type=bool))
        self.mesh_edge_opacity = max(0, min(100, self.settings.value("mesh_edge_opacity", 60, type=int)))
        self.xy_grid_color = self._load_xy_grid_color()
        self.background_color = self._load_background_color()
        self.xy_grid_scale = self._load_xy_grid_scale()
        self.autozoom = self._load_autozoom()
        self.label_mode = self._load_label_mode()
        self.max_entity_labels = VtkView._clamp_label_limit(
            self.settings.value("max_entity_labels", self.config.max_entity_labels, type=int)
        )
        self.playback_speed = self._load_playback_speed()
        self.playback_order = 0
        self._last_playback_step_finished: float | None = None
        self.control_net_densities: dict[int, int] = {}
        self._control_nets_by_id = {}
        self._control_net_statuses_by_id = {}
        self._surface_control_status_labels: dict[int, QLabel] = {}
        self.surface_control_sliders: dict[int, QSlider] = {}
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(self._playback_interval())
        self.playback_timer.timeout.connect(self._playback_tick)
        self.recent_files: list[str] = self._load_recent_files()
        self.current_id: int | None = None
        self.analyzed_face_id: int | None = None
        self._analysis_snapshot = None
        self.current_snapshot = None
        self._report_snapshot = None
        self._report_health_score: int | None = None
        self._report_pain = None
        self._entity_processing_seconds: dict[int, float] = {}
        self._face_peak_seconds: dict[int, float] = {}
        self._timed_playback_orders: set[int] = set()
        self._preview_gaps_by_id = {}
        self._same_direction_pairs = ()
        self._same_direction_index = 0
        self.discovered_order = -1
        self.tree = EntityTree()
        self.object_tree = ObjectTree()
        palette = str(self.settings.value("step_lines_palette", "Classic"))
        self.step_lines_palette = palette if palette in STEP_LINE_PALETTES else "Classic"
        self.step_lines_show_emojis = self.settings.value("step_lines_show_emojis", True, type=bool)
        self.step_lines_color_source = self.settings.value("step_lines_color_source", False, type=bool)
        self.tree.set_step_line_appearance(
            self.step_lines_palette, self.step_lines_show_emojis, self.step_lines_color_source
        )
        self.details = EntityDetails()
        self.details_tabs = QTabWidget()
        self._interpretation_width = 376
        interpretation_page = QWidget()
        interpretation_layout = QVBoxLayout(interpretation_page)
        self.interpretation_layout = interpretation_layout
        interpretation_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_warning_group = QGroupBox("Preview warnings")
        warning_details_layout = QVBoxLayout(self.preview_warning_group)
        warning_details_layout.setContentsMargins(8, 6, 8, 6)
        self.preview_warning_details = QPlainTextEdit()
        self.preview_warning_details.setReadOnly(True)
        self.preview_warning_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview_warning_details.setMaximumHeight(180)
        self.preview_warning_details.setToolTip("Select warning text to copy it")
        self.preview_warning_details.setStyleSheet(
            "QPlainTextEdit { background: #20242a; color: #f2f4f8; border: 1px solid #555b63; }"
        )
        warning_details_layout.addWidget(self.preview_warning_details)
        self.preview_warning_group.hide()
        interpretation_layout.addWidget(self.preview_warning_group)
        self.edge_issue_group = QGroupBox("⚠ Same-direction edge pairs")
        edge_issue_layout = QVBoxLayout(self.edge_issue_group)
        edge_issue_layout.setContentsMargins(8, 6, 8, 6)
        self.edge_issue_summary = QLabel()
        self.edge_issue_summary.setWordWrap(True)
        edge_issue_layout.addWidget(self.edge_issue_summary)
        edge_issue_navigation = QHBoxLayout()
        self.previous_edge_issue_button = QPushButton("Previous")
        self.next_edge_issue_button = QPushButton("Next")
        self.edge_issue_position = QLabel()
        edge_issue_navigation.addWidget(self.previous_edge_issue_button)
        edge_issue_navigation.addWidget(self.edge_issue_position)
        edge_issue_navigation.addWidget(self.next_edge_issue_button)
        edge_issue_layout.addLayout(edge_issue_navigation)
        self.edge_issue_details = QLabel()
        self.edge_issue_details.setWordWrap(True)
        self.edge_issue_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        edge_issue_layout.addWidget(self.edge_issue_details)
        self.edge_issue_edge_button = QPushButton()
        self.edge_issue_face_buttons = (QPushButton(), QPushButton())
        self.edge_issue_use_buttons = (QPushButton(), QPushButton())
        for button in (self.edge_issue_edge_button, *self.edge_issue_face_buttons,
                       *self.edge_issue_use_buttons):
            edge_issue_layout.addWidget(button)
        self.fit_edge_issue_button = QPushButton("Fit affected edge")
        edge_issue_layout.addWidget(self.fit_edge_issue_button)
        self.blink_edge_issue_button = QPushButton("Blink affected edge and faces")
        self.blink_edge_issue_button.setToolTip(
            "Flash the edge three times, then flash each connected face in turn"
        )
        edge_issue_layout.addWidget(self.blink_edge_issue_button)
        self.highlight_edge_issues_button = QPushButton("Highlight edges and faces")
        self.highlight_edge_issues_button.setCheckable(True)
        self.highlight_edge_issues_button.setToolTip(
            "Show all same-direction pairs together: grey model, red edges, colored faces"
        )
        edge_issue_layout.addWidget(self.highlight_edge_issues_button)
        self.edge_issue_group.hide()
        interpretation_layout.addWidget(self.edge_issue_group)
        self.tessellation_group = QGroupBox("Tessellation")
        tessellation_layout = QVBoxLayout(self.tessellation_group)
        tessellation_layout.setContentsMargins(8, 6, 8, 6)
        self.mesh_edges_checkbox = QCheckBox("Show triangle edges")
        self.mesh_edges_checkbox.setChecked(self.show_mesh_edges)
        tessellation_layout.addWidget(self.mesh_edges_checkbox)
        self.mesh_edge_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.mesh_edge_opacity_slider.setRange(0, 100)
        self.mesh_edge_opacity_slider.setValue(self.mesh_edge_opacity)
        self.mesh_edge_opacity_slider.setAccessibleName("Tessellation edge opacity")
        self.mesh_edge_opacity_slider.setEnabled(self.show_mesh_edges)
        self.mesh_edge_opacity_value = QLabel(f"{self.mesh_edge_opacity}%")
        tessellation_layout.addLayout(self._analysis_slider_row(
            "Edge opacity:", self.mesh_edge_opacity_slider, self.mesh_edge_opacity_value
        ))
        self.background_color_button = QPushButton("Background color…")
        self.background_color_button.setToolTip("Choose the viewport background color")
        self._update_background_color_button()
        tessellation_layout.addWidget(
            self.background_color_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        interpretation_layout.addWidget(self.tessellation_group)
        face_analysis_actions = QWidget()
        face_analysis_actions_layout = QVBoxLayout(face_analysis_actions)
        face_analysis_actions_layout.setContentsMargins(6, 6, 6, 2)
        face_analysis_actions_layout.setSpacing(3)
        self.analyze_face_button = QPushButton("Analyze this face")
        self.analyze_face_button.setToolTip(
            "Highlight this face, dim the rest of the model, and label its control points"
        )
        self.analyze_face_button.setVisible(False)
        self.exit_face_analysis_button = QPushButton("Exit face analysis")
        self.exit_face_analysis_button.setVisible(False)
        self.extract_face_stl_button = QPushButton("Extract to .stl")
        self.extract_face_stl_button.setToolTip("Save the analyzed face preview mesh as an STL file")
        self.extract_face_stl_button.setVisible(False)
        self.extract_face_obj_button = QPushButton("Extract to .obj")
        self.extract_face_obj_button.setToolTip("Save the analyzed face preview mesh as an OBJ file")
        self.extract_face_obj_button.setVisible(False)
        for button in (
            self.analyze_face_button,
            self.exit_face_analysis_button,
            self.extract_face_stl_button,
            self.extract_face_obj_button,
        ):
            face_analysis_actions_layout.addWidget(
                button, alignment=Qt.AlignmentFlag.AlignLeft
            )
        interpretation_layout.addWidget(face_analysis_actions)
        self.face_inspection_group = QGroupBox("Face inspection")
        face_inspection_layout = QVBoxLayout(self.face_inspection_group)
        face_inspection_layout.setContentsMargins(8, 6, 8, 6)
        face_inspection_layout.setSpacing(4)
        self.face_assessment_verdict = QLabel("Assessment: —")
        self.face_assessment_verdict.setWordWrap(True)
        self.face_assessment_reasons = QLabel(
            "The assessment uses local topology and preview-mesh checks."
        )
        self.face_assessment_reasons.setWordWrap(True)
        face_inspection_layout.addWidget(self.face_assessment_verdict)
        face_inspection_layout.addWidget(self.face_assessment_reasons)
        self.face_triangle_edges_checkbox = QCheckBox("Triangle edges")
        self.face_triangle_edges_checkbox.setChecked(True)
        self.face_triangle_edges_checkbox.setToolTip(
            "Show the preview mesh triangles to inspect tessellation and gaps"
        )
        face_inspection_layout.addWidget(self.face_triangle_edges_checkbox)
        self.face_normals_checkbox = QCheckBox("Show face normals")
        self.face_normals_checkbox.setToolTip(
            "Draw sampled arrows following the analyzed face's preview triangle winding"
        )
        face_inspection_layout.addWidget(self.face_normals_checkbox)
        self.face_normal_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.face_normal_scale_slider.setRange(25, 300)
        self.face_normal_scale_slider.setValue(100)
        self.face_normal_scale_slider.setEnabled(False)
        self.face_normal_scale_slider.setAccessibleName("Face normal length")
        self.face_normal_scale_value = QLabel("100%")
        self.face_normal_scale_row = QWidget()
        self.face_normal_scale_row.setLayout(
            self._analysis_slider_row(
                "Normal length:", self.face_normal_scale_slider, self.face_normal_scale_value
            )
        )
        self.face_normal_scale_row.hide()
        face_inspection_layout.addWidget(self.face_normal_scale_row)
        self.face_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.face_opacity_slider.setRange(5, 100)
        self.face_opacity_slider.setValue(86)
        self.face_opacity_slider.setAccessibleName("Analyzed face opacity")
        self.face_opacity_value = QLabel("86%")
        face_inspection_layout.addLayout(
            self._analysis_slider_row("Face opacity:", self.face_opacity_slider, self.face_opacity_value)
        )
        self.face_context_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.face_context_opacity_slider.setRange(0, 30)
        self.face_context_opacity_slider.setValue(7)
        self.face_context_opacity_slider.setAccessibleName("Surrounding geometry visibility")
        self.face_context_opacity_value = QLabel("7%")
        face_inspection_layout.addLayout(
            self._analysis_slider_row(
                "Context visibility:",
                self.face_context_opacity_slider,
                self.face_context_opacity_value,
            )
        )
        self.fit_analyzed_face_button = QPushButton("Fit face")
        self.fit_analyzed_face_button.setToolTip("Zoom the viewport to the analyzed face")
        self.reset_face_inspection_button = QPushButton("Reset controls")
        self.reset_face_inspection_button.setToolTip("Restore the default face-analysis display settings")
        face_inspection_layout.addWidget(
            self.fit_analyzed_face_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        face_inspection_layout.addWidget(
            self.reset_face_inspection_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self.face_inspection_group.setVisible(False)
        interpretation_layout.addWidget(self.face_inspection_group)
        interpretation_layout.addWidget(self.details, 1)
        self.details_tabs.addTab(interpretation_page, "Interpretation")
        self.viewport = VtkView(
            navigation_preset=self.navigation_preset,
            orbit_method=self.orbit_method,
            invert_mouse_horizontal=self.invert_mouse_horizontal,
            invert_mouse_vertical=self.invert_mouse_vertical,
            autozoom=self.autozoom,
            max_entity_labels=self.max_entity_labels,
            show_xyz_origin=self.show_xyz_origin,
            show_xy_grid=self.show_xy_grid,
            show_mesh_edges=self.show_mesh_edges,
            mesh_edge_opacity=self.mesh_edge_opacity / 100,
            xy_grid_color=self.xy_grid_color,
            xy_grid_scale=self.xy_grid_scale,
            background_color=self.background_color,
        )
        self.mode = QComboBox(); self.mode.addItems(["File order", "Semantic", "Playback"])
        self.mode.setCurrentText("Playback")
        self.normal_display = QComboBox()
        self.normal_display.addItems(VtkView.NORMAL_DISPLAY_MODES)
        saved_normal_display = str(self.settings.value("normal_display", "Hidden"))
        if saved_normal_display in VtkView.NORMAL_DISPLAY_MODES:
            self.normal_display.setCurrentText(saved_normal_display)
            self.viewport.set_normal_display(saved_normal_display)
        self.normal_display.setToolTip(
            "Face orientation: blue shows the front of the preview surface; red shows its back"
        )
        self.normal_arrow_limit = max(
            0,
            min(
                VtkView.MAX_NORMAL_ARROW_LIMIT,
                self.settings.value(
                    "normal_arrow_limit", VtkView.DEFAULT_NORMAL_ARROW_LIMIT, type=int,
                ),
            ),
        )
        self.viewport.set_normal_arrow_limit(self.normal_arrow_limit)
        self.normal_arrow_slider = QSlider(Qt.Orientation.Horizontal)
        self.normal_arrow_slider.setRange(0, VtkView.MAX_NORMAL_ARROW_LIMIT)
        self.normal_arrow_slider.setValue(self.normal_arrow_limit)
        self.normal_arrow_slider.setFixedWidth(100)
        self.normal_arrow_slider.setToolTip(
            "Maximum number of normal arrows shown across the whole model"
        )
        self.normal_arrow_count_label = QLabel(str(self.normal_arrow_limit))
        self.normal_arrow_count_label.setToolTip(self.normal_arrow_slider.toolTip())
        self.face_color_mode = QComboBox()
        self.face_color_mode.addItems(VtkView.FACE_COLOR_MODES)
        saved_face_colors = str(self.settings.value("face_color_mode", "normals"))
        if saved_face_colors not in VtkView.FACE_COLOR_MODES or saved_normal_display == "Face orientation":
            saved_face_colors = "normals"
        self.face_color_mode.setCurrentText(saved_face_colors)
        self.viewport.set_face_color_mode(saved_face_colors)
        self.face_color_mode.setToolTip(
            "Choose one shared face color, front/back normal colors, or stable per-face colors"
        )
        self.legend = QLabel()
        self.status = QLabel("")
        self.open_file_directory_button = QPushButton("Open file directory")
        self.open_file_directory_button.setToolTip("Open the folder containing the loaded STEP file")
        self.open_file_directory_button.setEnabled(False)
        self.preview_warning_button = QPushButton()
        self.preview_warning_button.setObjectName("previewWarningButton")
        self.preview_warning_button.setToolTip("Open the full preview warning details")
        self.preview_warning_button.hide()
        self.preview_warning_button.setParent(
            self.viewport.widget if hasattr(self.viewport, "widget") else self.viewport
        )
        self.preview_warning_button.setStyleSheet(
            "QPushButton { background: #4a2716; color: #fff1df; font-weight: bold; "
            "border: 2px solid #ef8b38; border-radius: 5px; padding: 5px 9px; }"
            "QPushButton:hover { background: #69341a; }"
        )
        self.edge_issue_warning_button = QPushButton()
        self.edge_issue_warning_button.setParent(
            self.viewport.widget if hasattr(self.viewport, "widget") else self.viewport
        )
        self.edge_issue_warning_button.setToolTip(
            "The completed model has face boundaries using the same edge in the same direction. "
            "Click to inspect the affected edges and faces."
        )
        self.edge_issue_warning_button.setStyleSheet(
            "QPushButton { background: #5b2a12; color: #ffe8b3; font-weight: bold; "
            "border: 2px solid #ffad36; border-radius: 5px; padding: 6px 10px; }"
            "QPushButton:hover { background: #783914; }"
        )
        self.edge_issue_warning_button.hide()
        self.open_button = QPushButton("Open STEP…")
        self.play_button = QPushButton("Play")
        self.play_button.setCheckable(True)
        self.heal_checkbox = QCheckBox("Heal")
        self.heal_checkbox.setChecked(self.settings.value("heal_orientations", False, type=bool))
        self.heal_checkbox.setToolTip("Align preview face orientations with their analytic surfaces; does not modify the STEP file")
        self.fast_preview_checkbox = QCheckBox("Turbo")
        self.fast_preview_checkbox.setChecked(self.settings.value("fast_preview", False, type=bool))
        self.fast_preview_checkbox.setToolTip(
            "Set Max speed to 100%. Interpret up to 256 consecutive STEP entities or about 20 ms "
            "of work per playback tick, "
            "then refresh the viewport once per batch. During playback, hide standalone point "
            "and vertex markers once other geometry appears, and hide triangle-edge overlays. "
            "Source order and entity inspection are preserved; the displayed geometry may advance in larger jumps."
        )
        self.reset_view_button = QPushButton("Reset view")
        self.reset_view_button.setToolTip("Reset zoom and restore an isometric view toward the axes origin (R in the viewport)")
        self.fit_view_button = QPushButton("Fit")
        self.fit_view_button.setToolTip("Frame the model while keeping the current viewing angle (F in the viewport)")
        self.focus_face_button = QPushButton("Focus on Face")
        self.focus_face_button.setToolTip("Orbit around the selected face without changing zoom or viewing angle")
        self.focus_face_button.setEnabled(False)
        self.projection_mode = QComboBox()
        self.projection_mode.addItems(["Perspective", "Isometric"])
        saved_projection = str(self.settings.value("projection_mode", "Perspective"))
        self.projection_mode.setCurrentText(saved_projection if saved_projection in {"Perspective", "Isometric"} else "Perspective")
        self.projection_mode.setToolTip("Perspective or parallel isometric projection")
        self.viewport.set_projection_mode(self.projection_mode.currentText())
        self.step_backward_button = QPushButton("Step backward")
        self.step_forward_button = QPushButton("Step forward")
        self.playback_progress = QSlider(Qt.Orientation.Horizontal)
        self.playback_progress.setRange(0, 0)
        self.playback_progress.setToolTip("Choose the point in the STEP entity build-up")
        self.playback_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.playback_speed_slider.setRange(0, 100)
        self.playback_speed_slider.setValue(self.playback_speed)
        self.playback_speed_slider.setFixedWidth(100)
        self.playback_speed_slider.setAccessibleName("Maximum playback speed")
        self.playback_speed_value = QLabel(f"{self.playback_speed}%")
        self.playback_speed_value.setMinimumWidth(38)
        self.playback_speed_help = QLabel("(?)")
        self.playback_speed_help.setAccessibleName("Playback speed help")
        self.playback_speed_help.setStyleSheet("color: #78bfff; font-weight: bold;")
        speed_note = (
            "100% removes the playback timer delay, but actual speed is still limited by CPU "
            "processing, geometry building, and viewport rendering. 0% pauses playback."
        )
        self.playback_speed_help.setToolTip(speed_note)
        self.playback_speed_slider.setToolTip(speed_note)
        self.playback_frame_label = QLabel("0 / 0")
        self.hotkey_legend = QLabel(
            ""
        )
        self._update_hotkey_legend()
        self.hotkey_legend.setToolTip("Keyboard and mouse shortcuts for the STEP viewer")
        self.hotkey_legend.setParent(self.viewport.widget if hasattr(self.viewport, "widget") else self.viewport)
        self.hotkey_legend.setObjectName("hotkeyOverlay")
        self.hotkey_legend.setStyleSheet(
            "QLabel { background: rgba(25, 29, 35, 225); color: #f2f4f8; "
            "border: 1px solid #68717c; border-radius: 5px; padding: 10px; }"
        )
        self.hotkey_legend.setWordWrap(True)
        self.hotkey_legend.setMaximumWidth(520)
        self.hotkey_legend.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hotkey_legend.hide()
        self.hotkey_opacity = QGraphicsOpacityEffect(self.hotkey_legend)
        self.hotkey_legend.setGraphicsEffect(self.hotkey_opacity)
        self.hotkey_fade = QPropertyAnimation(self.hotkey_opacity, b"opacity", self)
        self.hotkey_fade.setDuration(1800)
        self.hotkey_fade.finished.connect(self.hotkey_legend.hide)
        self.hotkey_hide_timer = QTimer(self)
        self.hotkey_hide_timer.setSingleShot(True)
        self.hotkey_hide_timer.timeout.connect(self._fade_hotkey_help)
        self.shape_expectation_label = QLabel()
        self.shape_expectation_label.setParent(
            self.viewport.widget if hasattr(self.viewport, "widget") else self.viewport
        )
        self.shape_expectation_label.setStyleSheet(
            "QLabel { background: rgba(25, 29, 35, 225); color: #f2f4f8; "
            "border: 1px solid #68717c; border-radius: 4px; padding: 3px 7px; "
            "font-size: 10px; font-weight: normal; }"
        )
        self.shape_expectation_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.shape_expectation_label.hide()
        self._build_file_menu()
        self._build_ui()
        self._connect_signals()
        QTimer.singleShot(150, self.viewport.reset_camera)
        if initial_path:
            self.open_path(initial_path)

    def _build_file_menu(self):
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open STEP…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        open_action.triggered.connect(self.open_dialog)
        file_menu.addAction(open_action)
        self.save_geometry_action = QAction("Save Geometry only STEP file…", self)
        self.save_geometry_action.setEnabled(False)
        self.save_geometry_action.triggered.connect(self.save_geometry_dialog)
        file_menu.addAction(self.save_geometry_action)
        self.recent_menu = file_menu.addMenu("Recent files")
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)
        file_menu.addSeparator()
        clear_action = QAction("Clear recent files", self)
        clear_action.triggered.connect(self._clear_recent_files)
        file_menu.addAction(clear_action)
        file_menu.addSeparator()
        close_action = QAction("Quit", self)
        close_action.setShortcut("Ctrl+Q")
        close_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)
        self._populate_recent_menu()

        edit_menu = self.menuBar().addMenu("Edit")
        preferences_action = QAction("Preferences…", self)
        preferences_action.setShortcuts([QKeySequence("Ctrl+,"), QKeySequence("Ctrl+Shift+P")])
        preferences_action.triggered.connect(self.open_preferences)
        edit_menu.addAction(preferences_action)

        view_menu = self.menuBar().addMenu("View")
        self.view_menu = view_menu
        self.geometry_only_action = QAction("Show only geometry", self)
        self.geometry_only_action.setCheckable(True)
        self.geometry_only_action.setShortcut("Ctrl+Shift+G")
        self.geometry_only_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.geometry_only_action.setToolTip("Hide organizational, approval, and other non-shape entities")
        self.geometry_only_action.toggled.connect(self._geometry_filter_changed)
        view_menu.addAction(self.geometry_only_action)
        self.invert_mouse_horizontal_action = QAction("Invert horizontal rotation", self)
        self.invert_mouse_horizontal_action.setCheckable(True)
        self.invert_mouse_horizontal_action.setChecked(self.invert_mouse_horizontal)
        self.invert_mouse_horizontal_action.setToolTip("Reverse left-right orbit dragging")
        self.invert_mouse_vertical_action = QAction("Invert vertical rotation", self)
        self.invert_mouse_vertical_action.setCheckable(True)
        self.invert_mouse_vertical_action.setChecked(self.invert_mouse_vertical)
        self.invert_mouse_vertical_action.setToolTip("Reverse up-down orbit dragging")
        self.autozoom_action = QAction("Autozoom", self)
        self.autozoom_action.setCheckable(True)
        self.autozoom_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.autozoom_action.setChecked(self.autozoom)
        self.autozoom_action.setToolTip("Fit the camera to visible geometry after every playback update")
        self.show_xyz_origin_action = QAction("Show XYZ origin axes", self)
        self.show_xyz_origin_action.setCheckable(True)
        self.show_xyz_origin_action.setChecked(self.show_xyz_origin)
        self.show_xyz_origin_action.setToolTip("Show the red X, green Y, and cyan Z axes at the world origin")
        view_menu.addAction(self.show_xyz_origin_action)
        self.show_xy_grid_action = QAction("XY grid", self)
        self.show_xy_grid_action.setCheckable(True)
        self.show_xy_grid_action.setChecked(self.show_xy_grid)
        self.show_xy_grid_action.setToolTip("Show or hide the world XY-plane grid")
        self.show_mesh_edges_action = QAction("Tessellation edges", self)
        self.show_mesh_edges_action.setCheckable(True)
        self.show_mesh_edges_action.setShortcut("M")
        self.show_mesh_edges_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.show_mesh_edges_action.setChecked(self.show_mesh_edges)
        self.show_mesh_edges_action.setToolTip("Draw triangle edges over shaded faces")
        view_menu.addAction(self.show_mesh_edges_action)
        self.background_color_action = QAction("Background color…", self)
        self.background_color_action.triggered.connect(self._choose_background_color)
        view_menu.addAction(self.background_color_action)
        self.show_entity_labels_action = QAction("Show entity labels", self)
        self.show_entity_labels_action.setCheckable(True)
        self.show_entity_labels_action.setShortcut("L")
        self.show_entity_labels_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.show_entity_labels_action.setChecked(self.show_entity_labels)
        self.show_entity_labels_action.setToolTip("Show #entity labels for rendered points, edges, and faces")
        view_menu.addAction(self.show_entity_labels_action)
        label_content_menu = view_menu.addMenu("Label content")
        self.label_mode_group = QActionGroup(self)
        self.label_mode_group.setExclusive(True)
        self.line_index_labels_action = QAction("Line index (#ID)", self)
        self.line_index_labels_action.setCheckable(True)
        self.type_labels_action = QAction("Entity type", self)
        self.type_labels_action.setCheckable(True)
        self.label_mode_group.addAction(self.line_index_labels_action)
        self.label_mode_group.addAction(self.type_labels_action)
        label_content_menu.addAction(self.line_index_labels_action)
        label_content_menu.addAction(self.type_labels_action)
        self.line_index_labels_action.setChecked(self.label_mode == "index")
        self.type_labels_action.setChecked(self.label_mode == "type")

    def _load_invert_mouse_axis(self, axis: str) -> bool:
        key = f"invert_mouse_{axis}"
        if self.settings.contains(key):
            return bool(self.settings.value(key, False, type=bool))
        if self.settings.contains("invert_mouse_rotation"):
            return bool(self.settings.value("invert_mouse_rotation", False, type=bool))
        if axis in {"horizontal", "vertical"} and self.navigation_preset == "Blender":
            return axis == "vertical"
        return getattr(self.config, key)

    def _load_playback_speed(self) -> int:
        if self.settings.contains("playback_speed_percent"):
            value = self.settings.value("playback_speed_percent", 100, type=int)
        elif self.settings.contains("playback_tick_rate"):
            old_rate = max(1, min(60, self.settings.value("playback_tick_rate", 10, type=int)))
            value = 100 - round(100 / old_rate)
        else:
            value = 100
        return max(0, min(100, int(value)))

    def _load_show_entity_labels(self) -> bool:
        if not self.settings.contains("show_entity_labels"):
            return self.config.show_entity_labels
        return bool(self.settings.value("show_entity_labels", False, type=bool))

    def _load_show_xyz_origin(self) -> bool:
        if not self.settings.contains("show_xyz_origin"):
            return self.config.show_xyz_origin
        return bool(self.settings.value("show_xyz_origin", True, type=bool))

    def _load_show_xy_grid(self) -> bool:
        if not self.settings.contains("show_xy_grid"):
            return self.config.show_xy_grid
        return bool(self.settings.value("show_xy_grid", True, type=bool))

    def _load_xy_grid_color(self) -> str:
        value = str(self.settings.value("xy_grid_color", self.config.xy_grid_color))
        return value if QColor(value).isValid() else self.config.xy_grid_color

    def _load_background_color(self) -> str:
        value = str(self.settings.value("background_color", self.config.background_color))
        # Treat the previous built-in default as unset so existing installs
        # receive the updated slate-blue default. Other saved colors persist.
        if value.lower() == "#1a1f26":
            value = self.config.background_color
        color = QColor(value)
        return color.name() if color.isValid() else self.config.background_color

    def _load_xy_grid_scale(self) -> float:
        try:
            value = float(self.settings.value("xy_grid_scale", self.config.xy_grid_scale))
        except (TypeError, ValueError):
            value = self.config.xy_grid_scale
        if not isfinite(value):
            value = 1.0
        return max(0.25, min(10.0, value))

    def _load_autozoom(self) -> bool:
        if not self.settings.contains("autozoom"):
            return self.config.autozoom
        return bool(self.settings.value("autozoom", True, type=bool))

    def _load_navigation_preset(self) -> str:
        value = str(self.settings.value("navigation_preset", "Blender"))
        return value if value in {"Blender", "CAD", "Plasticity", "FreeCAD"} else "Blender"

    def _migrate_navigation_preset_values(self):
        version_key = "navigation_preset_values_version"
        if self.settings.value(version_key, 0, type=int) >= 1:
            return
        if self.settings.contains("navigation_preset") and self.navigation_preset == "Blender":
            self.settings.setValue("invert_mouse_horizontal", False)
            self.settings.setValue("invert_mouse_vertical", True)
        self.settings.setValue(version_key, 1)
        self.settings.sync()

    def _load_orbit_method(self) -> str:
        profile_key = f"navigation_profiles/{self.navigation_preset}/orbit_method"
        if self.settings.contains(profile_key):
            value = str(self.settings.value(profile_key, "Turntable"))
        else:
            value = str(self.settings.value("orbit_method", "Turntable"))
            if value in {"Turntable", "Trackball"} and self.settings.contains("orbit_method"):
                self.settings.setValue(profile_key, value)
                self.settings.sync()
        return value if value in {"Turntable", "Trackball"} else "Turntable"

    def _orbit_method_changed(self, method: str):
        if method not in {"Turntable", "Trackball"}:
            return
        self.orbit_method = method
        self.settings.setValue(
            f"navigation_profiles/{self.navigation_preset}/orbit_method", method
        )
        self.settings.sync()
        self.viewport.set_orbit_method(method)

    def _navigation_preset_changed(self, preset: str):
        defaults = self.NAVIGATION_PRESET_DEFAULTS.get(preset)
        if defaults is None:
            return
        self.navigation_preset = preset
        self.settings.setValue("navigation_preset", preset)
        self.settings.sync()
        self.viewport.set_navigation_preset(preset)
        horizontal, vertical = defaults
        self.invert_mouse_horizontal_action.setChecked(horizontal)
        self.invert_mouse_vertical_action.setChecked(vertical)
        profile_key = f"navigation_profiles/{preset}/orbit_method"
        method = str(self.settings.value(profile_key, "Turntable"))
        self.orbit_method = method if method in {"Turntable", "Trackball"} else "Turntable"
        self.viewport.set_orbit_method(self.orbit_method)
        self._update_hotkey_legend()

    def _update_hotkey_legend(self):
        gestures = {
            "Blender": "MMB orbit · Shift+MMB pan · wheel zoom",
            "CAD": "LMB orbit · MMB pan · wheel zoom",
            "Plasticity": "MMB orbit · RMB pan · wheel zoom · Ctrl+MMB drag zoom",
            "FreeCAD": "MMB pan · LMB+MMB orbit · wheel zoom",
        }.get(self.navigation_preset, "MMB orbit · Shift+MMB pan · wheel zoom")
        self.hotkey_legend.setText(
            "Hotkeys: Ctrl+O open · Ctrl+Q quit · Ctrl+Shift+G geometry only · "
            f"Space play/pause · L labels · T label content · R reset view · F fit · {gestures}"
        )

    def _load_label_mode(self) -> str:
        if not self.settings.contains("label_mode"):
            return self.config.label_mode
        value = str(self.settings.value("label_mode", "index"))
        return value if value in {"index", "type"} else "index"

    def _playback_interval(self) -> int:
        return (100 - self.playback_speed) * 10

    def _invert_mouse_horizontal_changed(self, enabled: bool):
        self.invert_mouse_horizontal = enabled
        self.settings.setValue("invert_mouse_horizontal", enabled)
        self.settings.sync()
        self.viewport.set_invert_mouse_horizontal(enabled)

    def _invert_mouse_vertical_changed(self, enabled: bool):
        self.invert_mouse_vertical = enabled
        self.settings.setValue("invert_mouse_vertical", enabled)
        self.settings.sync()
        self.viewport.set_invert_mouse_vertical(enabled)

    def _autozoom_changed(self, enabled: bool):
        self.autozoom = enabled
        self.settings.setValue("autozoom", enabled)
        self.settings.sync()
        self.viewport.set_autozoom(enabled)

    def _show_xyz_origin_changed(self, enabled: bool):
        self.show_xyz_origin = enabled
        self.settings.setValue("show_xyz_origin", enabled)
        self.settings.sync()
        self.viewport.set_show_xyz_origin(enabled)

    def _show_xy_grid_changed(self, enabled: bool):
        self.show_xy_grid = enabled
        self.settings.setValue("show_xy_grid", enabled)
        self.settings.sync()
        self.viewport.set_show_xy_grid(enabled)

    def _show_mesh_edges_changed(self, enabled: bool):
        self.show_mesh_edges = enabled
        self.mesh_edges_checkbox.setChecked(enabled)
        self.mesh_edge_opacity_slider.setEnabled(enabled)
        self.settings.setValue("show_mesh_edges", enabled)
        self.settings.sync()
        self.viewport.set_show_mesh_edges(enabled)

    def _mesh_edge_opacity_changed(self, value: int):
        self.mesh_edge_opacity = value
        self.mesh_edge_opacity_value.setText(f"{value}%")
        self.settings.setValue("mesh_edge_opacity", value)
        self.settings.sync()
        self.viewport.set_mesh_edge_opacity(value / 100)

    def _set_xy_grid_color(self, color: str):
        self.xy_grid_color = color
        self.settings.setValue("xy_grid_color", self.xy_grid_color)
        self.settings.sync()
        self.viewport.set_xy_grid_color(self.xy_grid_color)

    def _choose_background_color(self):
        selected = QColorDialog.getColor(QColor(self.background_color), self, "Choose background color")
        if not selected.isValid():
            return
        self._set_background_color(selected.name())

    def _set_background_color(self, color: str):
        self.background_color = color
        self.settings.setValue("background_color", self.background_color)
        self.settings.sync()
        self.viewport.set_background_color(self.background_color)
        self._update_background_color_button()

    def _xy_grid_scale_changed(self, scale: float):
        self.xy_grid_scale = scale
        self.settings.setValue("xy_grid_scale", scale)
        self.settings.sync()
        self.viewport.set_xy_grid_scale(scale)

    def _update_background_color_button(self):
        color = QColor(self.background_color)
        text_color = "#ffffff" if color.lightness() < 128 else "#111111"
        self.background_color_button.setStyleSheet(
            "QPushButton { "
            f"background-color: {color.name()}; color: {text_color}; "
            "border: 1px solid #555555; padding: 3px 6px; }"
        )

    def _show_entity_labels_changed(self, enabled: bool):
        self.show_entity_labels = enabled
        self.settings.setValue("show_entity_labels", enabled)
        self.settings.sync()
        # Rebuild the current snapshot so enabling labels also refreshes the
        # type-name lookup that is intentionally skipped while labels are off.
        self._refresh_viewport()

    def _label_mode_changed(self, mode: str):
        self.label_mode = mode
        self.settings.setValue("label_mode", mode)
        self.settings.sync()
        self._refresh_viewport()

    def _max_entity_labels_changed(self, value: int):
        self.max_entity_labels = VtkView._clamp_label_limit(value)
        self.settings.setValue("max_entity_labels", self.max_entity_labels)
        self.settings.sync()
        self.viewport.set_max_entity_labels(self.max_entity_labels)

    def open_preferences(self):
        PreferencesDialog(self).exec()

    def _set_step_line_appearance(self, palette: str, show_emojis: bool, color_source: bool):
        self.step_lines_palette = palette if palette in STEP_LINE_PALETTES else "Classic"
        self.step_lines_show_emojis = show_emojis
        self.step_lines_color_source = color_source
        self.settings.setValue("step_lines_palette", self.step_lines_palette)
        self.settings.setValue("step_lines_show_emojis", show_emojis)
        self.settings.setValue("step_lines_color_source", color_source)
        self.settings.sync()
        self.tree.set_step_line_appearance(self.step_lines_palette, show_emojis, color_source)

    def _label_texts(self) -> dict[int, str]:
        if not self.document or not self.show_entity_labels:
            return {}
        return {entity.entity_id: _label_for_entity_type(entity.type_name) for entity in self.document.entities}

    def _label_orders(self) -> dict[int, int]:
        if not self.document or not self.show_entity_labels:
            return {}
        return {entity.entity_id: entity.order for entity in self.document.entities}

    def _refresh_viewport(self):
        self._refresh_viewport_with_surface_controls()

    def _heal_changed(self, checked: bool):
        self.settings.setValue("heal_orientations", checked)
        self.settings.sync()
        if self.geometry_builder is not None:
            self.geometry_builder.heal_orientations = checked
        self._analysis_snapshot = None
        self._refresh_viewport()

    def _fast_preview_changed(self, checked: bool):
        """Update playback batching without changing a paused preview."""
        if checked:
            self.playback_speed_slider.setValue(100)
        self.settings.setValue("fast_preview", checked)
        self.settings.sync()
        if self.current_snapshot is not None and self.play_button.isChecked():
            self._show_snapshot(self.current_snapshot, self.current_id, update_surface_controls=False)

    def _refresh_viewport_with_surface_controls(self, update_surface_controls: bool = True):
        if not self.document or not self.document.entities:
            return
        builder = self.geometry_builder or GeometryBuilder(self.document)
        snapshot = builder.build(self.discovered_order)
        self._show_snapshot(snapshot, self.current_id, update_surface_controls)

    def _show_snapshot(
        self,
        snapshot,
        selected_id: int | None,
        update_surface_controls: bool = True,
        incremental: bool = False,
    ):
        if self.analyzed_face_id is not None:
            if self._analysis_snapshot is None and self.document is not None:
                self._analysis_snapshot = (self.geometry_builder or GeometryBuilder(self.document)).build()
            snapshot = self._analysis_snapshot
            incremental = False
        self.current_snapshot = snapshot
        self._update_preview_warnings(snapshot, incremental=incremental)
        if (
            self.document is not None
            and self.document.entities
            and self.discovered_order >= len(self.document.entities) - 1
        ):
            if snapshot is not self._report_snapshot:
                self._report_health_score = preview_health_score(self.document, snapshot)[0]
                self._report_pain = analyze_processing_pain(
                    self.document, self._entity_processing_seconds, self._face_peak_seconds
                )
            self._report_snapshot = snapshot
            self._update_model_completeness(snapshot)
        else:
            self.viewport.set_model_check_text("")
            self._same_direction_pairs = ()
            self.edge_issue_group.hide()
            self.edge_issue_warning_button.hide()
        if update_surface_controls and (
            not incremental
            or snapshot.control_net_statuses != list(getattr(self, "_control_net_statuses_by_id", {}).values())
            or snapshot.control_nets != list(getattr(self, "_control_nets_by_id", {}).values())
        ):
            self._update_surface_controls(snapshot)
        hidden_ids = self.object_tree.hidden_entity_ids()
        viewport_snapshot = self._visible_snapshot(snapshot, hidden_ids) if hidden_ids else snapshot
        has_drawable_geometry = bool(
            viewport_snapshot.faces or viewport_snapshot.polylines or viewport_snapshot.control_nets
        )
        turbo_active = self.fast_preview_checkbox.isChecked() and self.play_button.isChecked()
        show_points = not turbo_active or not has_drawable_geometry
        self.viewport.show_snapshot(
            viewport_snapshot,
            selected_id=selected_id,
            labels=self.show_entity_labels,
            label_mode=self.label_mode,
            label_texts=self._label_texts(),
            label_orders=self._label_orders(),
            label_time=self.discovered_order,
            control_net_densities=self.control_net_densities,
            incremental=incremental and not hidden_ids,
            show_points=show_points,
            fast_preview=turbo_active,
        )
        self._show_edge_issue()
        self.focus_face_button.setEnabled(self.viewport.has_rendered_face(selected_id))
        if self.analyzed_face_id is not None:
            self._update_face_assessment()

    @staticmethod
    def _visible_snapshot(snapshot: GeometrySnapshot, hidden_ids: set[int]) -> GeometrySnapshot:
        return replace(
            snapshot,
            points={entity_id: point for entity_id, point in snapshot.points.items()
                    if entity_id not in hidden_ids},
            vertices={entity_id: point for entity_id, point in snapshot.vertices.items()
                      if entity_id not in hidden_ids},
            polylines=[item for item in snapshot.polylines if item.entity_id not in hidden_ids],
            faces=[item for item in snapshot.faces if item.entity_id not in hidden_ids],
            control_nets=[item for item in snapshot.control_nets if item.entity_id not in hidden_ids],
            render_updates=None,
        )

    def _object_visibility_changed(self) -> None:
        if self.current_snapshot is None:
            return
        autozoom = self.viewport.autozoom
        self.viewport.autozoom = False
        try:
            self._show_snapshot(self.current_snapshot, self.current_id, update_surface_controls=False)
        finally:
            self.viewport.autozoom = autozoom

    def _update_preview_warnings(self, snapshot, incremental: bool = False) -> None:
        if self.document is None:
            self.preview_warning_button.hide()
            self.preview_warning_group.hide()
            self.preview_warning_details.clear()
            self._preview_gaps_by_id.clear()
            return
        updates = getattr(snapshot, "render_updates", None)
        if incremental and updates is not None:
            changed_face_ids = {
                entity_id for entity_id in updates
                if (entity := self.document.entity(entity_id)) is not None
                and entity.type_name.upper() == "ADVANCED_FACE"
            }
            for entity_id in changed_face_ids:
                self._preview_gaps_by_id.pop(entity_id, None)
            unmeshed_face_ids = {
                entity_id for entity_id in changed_face_ids
                if updates[entity_id] is None
            }
            changed_gaps = find_face_preview_gaps(
                self.document,
                snapshot,
                self.discovered_order,
                face_ids=unmeshed_face_ids,
                mesh_ids=set(),
            )
            self._preview_gaps_by_id.update({gap.face_id: gap for gap in changed_gaps})
            gaps = tuple(sorted(
                self._preview_gaps_by_id.values(),
                key=lambda gap: self.document.entity(gap.face_id).order,
            ))
        else:
            gaps = find_face_preview_gaps(self.document, snapshot, self.discovered_order)
            self._preview_gaps_by_id = {gap.face_id: gap for gap in gaps}
        notes_by_text: dict[str, list[int]] = {}
        for mesh in snapshot.faces:
            if mesh.preview_note:
                notes_by_text.setdefault(mesh.preview_note, []).append(mesh.entity_id)
        preview_notes = [
            f"#{', #'.join(str(entity_id) for entity_id in entity_ids)}: {note}"
            for note, entity_ids in notes_by_text.items()
        ]
        if not gaps and not preview_notes:
            self.preview_warning_button.hide()
            self.preview_warning_group.hide()
            self.preview_warning_details.clear()
            return

        messages = []
        if gaps:
            messages.append(f"PREVIEW INCOMPLETE · {len(gaps)} face(s) could not be triangulated")
            messages.extend(f"#{gap.face_id} ({gap.surface_type}): {gap.reason}" for gap in gaps)
        if preview_notes:
            if gaps:
                messages.append("")
            messages.append("PREVIEW APPROXIMATION")
            messages.extend(preview_notes)
        details = "\n".join(messages)
        if self.preview_warning_details.toPlainText() != details:
            self.preview_warning_details.setPlainText(details)
        self.preview_warning_group.show()
        if gaps and preview_notes:
            summary = f"⚠ Preview warnings · {len(gaps)} faces, {len(preview_notes)} notes"
        elif gaps:
            summary = f"⚠ Preview incomplete · {len(gaps)} face{'s' if len(gaps) != 1 else ''}"
        else:
            summary = f"⚠ Preview approximation · {len(preview_notes)} note{'s' if len(preview_notes) != 1 else ''}"
        self.preview_warning_button.setText(summary)
        self.preview_warning_button.show()
        self._position_canvas_overlays()

    def _open_preview_warning_details(self):
        self.play_button.setChecked(False)
        if self.details_tabs.isHidden():
            self._toggle_interpretation_panel()
        self.preview_warning_details.setFocus()

    def _update_model_completeness(self, snapshot) -> None:
        if self.document is None:
            return
        report = assess_completeness(self.document, snapshot)
        previous_edge_id = (
            self._same_direction_pairs[self._same_direction_index].edge_id
            if self._same_direction_pairs and self._same_direction_index < len(self._same_direction_pairs)
            else None
        )
        self._same_direction_pairs = report.same_direction_pairs
        self._same_direction_index = next(
            (index for index, pair in enumerate(self._same_direction_pairs)
             if pair.edge_id == previous_edge_id), 0
        )
        self.edge_issue_group.setVisible(bool(self._same_direction_pairs))
        if self._same_direction_pairs:
            count = len(self._same_direction_pairs)
            self.edge_issue_warning_button.setText(
                f"⚠ Orientation issue · {count} edge pair{'s' if count != 1 else ''}"
            )
            self.edge_issue_warning_button.show()
        else:
            self.edge_issue_warning_button.hide()
        self._position_canvas_overlays()
        if previous_edge_id is None and self._same_direction_pairs and self.details_tabs.isHidden():
            self._toggle_interpretation_panel()
        if report.suspicious_normal_count:
            verdict = (
                f"Model check paused · {report.suspicious_normal_count} face(s) have suspected normal issues. "
                "Resolve or review those warnings before checking closure."
            )
        else:
            if report.open_edge_count:
                state = "OPEN BOUNDARIES"
            elif report.non_manifold_edge_count:
                state = "NON-MANIFOLD EDGES"
            elif report.unresolved_edge_count or report.disconnected_loop_count:
                state = "INCOMPLETE TOPOLOGY"
            elif report.inconsistent_edge_count:
                state = "CLOSED EDGE CONNECTIVITY · ORIENTATION INCONSISTENT"
            elif report.meshed_face_count < report.face_count:
                state = "CLOSED EDGE CONNECTIVITY · PREVIEW INCOMPLETE"
            elif report.is_closed:
                state = "CLOSED TOPOLOGY"
            else:
                state = "INCOMPLETE TOPOLOGY"
            verdict = (
                f"Model check · {state} · {report.meshed_face_count}/{report.face_count} faces meshed · "
                f"{report.triangle_count:,} preview triangles · {report.edge_count:,} STEP edges · "
                f"{report.open_edge_count} open · {report.non_manifold_edge_count} overused"
            )
            if report.inconsistent_edge_count:
                verdict += f" · {report.inconsistent_edge_count} same-direction edge pair(s)"
            if report.disconnected_loop_count:
                verdict += f" · {report.disconnected_loop_count} disconnected loop join(s)"
            if report.unresolved_edge_count:
                verdict += f" · {report.unresolved_edge_count} unresolved topology item(s)"
            if report.face_count == 0:
                verdict = "Model check · no ADVANCED_FACE entities were found."
            elif report.meshed_face_count < report.face_count:
                verdict += ". Some faces are unsupported by the preview tessellator."
        if report.suspicious_normal_count or report.face_count == 0:
            summary = verdict
        else:
            summary = f"Model check · {state} · {report.meshed_face_count}/{report.face_count} faces meshed"
            issues = (
                (report.open_edge_count, "open"),
                (report.non_manifold_edge_count, "overused"),
                (report.inconsistent_edge_count, "same-direction pairs"),
                (report.disconnected_loop_count, "disconnected joins"),
                (report.unresolved_edge_count, "unresolved items"),
            )
            summary += "".join(f" · {count} {name}" for count, name in issues if count)
        self.viewport.set_model_check_text(summary,
            verdict + "\nChecks face preview coverage, boundary loops, and directed STEP edge uses. "
            "This check does not detect self-intersections or prove full BREP validity."
        )

    def _step_edge_issue(self, direction: int) -> None:
        if not self._same_direction_pairs:
            return
        self._same_direction_index = (
            self._same_direction_index + direction
        ) % len(self._same_direction_pairs)
        self._show_edge_issue()

    def _open_edge_issue_details(self) -> None:
        if self.details_tabs.isHidden():
            self._toggle_interpretation_panel()
        self.edge_issue_group.setFocus()

    def _show_edge_issue(self) -> None:
        if not self._same_direction_pairs:
            self.highlight_edge_issues_button.setChecked(False)
            self.viewport.set_issue_edge(None)
            return
        pair = self._same_direction_pairs[self._same_direction_index]
        first, second = pair.uses
        self.edge_issue_summary.setText(
            "Two face boundaries traverse this STEP edge in the same effective direction. "
            "Inspect the oriented edges, face bounds, and face senses to locate the cause."
        )
        self.edge_issue_position.setText(
            f"{self._same_direction_index + 1} / {len(self._same_direction_pairs)}"
        )
        self.edge_issue_details.setText(
            f"EDGE_CURVE #{pair.edge_id}\n"
            f"Face #{first.face_id}: ORIENTED_EDGE #{first.oriented_edge_id} "
            f"({'.T.' if first.oriented_forward else '.F.'}), bound "
            f"{'.T.' if first.bound_forward else '.F.'}\n"
            f"Face #{second.face_id}: ORIENTED_EDGE #{second.oriented_edge_id} "
            f"({'.T.' if second.oriented_forward else '.F.'}), bound "
            f"{'.T.' if second.bound_forward else '.F.'}\n"
            f"Effective direction: {'forward' if first.forward else 'reverse'} on both"
        )
        self.edge_issue_edge_button.setText(f"Show edge #{pair.edge_id} details")
        for button, use in zip(self.edge_issue_face_buttons, pair.uses):
            button.setText(f"Show face #{use.face_id} details")
        for button, use in zip(self.edge_issue_use_buttons, pair.uses):
            button.setText(f"Show oriented edge #{use.oriented_edge_id} details")
        if self.highlight_edge_issues_button.isChecked():
            self.viewport.set_issue_overview(self._same_direction_pairs, pair.edge_id)
        else:
            self.viewport.set_issue_edge(pair.edge_id, (first.face_id, second.face_id))

    def _blink_current_edge_issue(self) -> None:
        if self.highlight_edge_issues_button.isChecked():
            self.highlight_edge_issues_button.setChecked(False)
        self.viewport.blink_issue_edge_and_faces()

    def _show_edge_issue_entity(self, which) -> None:
        if not self.document or not self._same_direction_pairs:
            return
        pair = self._same_direction_pairs[self._same_direction_index]
        if which == "edge":
            entity_id = pair.edge_id
        elif isinstance(which, str) and which.startswith("use"):
            entity_id = pair.uses[int(which[-1])].oriented_edge_id
        else:
            entity_id = pair.uses[which].face_id
        entity = self.document.entity(entity_id)
        if entity is None:
            return
        self.details.show_entity(entity, self.document)
        self.tree.focus_entity(entity_id)
        if self.details_tabs.isHidden():
            self._toggle_interpretation_panel()

    @staticmethod
    def _shape_expectation(document: StepDocument) -> str:
        types = {entity.type_name.upper() for entity in document.entities}
        solid_types = {
            "MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP", "CSG_SOLID",
            "BOOLEAN_RESULT", "BLOCK", "RIGHT_ANGULAR_WEDGE", "RIGHT_CIRCULAR_CONE",
            "RIGHT_CIRCULAR_CYLINDER", "SPHERE", "HALF_SPACE_SOLID", "EXTRUDED_AREA_SOLID",
            "REVOLVED_AREA_SOLID", "SWEPT_AREA_SOLID", "FIXED_REFERENCE_SWEPT_AREA_SOLID",
            "SURFACE_CURVE_SWEPT_AREA_SOLID",
        }
        solid_entities = [entity for entity in document.entities if entity.type_name.upper() in solid_types]
        solid_ids = {entity.entity_id for entity in solid_entities}
        nested_solid_ids = {
            reference
            for entity in solid_entities
            for reference in entity.references
            if reference in solid_ids
        }
        solid_count = len(solid_ids - nested_solid_ids) or len(solid_ids)
        surface_model_types = {
            "SHELL_BASED_SURFACE_MODEL", "FACE_BASED_SURFACE_MODEL",
            "MANIFOLD_SURFACE_SHAPE_REPRESENTATION",
        }
        has_surface_model = bool(types & surface_model_types)
        has_surface_geometry = has_surface_model or bool(
            types & {"ADVANCED_FACE", "FACE_SURFACE", "OPEN_SHELL", "CLOSED_SHELL"}
        )
        if solid_count and has_surface_model:
            return f"Expected geometry: {solid_count} solid body/bodies + surface geometry"
        if solid_count == 1:
            return "Expected geometry: 1 solid body"
        if solid_count > 1:
            return f"Expected geometry: {solid_count} solid bodies"
        if has_surface_model:
            return "Expected geometry: non-solid surface model"
        if has_surface_geometry:
            return "Expected geometry: surfaces; no solid body declared"
        return "Expected geometry: no recognized solid or surface body"

    def open_file_directory(self):
        source_path = getattr(self, "source_path", None)
        if not source_path:
            return
        directory = Path(source_path).expanduser().resolve().parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.status.setText(f"Could not open directory: {directory}")

    def _load_recent_files(self) -> list[str]:
        stored = self.settings.value("recent_files", [])
        if isinstance(stored, str):
            stored = [stored]
        return [str(path) for path in (stored or [])][:5]

    def _save_recent_files(self):
        self.settings.setValue("recent_files", self.recent_files[:5])

    def _add_recent_file(self, path: str):
        normalized = str(Path(path).expanduser().resolve())
        self.recent_files = [normalized, *(item for item in self.recent_files if item != normalized)][:5]
        self._save_recent_files()
        self._populate_recent_menu()

    def _populate_recent_menu(self):
        self.recent_menu.clear()
        if not self.recent_files:
            empty_action = self.recent_menu.addAction("No recent files")
            empty_action.setEnabled(False)
            return
        for path in self.recent_files:
            action = self.recent_menu.addAction(path)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, recent_path=path: self.open_path(recent_path))

    def _clear_recent_files(self):
        self.recent_files.clear()
        self._save_recent_files()
        self._populate_recent_menu()

    def _build_ui(self):
        toolbar = QToolBar("Navigation", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.addWidget(self.open_button)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Mode: "))
        toolbar.addWidget(self.mode)
        toolbar.addSeparator()
        toolbar.addWidget(self.legend)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Normals: "))
        toolbar.addWidget(self.normal_display)
        toolbar.addWidget(QLabel(" Arrow limit: "))
        toolbar.addWidget(self.normal_arrow_slider)
        toolbar.addWidget(self.normal_arrow_count_label)
        toolbar.addWidget(QLabel(" Colored faces: "))
        toolbar.addWidget(self.face_color_mode)
        toolbar.addSeparator()
        toolbar.addAction(self.show_mesh_edges_action)
        self.preferences_button = QToolButton(self.menuBar())
        self.preferences_button.setObjectName("preferencesButton")
        self.preferences_button.setText("⚙")
        self.preferences_button.setToolTip("Preferences… (Ctrl+,)")
        self.preferences_button.setAccessibleName("Preferences")
        self.preferences_button.setFixedSize(30, 24)
        self.preferences_button.clicked.connect(self.open_preferences)
        self.menuBar().setCornerWidget(self.preferences_button, Qt.Corner.TopRightCorner)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.interpretation_toggle = QToolButton()
        self.interpretation_toggle.setObjectName("interpretationToggle")
        self.interpretation_toggle.setText("◀")
        self.interpretation_toggle.setToolTip("Show Interpretation panel")
        self.interpretation_toggle.setAccessibleName("Show Interpretation panel")
        self.interpretation_toggle.setFixedSize(36, 72)
        self.interpretation_toggle.clicked.connect(self._toggle_interpretation_panel)
        interpretation_sidebar = QWidget()
        interpretation_sidebar.setObjectName("interpretationSidebar")
        interpretation_sidebar.setMinimumWidth(36)
        sidebar_layout = QHBoxLayout(interpretation_sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        sidebar_layout.addWidget(self.interpretation_toggle, alignment=Qt.AlignmentFlag.AlignTop)
        sidebar_layout.addWidget(self.details_tabs, 1)
        self.details_tabs.hide()
        self.objects_group = QGroupBox("Bodies")
        objects_layout = QVBoxLayout(self.objects_group)
        objects_layout.setContentsMargins(5, 5, 5, 5)
        objects_actions = QHBoxLayout()
        objects_actions.addStretch(1)
        self.show_all_bodies_button = QPushButton("Show all")
        self.show_all_bodies_button.setToolTip("Show all STEP bodies")
        self.show_all_bodies_button.clicked.connect(self.object_tree.show_all)
        objects_actions.addWidget(self.show_all_bodies_button)
        objects_layout.addLayout(objects_actions)
        objects_layout.addWidget(self.object_tree)
        self.objects_group.hide()
        self.left_panel = QSplitter(Qt.Orientation.Vertical)
        self.left_panel.addWidget(self.objects_group)
        self.left_panel.addWidget(self.tree)
        self.left_panel.setChildrenCollapsible(False)
        self.left_panel.setSizes([160, 620])
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.left_panel)
        self.main_splitter.addWidget(self.viewport)
        self.main_splitter.addWidget(interpretation_sidebar)
        self.main_splitter.setSizes([390, 1074, 36])
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setStretchFactor(1, 3)

        playback_panel = QWidget()
        playback_layout = QVBoxLayout(playback_panel)
        playback_layout.setContentsMargins(8, 6, 8, 6)
        playback_controls = QWidget()
        playback_controls_layout = QHBoxLayout(playback_controls)
        playback_controls_layout.setContentsMargins(0, 0, 0, 0)
        play_control = QFrame()
        self.play_control = play_control
        play_control.setObjectName("playControl")
        play_control_layout = QHBoxLayout(play_control)
        play_control_layout.setContentsMargins(4, 2, 4, 2)
        play_control_layout.addWidget(self.play_button)
        self.llm_report_button = QPushButton("Report")
        self.llm_report_button.setToolTip("Inspect and copy a technical report after reaching the last STEP entity")
        self.llm_report_button.setEnabled(False)
        play_control_layout.addWidget(self.llm_report_button)
        playback_controls_layout.addWidget(play_control)
        self.play_button.setFixedWidth(90)
        self.llm_report_button.setFixedWidth(90)
        self.play_button.setToolTip("Play or pause STEP playback (Space)")
        playback_controls_layout.addSpacing(10)
        playback_separator = QFrame()
        playback_separator.setFrameShape(QFrame.Shape.VLine)
        playback_separator.setFrameShadow(QFrame.Shadow.Sunken)
        playback_separator.setFixedHeight(24)
        playback_controls_layout.addWidget(playback_separator)
        playback_controls_layout.addSpacing(4)
        playback_controls_layout.addWidget(self.heal_checkbox)
        self.autozoom_checkbox = QCheckBox("Auto zoom")
        self.autozoom_checkbox.setChecked(self.autozoom_action.isChecked())
        self.autozoom_checkbox.setToolTip(self.autozoom_action.toolTip())
        playback_controls_layout.addWidget(self.autozoom_checkbox)
        playback_controls_layout.addWidget(self.reset_view_button)
        playback_controls_layout.addWidget(self.open_file_directory_button)
        playback_controls_layout.addWidget(self.fit_view_button)
        playback_controls_layout.addWidget(self.focus_face_button)
        playback_controls_layout.addWidget(self.projection_mode)
        playback_controls_layout.addWidget(self.step_backward_button)
        playback_controls_layout.addWidget(self.step_forward_button)
        playback_controls_layout.addWidget(self.playback_progress, 1)
        playback_controls_layout.addWidget(QLabel("Max speed:"))
        playback_controls_layout.addWidget(self.playback_speed_slider)
        playback_controls_layout.addWidget(self.playback_speed_value)
        playback_controls_layout.addWidget(self.playback_speed_help)
        playback_controls_layout.addWidget(self.fast_preview_checkbox)
        playback_controls_layout.addWidget(self.playback_frame_label)
        playback_layout.addWidget(playback_controls)
        self._build_surface_control_panel(self.interpretation_layout)
        # The final model check is drawn over the viewport after playback has
        # reached the final STEP entity.

        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.main_splitter, 1)
        central_layout.addWidget(playback_panel)
        self.setCentralWidget(central_widget)
        self.statusBar().addWidget(self.status, 1)
        self.setStyleSheet(
            "QToolBar { spacing: 6px; padding: 4px; }"
            "QToolButton#preferencesButton { color: #343a40; background: #e4e6e9; border: 1px solid #b9bec4; "
            "border-radius: 4px; font-size: 20px; padding: 0; }"
            "QToolButton#preferencesButton:hover { color: white; background: #3d596d; border-color: #3d596d; }"
            "QToolButton#preferencesButton:pressed { background: #304858; border-color: #304858; }"
            "QToolButton#interpretationToggle { font-size: 20px; font-weight: bold; }"
            "QTreeWidget { alternate-background-color: #20242a; }"
            "QTabWidget::pane { border: 1px solid #343a40; }"
        )

    def _toggle_interpretation_panel(self):
        sizes = self.main_splitter.sizes()
        expanded = not self.details_tabs.isHidden()
        if expanded:
            self._interpretation_width = sizes[2]
            self.details_tabs.hide()
            self.main_splitter.setSizes([sizes[0], sizes[1] + sizes[2] - 36, 36])
        else:
            self.details_tabs.show()
            width = max(376, self._interpretation_width)
            self.main_splitter.setSizes([sizes[0], max(1, sizes[1] - width + 36), width])
        self.interpretation_toggle.setText("◀" if expanded else "▶")
        label = "Show Interpretation panel" if expanded else "Hide Interpretation panel"
        self.interpretation_toggle.setToolTip(label)
        self.interpretation_toggle.setAccessibleName(label)

    @staticmethod
    def _analysis_slider_row(title: str, slider: QSlider, value_label: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setMinimumWidth(104)
        value_label.setMinimumWidth(36)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(title_label)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        return row

    def _connect_signals(self):
        self.open_button.clicked.connect(self.open_dialog)
        self.open_file_directory_button.clicked.connect(self.open_file_directory)
        self.background_color_button.clicked.connect(self._choose_background_color)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.normal_display.currentTextChanged.connect(self._normal_display_changed)
        self.normal_arrow_slider.valueChanged.connect(self._normal_arrow_limit_changed)
        self.face_color_mode.currentTextChanged.connect(self._face_color_mode_changed)
        self.analyze_face_button.clicked.connect(self._analyze_current_face)
        self.exit_face_analysis_button.clicked.connect(self._exit_face_analysis)
        self.extract_face_stl_button.clicked.connect(lambda: self._extract_analyzed_face("stl"))
        self.extract_face_obj_button.clicked.connect(lambda: self._extract_analyzed_face("obj"))
        self.face_triangle_edges_checkbox.toggled.connect(self._face_analysis_display_changed)
        self.face_normals_checkbox.toggled.connect(self._face_analysis_display_changed)
        self.face_normal_scale_slider.valueChanged.connect(self._face_analysis_display_changed)
        self.face_opacity_slider.valueChanged.connect(self._face_analysis_display_changed)
        self.face_context_opacity_slider.valueChanged.connect(self._face_analysis_display_changed)
        self.fit_analyzed_face_button.clicked.connect(self.viewport.fit_analysis_face)
        self.reset_face_inspection_button.clicked.connect(self._reset_face_analysis_controls)
        self.preview_warning_button.clicked.connect(self._open_preview_warning_details)
        self.edge_issue_warning_button.clicked.connect(self._open_edge_issue_details)
        self.previous_edge_issue_button.clicked.connect(lambda: self._step_edge_issue(-1))
        self.next_edge_issue_button.clicked.connect(lambda: self._step_edge_issue(1))
        self.edge_issue_edge_button.clicked.connect(lambda: self._show_edge_issue_entity("edge"))
        for index, button in enumerate(self.edge_issue_face_buttons):
            button.clicked.connect(lambda _checked=False, index=index: self._show_edge_issue_entity(index))
        for index, button in enumerate(self.edge_issue_use_buttons):
            button.clicked.connect(lambda _checked=False, index=index: self._show_edge_issue_entity(
                f"use{index}"
            ))
        self.fit_edge_issue_button.clicked.connect(self.viewport.fit_issue_edge)
        self.blink_edge_issue_button.clicked.connect(self._blink_current_edge_issue)
        self.highlight_edge_issues_button.toggled.connect(self._show_edge_issue)
        self.tree.selected_entity.connect(self._tree_entity_selected)
        self.object_tree.visibility_changed.connect(self._object_visibility_changed)
        self.tree.face_analysis_requested.connect(self._tree_face_analysis_requested)
        self.viewport.face_picked.connect(self._viewport_face_picked)
        self.viewport.face_double_clicked.connect(self._viewport_face_double_clicked)
        self.viewport.background_clicked.connect(self._viewport_background_clicked)
        self.viewport.orientation_warning_clicked.connect(self._show_orientation_warning)
        self.invert_mouse_horizontal_action.toggled.connect(self._invert_mouse_horizontal_changed)
        self.invert_mouse_vertical_action.toggled.connect(self._invert_mouse_vertical_changed)
        self.autozoom_action.toggled.connect(self._autozoom_changed)
        self.autozoom_action.toggled.connect(self.autozoom_checkbox.setChecked)
        self.autozoom_checkbox.toggled.connect(self.autozoom_action.setChecked)
        self.show_xyz_origin_action.toggled.connect(self._show_xyz_origin_changed)
        self.show_xy_grid_action.toggled.connect(self._show_xy_grid_changed)
        self.show_mesh_edges_action.toggled.connect(self._show_mesh_edges_changed)
        self.mesh_edges_checkbox.toggled.connect(self.show_mesh_edges_action.setChecked)
        self.mesh_edge_opacity_slider.valueChanged.connect(self._mesh_edge_opacity_changed)
        self.show_entity_labels_action.toggled.connect(self._show_entity_labels_changed)
        self.line_index_labels_action.triggered.connect(lambda: self._label_mode_changed("index"))
        self.type_labels_action.triggered.connect(lambda: self._label_mode_changed("type"))
        self.play_button.toggled.connect(self._playback_toggled)
        self.heal_checkbox.toggled.connect(self._heal_changed)
        self.fast_preview_checkbox.toggled.connect(self._fast_preview_changed)
        self.reset_view_button.clicked.connect(self.viewport.reset_camera)
        self.fit_view_button.clicked.connect(self.viewport.fit_camera)
        self.focus_face_button.clicked.connect(self._focus_selected_face)
        self.projection_mode.currentTextChanged.connect(self._projection_mode_changed)
        # Qt shortcuts work even when a child overlay has keyboard focus;
        # do not rely on VTK's C++ interactor dispatching Python OnChar.
        self.reset_view_action = QAction("Reset view", self.viewport)
        self.reset_view_action.setShortcut("R")
        self.reset_view_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.reset_view_action.triggered.connect(self.viewport.reset_camera)
        self.viewport.addAction(self.reset_view_action)
        self.fit_view_action = QAction("Fit model", self.viewport)
        self.fit_view_action.setShortcut("F")
        self.fit_view_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.fit_view_action.triggered.connect(self.viewport.fit_camera)
        self.viewport.addAction(self.fit_view_action)
        self.step_backward_button.clicked.connect(lambda: self.step_playback(-1))
        self.step_forward_button.clicked.connect(lambda: self.step_playback(1))
        self.playback_progress.valueChanged.connect(self._playback_frame_changed)
        self.llm_report_button.clicked.connect(self._show_llm_report)
        self.playback_speed_slider.valueChanged.connect(self._playback_speed_changed)
        self.surface_control_toggle.toggled.connect(self._surface_controls_toggled)
        self._update_playback_controls()

        # QVTKRenderWindowInteractor consumes key events for its own camera
        # controls, so bridge the application hotkeys at the Qt widget level.
        self.viewport.installEventFilter(self)
        vtk_widget = getattr(self.viewport, "widget", None)
        if vtk_widget is not None:
            vtk_widget.installEventFilter(self)
        self.hotkey_help_action = QAction("Show hotkeys", self)
        self.hotkey_help_action.setShortcut("H")
        self.hotkey_help_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.hotkey_help_action.triggered.connect(self._show_hotkey_help)
        self.addAction(self.hotkey_help_action)
        self._position_canvas_overlays()

    def _normal_display_changed(self, mode: str) -> None:
        if mode == "Face orientation":
            self.face_color_mode.setCurrentText("normals")
        self.settings.setValue("normal_display", mode)
        self.settings.sync()
        self.viewport.set_normal_display(mode)

    def _face_color_mode_changed(self, mode: str) -> None:
        self.settings.setValue("face_color_mode", mode)
        self.settings.sync()
        self.viewport.set_face_color_mode(mode)

    def _normal_arrow_limit_changed(self, limit: int) -> None:
        self.normal_arrow_limit = limit
        self.normal_arrow_count_label.setText(str(limit))
        self.settings.setValue("normal_arrow_limit", limit)
        self.viewport.set_normal_arrow_limit(limit)

    def _build_surface_control_panel(self, parent_layout):
        """Create the collapsible mixer displayed only for B-spline surfaces."""

        self.surface_control_panel = QFrame()
        panel_layout = QVBoxLayout(self.surface_control_panel)
        panel_layout.setContentsMargins(0, 2, 0, 0)
        self.surface_control_toggle = QToolButton()
        self.surface_control_toggle.setText("Surface control nets")
        self.surface_control_toggle.setCheckable(True)
        self.surface_control_toggle.setChecked(True)
        self.surface_control_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.surface_control_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.surface_control_toggle.setToolTip("Show or hide B-spline control-net channels")
        panel_layout.addWidget(self.surface_control_toggle)
        self.surface_control_scroll = QScrollArea()
        self.surface_control_scroll.setWidgetResizable(True)
        self.surface_control_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.surface_control_scroll.setFixedHeight(154)
        self.surface_control_content = QWidget()
        self.surface_control_layout = QHBoxLayout(self.surface_control_content)
        self.surface_control_layout.setContentsMargins(8, 2, 8, 2)
        self.surface_control_layout.setSpacing(12)
        self.surface_control_scroll.setWidget(self.surface_control_content)
        panel_layout.addWidget(self.surface_control_scroll)
        self.surface_control_panel.setVisible(False)
        parent_layout.insertWidget(max(0, parent_layout.count() - 1), self.surface_control_panel)

    def _surface_controls_toggled(self, expanded: bool):
        self.surface_control_scroll.setVisible(expanded)
        self.surface_control_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def _clear_surface_control_channels(self):
        while self.surface_control_layout.count():
            item = self.surface_control_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._surface_control_status_labels = {}
        self.surface_control_sliders = {}

    def _update_surface_controls(self, snapshot):
        statuses = snapshot.control_net_statuses
        self._control_nets_by_id = {control_net.entity_id: control_net for control_net in snapshot.control_nets}
        self._control_net_statuses_by_id = {status.entity_id: status for status in statuses}
        self._clear_surface_control_channels()
        self.surface_control_panel.setVisible(bool(statuses))
        if not statuses:
            return
        for status in statuses:
            surface_id = status.entity_id
            control_net = self._control_nets_by_id.get(surface_id)
            density = self.control_net_densities.setdefault(surface_id, 100)
            channel = QFrame()
            channel.setFrameShape(QFrame.Shape.StyledPanel)
            channel.setMinimumWidth(128)
            channel_layout = QVBoxLayout(channel)
            channel_layout.setContentsMargins(8, 5, 8, 5)
            title = QLabel(f"#{surface_id}")
            title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            channel_layout.addWidget(title)
            status_label = QLabel(self._surface_control_status_text(status, control_net, density))
            status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            status_label.setWordWrap(True)
            channel_layout.addWidget(status_label)
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(0, 100)
            slider.setSingleStep(1)
            slider.setPageStep(10)
            slider.setValue(density)
            slider.setEnabled(status.state == "ready" and control_net is not None)
            slider.setAccessibleName(f"Control-net density for surface #{surface_id}")
            slider.setAccessibleDescription(
                "Adjust the number of evenly distributed control points shown for this surface."
            )
            slider.setToolTip("Control-net density: coarse at the bottom, full detail at the top")
            slider.valueChanged.connect(
                lambda value, current_surface_id=surface_id: self._control_net_density_changed(
                    current_surface_id, value
                )
            )
            channel_layout.addWidget(slider, 1, Qt.AlignmentFlag.AlignHCenter)
            self._surface_control_status_labels[surface_id] = status_label
            self.surface_control_sliders[surface_id] = slider
            self.surface_control_layout.addWidget(channel)
        self.surface_control_layout.addStretch(1)

    @staticmethod
    def _surface_control_status_text(status, control_net, density: int) -> str:
        if status.state != "ready" or control_net is None:
            return status.description
        displayed = control_net.at_density(density)
        return f"Ready\n{len(displayed.point_markers)} / {len(control_net.point_markers)} points"

    def _control_net_density_changed(self, surface_id: int, density: int):
        self.control_net_densities[surface_id] = density
        status = self._control_net_statuses_by_id.get(surface_id)
        control_net = self._control_nets_by_id.get(surface_id)
        label = self._surface_control_status_labels.get(surface_id)
        if status is not None and label is not None:
            label.setText(self._surface_control_status_text(status, control_net, density))
        self._refresh_viewport_with_surface_controls(update_surface_controls=False)

    def eventFilter(self, watched, event):
        viewport_widget = getattr(self.viewport, "widget", None)
        if watched is viewport_widget and event.type() == QEvent.Type.Resize:
            self._position_canvas_overlays()
        if watched in {self.viewport, viewport_widget} and event.type() == QEvent.Type.KeyPress:
            if event.isAutoRepeat() or event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            ):
                return super().eventFilter(watched, event)
            if event.key() == Qt.Key.Key_Space:
                self.play_button.setChecked(not self.play_button.isChecked())
                event.accept()
                return True
            if event.key() == Qt.Key.Key_H:
                self._show_hotkey_help()
                event.accept()
                return True
            if event.key() == Qt.Key.Key_R:
                self.viewport.reset_camera()
                event.accept()
                return True
            if event.key() == Qt.Key.Key_F:
                self.viewport.fit_camera()
                event.accept()
                return True
            if event.key() == Qt.Key.Key_L:
                self.show_entity_labels_action.trigger()
                event.accept()
                return True
            if event.key() == Qt.Key.Key_T:
                action = self.line_index_labels_action if self.label_mode == "type" else self.type_labels_action
                action.trigger()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _position_canvas_overlays(self):
        canvas = getattr(self.viewport, "widget", self.viewport)
        if canvas is None:
            return
        margin = 14
        if self.shape_expectation_label.parentWidget() is canvas and self.shape_expectation_label.isVisible():
            self.shape_expectation_label.adjustSize()
            self.shape_expectation_label.move(
                max(margin, (canvas.width() - self.shape_expectation_label.width()) // 2), margin
            )
            self.shape_expectation_label.raise_()
        if self.preview_warning_button.parentWidget() is canvas and self.preview_warning_button.isVisible():
            self.preview_warning_button.setMaximumWidth(max(1, canvas.width() - 2 * margin))
            self.preview_warning_button.adjustSize()
            self.preview_warning_button.move(
                max(margin, canvas.width() - self.preview_warning_button.width() - margin), margin
            )
            self.preview_warning_button.raise_()
        hotkey_top = margin
        if self.edge_issue_warning_button.parentWidget() is canvas and self.edge_issue_warning_button.isVisible():
            self.edge_issue_warning_button.setMaximumWidth(max(1, canvas.width() - 2 * margin))
            self.edge_issue_warning_button.adjustSize()
            self.edge_issue_warning_button.move(margin, margin)
            self.edge_issue_warning_button.raise_()
            hotkey_top += self.edge_issue_warning_button.height() + 8
        if self.hotkey_legend.parentWidget() is canvas:
            self.hotkey_legend.adjustSize()
            self.hotkey_legend.move(margin, hotkey_top)
            self.hotkey_legend.raise_()

    def _show_hotkey_help(self):
        self.hotkey_fade.stop()
        self.hotkey_opacity.setOpacity(1.0)
        self._position_canvas_overlays()
        self.hotkey_legend.show()
        self.hotkey_legend.raise_()
        self.hotkey_hide_timer.start(8200)

    def _fade_hotkey_help(self):
        self.hotkey_fade.stop()
        self.hotkey_fade.setStartValue(1.0)
        self.hotkey_fade.setEndValue(0.0)
        self.hotkey_fade.start()

    def _update_playback_controls(self):
        has_entities = bool(self.document and self.document.entities)
        self._update_primary_action_styles(has_entities)
        self.play_button.setEnabled(has_entities)
        self.step_backward_button.setEnabled(has_entities and self.playback_order > 0)
        self.step_forward_button.setEnabled(
            has_entities and self.document is not None and self.playback_order < len(self.document.entities) - 1
        )
        self.playback_progress.setEnabled(has_entities)
        total = len(self.document.entities) if self.document else 0
        self.playback_frame_label.setText(f"{self.playback_order + 1 if has_entities else 0} / {total}")
        self.llm_report_button.setEnabled(
            has_entities and self._report_snapshot is not None
        )

    def _show_llm_report(self):
        if not self.document or not self._report_snapshot or not getattr(self, "source_path", None):
            return
        report = build_llm_report(
            self.source_path, self.document, self._report_snapshot, self.tree.playback_step_ms,
            self._entity_processing_seconds, self._face_peak_seconds,
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("STEP report for LLM")
        dialog.resize(840, 660)
        layout = QVBoxLayout(dialog)
        details = QPlainTextEdit(dialog)
        details.setReadOnly(True)
        details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        details.setPlainText(report)
        layout.addWidget(details)
        actions = QHBoxLayout()
        copy_button = QPushButton("Copy report", dialog)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(copy_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        dialog.exec()

    def _update_primary_action_styles(self, has_entities: bool):
        orange = (
            "QPushButton { background: #c85f17; color: white; font-weight: bold; "
            "padding: 6px 12px; border: 1px solid #ffad62; border-radius: 4px; }"
            "QPushButton:hover { background: #df7021; }"
        )
        pastel_green = (
            "QPushButton { background: #a9d8b0; color: #183321; font-weight: bold; "
            "padding: 7px 14px; border: 1px solid #d0eed4; border-radius: 4px; }"
            "QPushButton:hover { background: #bce5c2; }"
            "QPushButton:checked { background: #8fc99a; }"
        )
        report_red = (
            "QPushButton { background: #c83d3d; color: white; font-weight: bold; "
            "padding: 7px 14px; border: 1px solid #f09999; border-radius: 4px; }"
            "QPushButton:hover { background: #db5151; }"
        )
        self.open_button.setStyleSheet(orange)
        self.play_button.setStyleSheet(
            pastel_green if has_entities else "QPushButton { padding: 6px 12px; }"
        )
        if self._report_health_score is None:
            self.llm_report_button.setStyleSheet("QPushButton { padding: 7px 14px; }")
            self.llm_report_button.setToolTip(
                "Inspect and copy a technical report after reaching the last STEP entity"
            )
        else:
            warning = bool(self._report_pain and report_red_reasons(
                self._report_health_score, self._report_pain
            ))
            self.llm_report_button.setStyleSheet(report_red if warning else pastel_green)
            pain_text = f" Processing pain: {self._report_pain.score}/100." if self._report_pain else ""
            self.llm_report_button.setToolTip(
                f"Preview and topology health: {self._report_health_score}/100.{pain_text} "
                "Click to inspect and copy the report."
            )
        self.play_control.setStyleSheet(
            "QFrame#playControl { background: #26372a; border: 2px solid #a9d8b0; border-radius: 6px; }"
            if has_entities
            else "QFrame#playControl { background: #20242a; border: 1px solid #454c55; border-radius: 5px; }"
        )

    def _playback_toggled(self, playing: bool):
        if not playing:
            self._last_playback_step_finished = None
            self.playback_timer.stop()
            self.play_button.setText("Play")
            return
        if not self.document or not self.document.entities:
            self.play_button.setChecked(False)
            return
        if self.playback_speed == 0:
            self.playback_speed_slider.setValue(1)
        at_end = self.playback_order >= len(self.document.entities) - 1
        if not self._confirm_playback_duration(0 if at_end else self.playback_order):
            self.play_button.setChecked(False)
            return
        self.mode.setCurrentText("Playback")
        if at_end:
            self._set_playback_order(0)
        else:
            self.tree.focus_playback_entity(self.document.entities[self.playback_order].entity_id)
        self._last_playback_step_finished = monotonic()
        self.playback_timer.start()
        self.play_button.setText("Pause")

    def _playback_estimate_ack_key(self) -> str:
        path = getattr(self, "source_path", "")
        resolved = Path(path).expanduser().resolve() if path else Path(".")
        return f"playback_estimate_ack/{resolved}"

    def _playback_estimate_acknowledged(self) -> bool:
        key = self._playback_estimate_ack_key()
        return bool(self.settings.value(key, False, type=bool))

    def _confirm_playback_duration(self, start_order: int) -> bool:
        """Gate a play-through that may take hours, with a per-file opt-out."""

        estimate = estimate_playback_duration(
            self.document,
            interval_ms=self._playback_interval(),
            start_order=start_order,
        )
        if estimate.seconds <= PLAYBACK_ESTIMATE_WARN_SECONDS:
            return True
        if self._playback_estimate_acknowledged():
            return True
        message = describe_playback_estimate(
            estimate, turbo=self.fast_preview_checkbox.isChecked()
        )
        answer = QMessageBox(self)
        answer.setIcon(QMessageBox.Icon.Warning)
        answer.setWindowTitle("This may take a while")
        answer.setText(message)
        acknowledge = QCheckBox("Do not ask again for this file")
        answer.setCheckBox(acknowledge)
        answer.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        answer.setDefaultButton(QMessageBox.StandardButton.No)
        yes = answer.button(QMessageBox.StandardButton.Yes)
        yes.setText("Continue anyway")
        answer.exec()
        acknowledged = answer.clickedButton() is yes
        if acknowledged and acknowledge.isChecked():
            self.settings.setValue(self._playback_estimate_ack_key(), True)
            self.settings.sync()
        return acknowledged

    def _playback_tick(self):
        if not self.document or self.playback_order >= len(self.document.entities) - 1:
            self.play_button.setChecked(False)
            return
        previous_finished = self._last_playback_step_finished
        if self.fast_preview_checkbox.isChecked():
            start_order = self.playback_order
            target_order = min(
                len(self.document.entities) - 1,
                start_order + self.FAST_PREVIEW_BATCH_SIZE,
            )
            pending_updates = {}
            snapshot = None
            batch_started = monotonic()
            for order in range(start_order + 1, target_order + 1):
                snapshot = self._set_playback_order(order, present=False)
                if snapshot is not None and snapshot.render_updates is not None:
                    pending_updates.update(snapshot.render_updates)
                if monotonic() - batch_started >= self.FAST_PREVIEW_BATCH_BUDGET_SECONDS:
                    break
            if snapshot is not None:
                # A batched render must include every visual change accumulated
                # since the last viewport refresh, not just the final entity.
                snapshot = replace(snapshot, render_updates=pending_updates)
                self._present_playback_order(snapshot)
        else:
            self._set_playback_order(self.playback_order + 1)
        finished = monotonic()
        if previous_finished is not None:
            entity = self.document.entities[self.playback_order]
            self.tree.set_playback_step_time(entity.entity_id, (finished - previous_finished) * 1000)
        self._last_playback_step_finished = finished
        if self.playback_order >= len(self.document.entities) - 1:
            self.play_button.setChecked(False)

    def _playback_frame_changed(self, order: int):
        if self.document and self.document.entities and order != self.playback_order:
            self._set_playback_order(order)

    def _set_playback_order(self, order: int, *, present: bool = True):
        if not self.document or not self.document.entities:
            return None
        step_started = monotonic()
        previous_order = self.playback_order
        self.playback_order = max(0, min(len(self.document.entities) - 1, order))
        forward_step = self.playback_order == previous_order + 1
        self.playback_progress.blockSignals(True)
        self.playback_progress.setValue(self.playback_order)
        self.playback_progress.blockSignals(False)
        entity = self.document.entities[self.playback_order]
        self.current_id = entity.entity_id
        self.discovered_order = self.playback_order
        builder = self.geometry_builder or GeometryBuilder(self.document)
        measure = self.playback_order not in self._timed_playback_orders and self._report_snapshot is None
        snapshot = builder.build(self.playback_order, measure_times=measure)
        if measure:
            self._timed_playback_orders.add(self.playback_order)
            for entity_id, seconds in snapshot.entity_build_seconds.items():
                self._entity_processing_seconds[entity_id] = (
                    self._entity_processing_seconds.get(entity_id, 0.0) + seconds
                )
                measured_entity = self.document.entity(entity_id)
                if measured_entity is not None and measured_entity.type_name.upper() == "ADVANCED_FACE":
                    self._face_peak_seconds[entity_id] = max(
                        self._face_peak_seconds.get(entity_id, 0.0), seconds
                    )
        if not present:
            return snapshot
        self.details.show_entity(entity, self.document)
        self._update_face_analysis_actions(entity)
        self._present_playback_order(snapshot, incremental=forward_step)
        self.tree.set_playback_step_time(entity.entity_id, (monotonic() - step_started) * 1000)
        return snapshot

    def _present_playback_order(self, snapshot, *, incremental: bool = True):
        entity = self.document.entities[self.playback_order]
        self._show_snapshot(
            snapshot,
            entity.entity_id,
            incremental=incremental,
        )
        if self.mode.currentText() == "Playback":
            self.tree.focus_playback_entity(entity.entity_id)
        self.status.setText(
            f"Playback: #{entity.entity_id} {entity.type_name} · "
            f"{self.playback_order + 1}/{len(self.document.entities)}"
        )
        self._update_playback_controls()

    def _analyze_current_face(self):
        if self.document is None or self.current_id is None:
            return
        face = self.document.entity(self.current_id)
        if face is None or face.type_name.upper() != "ADVANCED_FACE":
            return
        surface_id = next(
            (argument.entity_id for argument in face.arguments if isinstance(argument, StepReference)),
            None,
        )
        builder = self.geometry_builder or GeometryBuilder(self.document)
        boundary_edge_ids, boundary_vertex_ids = builder.face_boundary_entity_ids(face.entity_id)
        self._analysis_snapshot = builder.build()
        self.analyzed_face_id = face.entity_id
        self.viewport.set_face_analysis(
            face.entity_id,
            surface_id,
            boundary_edge_ids,
            boundary_vertex_ids,
            refresh=False,
        )
        self._show_snapshot(self._analysis_snapshot, face.entity_id)
        self._update_face_analysis_actions(face)

    def _exit_face_analysis(self):
        self.analyzed_face_id = None
        self._analysis_snapshot = None
        self._preview_gaps_by_id.clear()
        self.viewport.set_face_analysis(None, refresh=False)
        self._refresh_viewport()
        entity = self.document.entity(self.current_id) if self.document and self.current_id is not None else None
        self._update_face_analysis_actions(entity)

    def _face_analysis_display_changed(self, *_args):
        normal_scale = self.face_normal_scale_slider.value()
        face_opacity = self.face_opacity_slider.value()
        context_opacity = self.face_context_opacity_slider.value()
        self.face_normal_scale_value.setText(f"{normal_scale}%")
        self.face_opacity_value.setText(f"{face_opacity}%")
        self.face_context_opacity_value.setText(f"{context_opacity}%")
        self.face_normal_scale_slider.setEnabled(self.face_normals_checkbox.isChecked())
        self.face_normal_scale_row.setVisible(self.face_normals_checkbox.isChecked())
        self.viewport.set_face_analysis_display(
            face_opacity=face_opacity / 100.0,
            context_opacity=context_opacity / 100.0,
            triangle_edges=self.face_triangle_edges_checkbox.isChecked(),
            show_normals=self.face_normals_checkbox.isChecked(),
            normal_scale=normal_scale / 100.0,
        )

    def _reset_face_analysis_controls(self):
        widgets = (
            self.face_triangle_edges_checkbox,
            self.face_normals_checkbox,
            self.face_normal_scale_slider,
            self.face_opacity_slider,
            self.face_context_opacity_slider,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.face_triangle_edges_checkbox.setChecked(True)
        self.face_normals_checkbox.setChecked(False)
        self.face_normal_scale_slider.setValue(100)
        self.face_opacity_slider.setValue(86)
        self.face_context_opacity_slider.setValue(7)
        for widget in widgets:
            widget.blockSignals(False)
        self._face_analysis_display_changed()

    def _update_face_analysis_actions(self, entity):
        is_face = entity is not None and entity.type_name.upper() == "ADVANCED_FACE"
        self.analyze_face_button.setVisible(is_face and entity.entity_id != self.analyzed_face_id)
        analyzing = self.analyzed_face_id is not None
        self.exit_face_analysis_button.setVisible(analyzing)
        self.extract_face_stl_button.setVisible(analyzing)
        self.extract_face_obj_button.setVisible(analyzing)
        self.face_inspection_group.setVisible(analyzing)
        if analyzing:
            self._update_face_assessment()

    def _update_face_assessment(self):
        if self.analyzed_face_id is None or self.document is None:
            return
        builder = self.geometry_builder or GeometryBuilder(self.document)
        mesh = next(
            (
                face
                for face in getattr(self.current_snapshot, "faces", ())
                if face.entity_id == self.analyzed_face_id
            ),
            None,
        )
        assessment = builder.assess_face(self.analyzed_face_id, mesh)
        colors = {
            "OK": "#61d095",
            "BAD": "#ff6b6b",
            "JUDGEMENT CALL": "#f4c95d",
        }
        self.face_assessment_verdict.setText(
            f"Assessment: {assessment.category} · {assessment.surface_type}"
        )
        self.face_assessment_verdict.setStyleSheet(
            f"font-weight: bold; color: {colors[assessment.category]};"
        )
        reason_lines = "\n".join(f"• {reason}" for reason in assessment.reasons)
        self.face_assessment_reasons.setText(
            f"{reason_lines}\nHeuristic only; not full B-rep validation."
        )

    def _extract_analyzed_face(self, file_format: str):
        if file_format not in {"stl", "obj"} or self.document is None or self.analyzed_face_id is None:
            return
        face = self.document.entity(self.analyzed_face_id)
        if face is None:
            return
        source_path = getattr(self, "source_path", None)
        suggested_name = (
            f"{Path(source_path).stem}_face_{face.entity_id}.{file_format}"
            if source_path else f"steposcope_face_{face.entity_id}.{file_format}"
        )
        suggested = str(Path(source_path).with_name(suggested_name)) if source_path else suggested_name
        file_filter = "STL files (*.stl)" if file_format == "stl" else "OBJ files (*.obj)"
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Extract face #{face.entity_id} to .{file_format}",
            suggested,
            file_filter,
        )
        if not path:
            return
        destination = Path(path).expanduser()
        suffix = f".{file_format}"
        if destination.suffix.lower() != suffix:
            destination = destination.with_suffix(suffix)
        try:
            snapshot = self._analysis_snapshot or (self.geometry_builder or GeometryBuilder(self.document)).build()
            mesh = next((item for item in snapshot.faces if item.entity_id == face.entity_id), None)
            if mesh is None:
                raise ValueError("The analyzed face has no supported preview mesh to export")
            triangle_count = (
                save_mesh_as_stl(mesh, destination)
                if file_format == "stl" else
                save_mesh_as_obj(mesh, destination)
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not extract face", str(exc))
            return
        self.status.setText(
            f"Extracted face #{face.entity_id} with {triangle_count} triangles to {destination.name}"
        )

    def step_playback(self, delta: int):
        if not self.document or not self.document.entities:
            return
        self.play_button.setChecked(False)
        self._set_playback_order(self.playback_order + delta)

    def _playback_speed_changed(self, value: int):
        self.playback_speed = value
        self.playback_speed_value.setText(f"{value}%")
        self.playback_timer.setInterval(self._playback_interval())
        if value == 0 and self.play_button.isChecked():
            self.play_button.setChecked(False)
        self.settings.setValue("playback_speed_percent", value)
        self.settings.sync()

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open STEP file", "", "STEP files (*.step *.stp *.STEP *.STP);;All files (*)")
        if path:
            self.open_path(path)

    def save_geometry_dialog(self):
        if self.document is None:
            return
        source_path = getattr(self, "source_path", None)
        suggested = str(Path(source_path).with_name(f"{Path(source_path).stem}_geometry.step")) if source_path else "geometry.step"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Geometry only STEP file", suggested,
            "STEP files (*.step *.stp);;All files (*)",
        )
        if not path:
            return
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(".step")
        if source_path and destination.expanduser().resolve() == Path(source_path).expanduser().resolve():
            QMessageBox.warning(self, "Choose another file", "Save the geometry-only copy under a different name.")
            return
        try:
            count = save_geometry_only_step(self.document, destination)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save STEP file", str(exc))
            return
        self.status.setText(f"Saved {count} geometry and supporting entities to {destination.name}")

    def open_path(self, path: str):
        self.play_button.setChecked(False)
        try:
            self.document = StepDocument.from_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not parse STEP file", str(exc))
            return
        self.analyzed_face_id = None
        self._same_direction_pairs = ()
        self._same_direction_index = 0
        self.highlight_edge_issues_button.blockSignals(True)
        self.highlight_edge_issues_button.setChecked(False)
        self.highlight_edge_issues_button.blockSignals(False)
        self.edge_issue_group.hide()
        self.edge_issue_warning_button.hide()
        self.viewport.set_issue_edge(None)
        self._analysis_snapshot = None
        self.viewport.set_face_analysis(None, refresh=False)
        self._update_face_analysis_actions(None)
        self._add_recent_file(path)
        self.source_path = path
        self.open_file_directory_button.setEnabled(True)
        self.shape_expectation_label.setText(self._shape_expectation(self.document))
        self.shape_expectation_label.show()
        self._position_canvas_overlays()
        self.save_geometry_action.setEnabled(True)
        self.geometry_builder = GeometryBuilder(self.document, heal_orientations=self.heal_checkbox.isChecked())
        self.object_tree.set_document(self.document)
        body_count = len(self.object_tree.bodies)
        single_body = body_count == 1
        self.objects_group.setTitle("" if single_body else "Bodies")
        self.show_all_bodies_button.setVisible(not single_body)
        self.object_tree.setMaximumHeight(26 if single_body else 16777215)
        self.objects_group.setMaximumHeight(42 if single_body else 16777215)
        self.objects_group.setVisible(bool(body_count))
        if body_count > 1 and self.left_panel.sizes()[0] < 100:
            sizes = self.left_panel.sizes()
            self.left_panel.setSizes([160, max(1, sum(sizes) - 160)])
        self.viewport.fit_next_snapshot()
        self.control_net_densities.clear()
        self.playback_order = 0
        self.discovered_order = -1
        self.current_snapshot = None
        self._report_snapshot = None
        self._report_health_score = None
        self._report_pain = None
        self._entity_processing_seconds.clear()
        self._face_peak_seconds.clear()
        self._timed_playback_orders.clear()
        self.playback_progress.setRange(0, max(0, len(self.document.entities) - 1))
        self.playback_progress.setValue(0)
        self._update_playback_controls()
        self.setWindowTitle(f"steposcope — {Path(path).name}")
        self.status.setText(f"{len(self.document.entities)} entities")
        self.viewport.set_model_check_text("")
        self.current_id = self.document.entities[0].entity_id if self.document.entities else None
        self._refresh_tree()
        if self.document.entities:
            self.select_entity(self.document.entities[0].entity_id)
            if self.mode.currentText() == "Playback":
                self.tree.focus_playback_entity(self.document.entities[0].entity_id)
            # The initial VTK render and Qt layout must settle before fitting
            # the origin axes and grid to the viewport.
            QTimer.singleShot(150, self.viewport.reset_camera)
        else:
            self.details.clear()
            self.viewport.set_model_check_text("")
            self._show_snapshot(GeometrySnapshot(), None)

    def _refresh_tree(self):
        if self.document:
            self.tree.set_document(
                self.document,
                self.mode.currentText(),
                geometry_only=self.geometry_only_action.isChecked(),
            )
            if self.current_id not in self.tree.visible_entity_ids and self.tree.visible_entity_ids:
                first_visible = self.tree.visible_entity_ids[0]
                self.select_entity(first_visible)
                self.tree.focus_entity(first_visible)
            elif self.current_id in self.tree.visible_entity_ids:
                self.tree.focus_entity(self.current_id)

    def _geometry_filter_changed(self, _enabled: bool):
        self._refresh_tree()

    def _mode_changed(self, mode: str):
        self.legend.setText(
            "<span style='color:#4fc3f7'>Geometry</span>  "
            "<span style='color:#ffb74d'>Topology</span>  "
            "<span style='color:#ce93d8'>Structure</span>  "
            "<span style='color:#90a4ae'>Metadata</span>"
            if mode == "Semantic" else ""
        )
        self._refresh_tree()
        if mode == "Playback" and self.document and self.document.entities:
            self.tree.focus_playback_entity(self.document.entities[self.playback_order].entity_id)

    def _tree_entity_selected(self, entity_id: int):
        if not self.document:
            return
        if self.mode.currentText() == "Playback":
            self.play_button.setChecked(False)
            entity = self.document.entity(entity_id)
            if entity is not None:
                self._set_playback_order(entity.order)
        else:
            self.select_entity(entity_id)

    def _viewport_face_picked(self, entity_id: int):
        if self.document is None or self.current_snapshot is None:
            return
        entity = self.document.entity(entity_id)
        if entity is None:
            return
        self.play_button.setChecked(False)
        self.current_id = entity_id
        self.details.show_entity(entity, self.document)
        self._update_face_analysis_actions(entity)
        autozoom = self.viewport.autozoom
        self.viewport.autozoom = False
        try:
            self._show_snapshot(self.current_snapshot, entity_id, update_surface_controls=False)
        finally:
            self.viewport.autozoom = autozoom
        self.status.setText(f"#{entity_id} {entity.type_name} · {entity.order + 1}/{len(self.document.entities)}")
        was_blocked = self.tree.blockSignals(True)
        try:
            self.tree.focus_entity(entity_id)
        finally:
            self.tree.blockSignals(was_blocked)

    def _viewport_face_double_clicked(self, entity_id: int):
        if self.document is None:
            return
        entity = self.document.entity(entity_id)
        if entity is None or entity.type_name.upper() != "ADVANCED_FACE":
            return
        self._viewport_face_picked(entity_id)
        if self.analyzed_face_id == entity_id:
            self._exit_face_analysis()
            return
        self._analyze_current_face()
        self.viewport.fit_analysis_face()
        self.details_tabs.setCurrentIndex(0)
        if self.details_tabs.isHidden():
            self._toggle_interpretation_panel()

    def _viewport_background_clicked(self):
        if self.document is None or self.current_snapshot is None:
            return
        if self.current_id is None and self.analyzed_face_id is None:
            return
        self.play_button.setChecked(False)
        autozoom = self.viewport.autozoom
        self.viewport.autozoom = False
        try:
            if self.analyzed_face_id is not None:
                self._exit_face_analysis()
            self.current_id = None
            self.details.clear()
            self._update_face_analysis_actions(None)
            self._show_snapshot(self.current_snapshot, None, update_surface_controls=False)
        finally:
            self.viewport.autozoom = autozoom
        was_blocked = self.tree.blockSignals(True)
        try:
            self.tree.clearSelection()
            self.tree.setCurrentItem(None)
        finally:
            self.tree.blockSignals(was_blocked)
        self.status.setText("No entity selected")

    def _focus_selected_face(self):
        if self.current_id is not None:
            self.viewport.focus_face(self.current_id)

    def _projection_mode_changed(self, mode: str):
        self.viewport.set_projection_mode(mode)
        self.settings.setValue("projection_mode", mode)
        self.settings.sync()

    def _tree_face_analysis_requested(self, entity_id: int):
        if self.document is None or self.mode.currentText() != "Playback":
            return
        entity = self.document.entity(entity_id)
        if entity is None or entity.type_name.upper() != "ADVANCED_FACE":
            return
        self.play_button.setChecked(False)
        if self.current_id != entity_id or self.discovered_order != entity.order:
            self._set_playback_order(entity.order)
        if self.analyzed_face_id == entity_id:
            self._exit_face_analysis()
            return
        self._analyze_current_face()
        if self.details_tabs.isHidden():
            self._toggle_interpretation_panel()

    def _show_orientation_warning(self, entity_id: int, message: str):
        self.play_button.setChecked(False)
        QMessageBox.information(self, f"Face #{entity_id} — orientation", message)

    def select_entity(self, entity_id: int):
        if not self.document or entity_id not in self.document.by_id:
            return
        self.play_button.setChecked(False)
        self.current_id = entity_id
        entity = self.document.entity(entity_id)
        self.discovered_order = max(self.discovered_order, entity.order)
        self.details.show_entity(entity, self.document)
        self._update_face_analysis_actions(entity)
        self.playback_order = self.discovered_order
        self.playback_progress.blockSignals(True)
        self.playback_progress.setValue(self.playback_order)
        self.playback_progress.blockSignals(False)
        snapshot = (self.geometry_builder or GeometryBuilder(self.document)).build(self.discovered_order)
        self._show_snapshot(snapshot, entity_id)
        self.status.setText(f"#{entity_id} {entity.type_name} · {entity.order + 1}/{len(self.document.entities)}")

