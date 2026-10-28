"""Live application preferences, grouped in a sidebar and form pages."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..visualization.vtk_view import VtkView
from .colors import category_for_type
from .step_lines import STEP_LINE_PALETTES, step_line_appearance


class PreferencesDialog(QDialog):
    """Edit settings through the same actions and widgets as the main window."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Preferences")
        self.setMinimumSize(760, 500)
        self.resize(920, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        columns = QHBoxLayout()
        columns.setSpacing(8)
        root.addLayout(columns, 1)

        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("preferencesSearch")
        self.search.setPlaceholderText("⌕  Search preferences")
        self.search.setClearButtonEnabled(True)
        sidebar_layout.addWidget(self.search)
        self.categories = QListWidget()
        self.categories.setFixedWidth(180)
        self.categories.setObjectName("preferencesCategories")
        self.pages = QStackedWidget()
        self.search_results = QListWidget()
        self.search_results.setObjectName("preferencesSearchResults")
        self.search_results.setWordWrap(True)
        self.search_results.hide()
        self.search_result_heading = QLabel("SEARCH RESULTS")
        self.search_result_heading.setObjectName("preferencesSearchHeading")
        self.search_result_heading.hide()
        sidebar_layout.addWidget(self.categories, 1)
        sidebar_layout.addWidget(self.search_result_heading)
        sidebar_layout.addWidget(self.search_results, 1)
        columns.addWidget(sidebar)
        columns.addWidget(self.pages, 1)

        self._preference_forms = []
        self.reset_buttons = {}
        self._build_navigation()
        self._build_viewport()
        self._build_labels()
        self._build_playback()
        self._build_step_lines()
        self._build_search_index()
        self.search.textChanged.connect(self._update_search_results)
        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        selected = max(0, min(
            self.categories.count() - 1,
            window.settings.value("preferences_category", 0, type=int),
        ))
        self.categories.setCurrentRow(selected)
        self.categories.currentRowChanged.connect(
            lambda row: window.settings.setValue("preferences_category", row)
        )

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(QLabel("Changes are saved automatically."))
        close_row.addWidget(close_button)
        root.addLayout(close_row)
        self.setStyleSheet(
            "QDialog { background: #292b2e; color: #e8e8e8; }"
            "QLineEdit#preferencesSearch { background: #1f2022; color: #eeeeee; border: 1px solid #45474a; "
            "border-radius: 5px; padding: 8px 9px; selection-background-color: #4779b8; }"
            "QLineEdit#preferencesSearch:focus { border: 1px solid #5b8fd1; }"
            "QListWidget#preferencesCategories, QListWidget#preferencesSearchResults { "
            "background: transparent; color: #dddddd; border: none; outline: none; font-size: 13px; }"
            "QListWidget#preferencesCategories::item { padding: 8px 9px; margin: 1px 0; border-radius: 4px; }"
            "QListWidget#preferencesCategories::item:hover { background: #383a3e; }"
            "QListWidget#preferencesCategories::item:selected { background: #4779b8; color: white; }"
            "QListWidget#preferencesSearchResults::item { padding: 2px; border-bottom: 1px solid #3e4043; }"
            "QLabel#preferencesSearchHeading { color: #999da3; font-size: 10px; font-weight: bold; "
            "letter-spacing: 1px; padding: 5px 3px 1px; }"
            "QFrame#preferencesGroup { background: #35373a; border: 1px solid #444649; border-radius: 5px; }"
            "QLabel#preferencesPageTitle { color: #f1f1f1; font-size: 20px; font-weight: 600; }"
            "QLabel#preferencesPageDescription { color: #b9bbc0; }"
            "QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #292b2e; color: #eeeeee; "
            "border: 1px solid #505256; border-radius: 3px; padding: 5px 7px; min-height: 20px; }"
            "QComboBox QAbstractItemView { background: #303236; color: #eeeeee; selection-background-color: #4779b8; }"
            "QCheckBox { spacing: 7px; }"
            "QPushButton { background: #45474b; color: #eeeeee; border: 1px solid #55575b; "
            "border-radius: 4px; padding: 6px 12px; }"
            "QPushButton:hover { background: #51545a; }"
            "QPushButton:pressed { background: #3c3e42; }"
        )

    def _page(self, title: str, description: str) -> QFormLayout:
        self.categories.addItem(title)
        page = QWidget()
        page.setObjectName("preferencesPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(10)
        group = QFrame()
        group.setObjectName("preferencesGroup")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(16, 14, 16, 16)
        group_layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("preferencesPageTitle")
        group_layout.addWidget(heading)
        detail = QLabel(description)
        detail.setObjectName("preferencesPageDescription")
        detail.setWordWrap(True)
        group_layout.addWidget(detail)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #4a4c50;")
        group_layout.addWidget(separator)
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        group_layout.addLayout(form)
        reset_button = QPushButton("Reset to defaults")
        reset_button.setToolTip(f"Restore the default {title.lower()} settings")
        reset_button.clicked.connect(lambda _checked=False, page=title: self._reset_page(page))
        group_layout.addWidget(reset_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.reset_buttons[title] = reset_button
        page_layout.addWidget(group)
        page_layout.addStretch(1)
        self._preference_forms.append((title, description, form))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return form

    def _build_search_index(self):
        self._search_entries = []
        for category, description, form in self._preference_forms:
            for row in range(form.rowCount()):
                field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                field = field_item.widget() if field_item else None
                label = label_item.widget() if label_item else None
                if field is None:
                    continue
                if isinstance(field, QCheckBox):
                    setting = field.text()
                elif isinstance(label, QLabel):
                    setting = label.text()
                else:
                    setting = field.objectName() or field.__class__.__name__
                terms = [category, description, setting, field.toolTip()]
                if isinstance(field, QComboBox):
                    terms.extend(field.itemText(index) for index in range(field.count()))
                self._search_entries.append((category, setting, " ".join(terms).casefold()))

    def _update_search_results(self, query: str):
        query_terms = query.casefold().split()
        searching = bool(query_terms)
        self.categories.setVisible(not searching)
        self.search_result_heading.setVisible(searching)
        self.search_results.setVisible(searching)
        self.search_results.clear()
        if not searching:
            return

        matches = [entry for entry in self._search_entries
                   if all(term in entry[2] for term in query_terms)]
        for category, setting, _ in matches:
            item = QListWidgetItem()
            result = QWidget()
            result_layout = QVBoxLayout(result)
            result_layout.setContentsMargins(7, 6, 7, 6)
            result_layout.setSpacing(2)
            name = QLabel(setting)
            name.setStyleSheet("color: #eeeeee; font-size: 12px; font-weight: 600;")
            location = QLabel(f"{category}  ›")
            location.setStyleSheet("color: #a4a7ad; font-size: 10px;")
            result_layout.addWidget(name)
            result_layout.addWidget(location)
            item.setSizeHint(result.sizeHint())
            item.setToolTip(f"{setting}\nCategory: {category}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.search_results.addItem(item)
            self.search_results.setItemWidget(item, result)
        if not matches:
            item = QListWidgetItem("No matching settings")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.search_results.addItem(item)

    @staticmethod
    def _action_check(form: QFormLayout, label: str, action) -> QCheckBox:
        check = QCheckBox(label)
        check.setChecked(action.isChecked())
        check.toggled.connect(action.setChecked)
        action.toggled.connect(check.setChecked)
        form.addRow(check)
        return check

    @staticmethod
    def _widget_check(form: QFormLayout, label: str, source: QCheckBox) -> QCheckBox:
        check = QCheckBox(label)
        check.setChecked(source.isChecked())
        check.toggled.connect(source.setChecked)
        source.toggled.connect(check.setChecked)
        form.addRow(check)
        return check

    @staticmethod
    def _spin(form: QFormLayout, label: str, source: QSpinBox) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(source.minimum(), source.maximum())
        spin.setSuffix(source.suffix())
        spin.setValue(source.value())
        spin.valueChanged.connect(source.setValue)
        source.valueChanged.connect(spin.setValue)
        form.addRow(label, spin)
        return spin

    @staticmethod
    def _color_button(form: QFormLayout, label: str, window, setting: str, parent) -> QPushButton:
        button = QPushButton()

        def update():
            PreferencesDialog._refresh_color_button(button, getattr(window, setting))

        def choose():
            selected = QColorDialog.getColor(QColor(getattr(window, setting)), parent, f"Choose {label.lower()}")
            if selected.isValid():
                getattr(window, f"_set_{setting}")(selected.name())
                update()

        button.clicked.connect(choose)
        update()
        form.addRow(label, button)
        return button

    @staticmethod
    def _refresh_color_button(button: QPushButton, value: str) -> None:
        color = QColor(value)
        text_color = "#ffffff" if color.lightness() < 128 else "#111111"
        button.setText(color.name())
        button.setStyleSheet(
            f"background-color: {color.name()}; color: {text_color}; padding: 5px 12px;"
        )

    def _reset_page(self, page: str) -> None:
        window = self.window
        config = window.config
        if page == "Navigation":
            self.navigation_preset.setCurrentText("Blender")
            self.orbit_method.setCurrentText("Turntable")
            horizontal, vertical = window.NAVIGATION_PRESET_DEFAULTS["Blender"]
            self.horizontal_check.setChecked(horizontal)
            self.vertical_check.setChecked(vertical)
        elif page == "Viewport":
            self.axes_check.setChecked(config.show_xyz_origin)
            self.grid_check.setChecked(config.show_xy_grid)
            self.grid_scale.setValue(config.xy_grid_scale)
            window._set_xy_grid_color(config.xy_grid_color)
            self._refresh_color_button(self.grid_color, window.xy_grid_color)
            window._set_background_color(config.background_color)
            self._refresh_color_button(self.background_color, window.background_color)
            self.mesh_edges_check.setChecked(False)
            self.edge_opacity.setValue(60)
            self.normal_display.setCurrentText("Hidden")
            self.arrow_limit.setValue(VtkView.DEFAULT_NORMAL_ARROW_LIMIT)
            self.face_colors.setCurrentText("normals")
        elif page == "Labels":
            self.labels_check.setChecked(config.show_entity_labels)
            self.label_content.setCurrentIndex(0 if config.label_mode == "index" else 1)
            label_action = (
                window.line_index_labels_action if config.label_mode == "index"
                else window.type_labels_action
            )
            if not label_action.isChecked():
                label_action.trigger()
            self.label_limit.setValue(config.max_entity_labels)
        elif page == "Playback":
            self.autozoom_check.setChecked(config.autozoom)
            self.playback_speed.setValue(100)
            self.fast_preview_check.setChecked(False)
            self.heal_check.setChecked(False)
        elif page == "STEP Lines":
            self.step_lines_palette.setCurrentText("Classic")
            self.step_lines_emojis.setChecked(True)
            self.step_lines_color_source.setChecked(False)

    def _build_navigation(self):
        form = self._page("Navigation", "Choose a mouse layout, rotation direction, and orbit method.")
        self.navigation_preset = QComboBox()
        self.navigation_preset.addItems(("Blender", "CAD", "Plasticity", "FreeCAD"))
        self.navigation_preset.setToolTip(
            "Selects a mouse layout with its own rotation directions and orbit method."
        )
        self.navigation_preset.setCurrentText(self.window.navigation_preset)
        self.navigation_preset.currentTextChanged.connect(self._navigation_preset_selected)
        form.addRow("Navigation preset", self.navigation_preset)
        self.orbit_method = QComboBox()
        self.orbit_method.addItems(("Turntable", "Trackball"))
        self.orbit_method.setCurrentText(self.window.orbit_method)
        self.orbit_method.currentTextChanged.connect(self.window._orbit_method_changed)
        form.addRow("Orbit method", self.orbit_method)
        self.horizontal_check = self._action_check(
            form, "Invert horizontal rotation", self.window.invert_mouse_horizontal_action
        )
        self.vertical_check = self._action_check(
            form, "Invert vertical rotation", self.window.invert_mouse_vertical_action
        )

    def _navigation_preset_selected(self, preset: str):
        self.window._navigation_preset_changed(preset)
        self.orbit_method.blockSignals(True)
        self.orbit_method.setCurrentText(self.window.orbit_method)
        self.orbit_method.blockSignals(False)

    def _build_viewport(self):
        window = self.window
        form = self._page("Viewport", "Control the grid, model overlays, and preview appearance.")
        self.axes_check = self._action_check(form, "Show XYZ origin axes", window.show_xyz_origin_action)
        self.grid_check = self._action_check(form, "Show XY grid", window.show_xy_grid_action)
        self.grid_scale = QDoubleSpinBox()
        self.grid_scale.setRange(0.25, 10.0)
        self.grid_scale.setSingleStep(0.25)
        self.grid_scale.setDecimals(2)
        self.grid_scale.setSuffix("×")
        self.grid_scale.setValue(window.xy_grid_scale)
        self.grid_scale.setToolTip("Expand or shrink the XY grid around the world origin")
        self.grid_scale.valueChanged.connect(window._xy_grid_scale_changed)
        self.grid_scale.valueChanged.connect(self._update_grid_extent)
        form.addRow("Grid scale", self.grid_scale)
        self.grid_extent = QLabel()
        self.grid_extent.setToolTip("Approximate XY grid width and depth in STEP model units")
        self._update_grid_extent()
        form.addRow("Grid extent", self.grid_extent)
        self.grid_color = self._color_button(form, "Grid color", window, "xy_grid_color", self)
        self.background_color = self._color_button(form, "Background color", window, "background_color", self)
        self.mesh_edges_check = self._action_check(form, "Tessellation edges", window.show_mesh_edges_action)
        self.edge_opacity = QSpinBox()
        self.edge_opacity.setRange(0, 100)
        self.edge_opacity.setSuffix("%")
        self.edge_opacity.setValue(window.mesh_edge_opacity_slider.value())
        self.edge_opacity.setEnabled(window.show_mesh_edges_action.isChecked())
        self.edge_opacity.valueChanged.connect(window.mesh_edge_opacity_slider.setValue)
        window.mesh_edge_opacity_slider.valueChanged.connect(self.edge_opacity.setValue)
        window.show_mesh_edges_action.toggled.connect(self.edge_opacity.setEnabled)
        form.addRow("Edge opacity", self.edge_opacity)

        self.normal_display = QComboBox()
        self.normal_display.addItems(VtkView.NORMAL_DISPLAY_MODES)
        self.normal_display.setCurrentText(window.normal_display.currentText())
        self.normal_display.currentTextChanged.connect(window.normal_display.setCurrentText)
        window.normal_display.currentTextChanged.connect(self.normal_display.setCurrentText)
        form.addRow("Normals", self.normal_display)
        self.arrow_limit = QSpinBox()
        self.arrow_limit.setRange(0, VtkView.MAX_NORMAL_ARROW_LIMIT)
        self.arrow_limit.setValue(window.normal_arrow_slider.value())
        self.arrow_limit.valueChanged.connect(window.normal_arrow_slider.setValue)
        window.normal_arrow_slider.valueChanged.connect(self.arrow_limit.setValue)
        form.addRow("Normal arrow limit", self.arrow_limit)
        self.face_colors = QComboBox()
        self.face_colors.addItems(VtkView.FACE_COLOR_MODES)
        self.face_colors.setCurrentText(window.face_color_mode.currentText())
        self.face_colors.currentTextChanged.connect(window.face_color_mode.setCurrentText)
        window.face_color_mode.currentTextChanged.connect(self.face_colors.setCurrentText)
        form.addRow("Face colors", self.face_colors)

    def _update_grid_extent(self, *_args):
        width, depth = self.window.viewport.xy_grid_world_size
        self.grid_extent.setText(f"{width:.3g} × {depth:.3g} units")

    def _build_labels(self):
        window = self.window
        form = self._page("Labels", "Choose which entity labels appear over the 3D preview.")
        self.labels_check = self._action_check(form, "Show entity labels", window.show_entity_labels_action)
        self.label_content = QComboBox()
        self.label_content.addItem("Entity ID (#123)", "index")
        self.label_content.addItem("Entity type", "type")
        self.label_content.setCurrentIndex(0 if window.label_mode == "index" else 1)

        def change_label_content(index):
            action = window.line_index_labels_action if index == 0 else window.type_labels_action
            if not action.isChecked():
                action.trigger()

        self.label_content.currentIndexChanged.connect(change_label_content)
        form.addRow("Label content", self.label_content)
        self.label_limit = QSpinBox()
        self.label_limit.setRange(1, 2000)
        self.label_limit.setValue(window.max_entity_labels)
        self.label_limit.valueChanged.connect(window._max_entity_labels_changed)
        form.addRow("Maximum visible labels", self.label_limit)

    def _build_playback(self):
        window = self.window
        form = self._page("Playback", "Control how the STEP file builds up and frames during playback.")
        self.autozoom_check = self._action_check(form, "Autozoom during playback", window.autozoom_action)
        self.playback_speed = QSlider(Qt.Orientation.Horizontal)
        self.playback_speed.setRange(0, 100)
        self.playback_speed.setValue(window.playback_speed_slider.value())
        self.playback_speed.valueChanged.connect(window.playback_speed_slider.setValue)
        window.playback_speed_slider.valueChanged.connect(self.playback_speed.setValue)
        speed_row = QWidget()
        speed_layout = QHBoxLayout(speed_row)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.addWidget(self.playback_speed)
        self.playback_speed_value = QLabel(f"{self.playback_speed.value()}%")
        self.playback_speed_value.setMinimumWidth(38)
        self.playback_speed.valueChanged.connect(
            lambda value: self.playback_speed_value.setText(f"{value}%")
        )
        speed_layout.addWidget(self.playback_speed_value)
        form.addRow("Maximum playback speed", speed_row)
        self.fast_preview_check = self._widget_check(form, "Turbo", window.fast_preview_checkbox)
        self.heal_check = self._widget_check(form, "Heal face orientations", window.heal_checkbox)

    def _build_step_lines(self):
        window = self.window
        form = self._page("STEP Lines", "Style the STEP entity list shown during playback.")
        self.step_lines_palette = QComboBox()
        self.step_lines_palette.addItems(STEP_LINE_PALETTES)
        self.step_lines_palette.setCurrentText(window.step_lines_palette)
        form.addRow("Color palette", self.step_lines_palette)
        self.step_lines_emojis = QCheckBox("Show type markers")
        self.step_lines_emojis.setChecked(window.step_lines_show_emojis)
        form.addRow(self.step_lines_emojis)
        self.step_lines_color_source = QCheckBox("Color STEP source by type")
        self.step_lines_color_source.setChecked(window.step_lines_color_source)
        form.addRow(self.step_lines_color_source)

        self.step_lines_preview = QTreeWidget()
        self.step_lines_preview.setObjectName("stepLinesPreview")
        self.step_lines_preview.setHeaderLabels(["Line", "Type", "STEP source", "Step (ms)"])
        self.step_lines_preview.setRootIsDecorated(False)
        self.step_lines_preview.setAlternatingRowColors(True)
        self.step_lines_preview.setFixedHeight(145)
        self.step_lines_preview.setStyleSheet(
            "QTreeWidget#stepLinesPreview { background: #20242a; alternate-background-color: #292d32; "
            "color: #e8e8e8; border: 1px solid #505256; }"
            "QTreeWidget#stepLinesPreview QHeaderView::section { background: #35373a; color: #eeeeee; "
            "border: 1px solid #505256; padding: 2px 4px; }"
        )
        self.step_lines_preview.setColumnWidth(0, 48)
        self.step_lines_preview.setColumnWidth(1, 42)
        self.step_lines_preview.setColumnWidth(2, 353)
        self.step_lines_preview.setColumnWidth(3, 78)
        for line, source, type_name in (
            ("121", "#115=VECTOR('',#112,1.);", "VECTOR"),
            ("122", "#116=ADVANCED_FACE('',...);", "ADVANCED_FACE"),
            ("123", "#117=PRODUCT('part',...);", "PRODUCT"),
            ("124", "#118=APPROVAL_STATUS('ok');", "APPROVAL_STATUS"),
        ):
            item = QTreeWidgetItem([line, "", source, "32.64"])
            item.setData(1, Qt.ItemDataRole.UserRole, type_name)
            self.step_lines_preview.addTopLevelItem(item)
        form.addRow("Preview", self.step_lines_preview)

        self.step_lines_palette.currentTextChanged.connect(self._step_lines_changed)
        self.step_lines_emojis.toggled.connect(self._step_lines_changed)
        self.step_lines_color_source.toggled.connect(self._step_lines_changed)
        self._update_step_lines_preview()

    def _step_lines_changed(self, *_args):
        self.window._set_step_line_appearance(
            self.step_lines_palette.currentText(),
            self.step_lines_emojis.isChecked(),
            self.step_lines_color_source.isChecked(),
        )
        self._update_step_lines_preview()

    def _update_step_lines_preview(self):
        self.step_lines_preview.setColumnHidden(1, not self.step_lines_emojis.isChecked())
        for index in range(self.step_lines_preview.topLevelItemCount()):
            item = self.step_lines_preview.topLevelItem(index)
            type_name = item.data(1, Qt.ItemDataRole.UserRole)
            emoji, color = step_line_appearance(type_name, self.step_lines_palette.currentText())
            item.setText(1, emoji)
            item.setToolTip(
                1, f"{type_name} — double-click to toggle face analysis"
                if type_name == "ADVANCED_FACE" else type_name
            )
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            if type_name == "ADVANCED_FACE":
                font = item.font(1)
                font.setPointSize(16)
                font.setBold(True)
                item.setFont(1, font)
            item.setForeground(1, QColor(color))
            subdued = category_for_type(type_name) == "Metadata"
            item.setForeground(
                2, QColor(color) if self.step_lines_color_source.isChecked() or subdued else QBrush()
            )
