from __future__ import annotations

from step_explorer.performance import timed

from .colors import category_for_entity, color_for_depth
from .step_lines import step_line_appearance

try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtGui import QBrush, QColor
    from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem
except ImportError:  # pragma: no cover - exercised only without GUI extras
    QTreeWidget = object  # type: ignore[misc,assignment]


class EntityTree(QTreeWidget):
    """Entity tree supporting file, semantic, and playback listings."""

    selected_entity = Signal(int)
    face_analysis_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Entity", "Type"])
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.document = None
        self.mode = "File order"
        self.playback_step_ms: dict[int, float] = {}
        self.step_line_palette = "Classic"
        self.step_line_emojis = True
        self.step_line_color_source = False

    def set_step_line_appearance(self, palette: str, show_emojis: bool, color_source: bool) -> None:
        self.step_line_palette = palette
        self.step_line_emojis = show_emojis
        self.step_line_color_source = color_source
        if self.columnCount() == 4:
            self.setColumnHidden(1, not show_emojis)
        if self.document is not None:
            for entity_id, item in self.playback_items.items():
                entity = self.document.entity(entity_id)
                if entity is not None:
                    self._style_playback_item(item, entity)

    def _style_playback_item(self, item, entity) -> None:
        type_name = entity.type_name
        category = category_for_entity(entity)
        emoji, color = step_line_appearance(type_name, self.step_line_palette, category)
        item.setText(1, emoji)
        item.setToolTip(
            1, f"{type_name} — double-click to toggle face analysis"
            if type_name.upper() == "ADVANCED_FACE" else type_name
        )
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
        if type_name.upper() == "ADVANCED_FACE":
            font = item.font(1)
            font.setPointSize(16)
            font.setBold(True)
            item.setFont(1, font)
        item.setForeground(1, QColor(color))
        subdued = category == "Metadata"
        item.setForeground(2, QColor(color) if self.step_line_color_source or subdued else QBrush())

    def set_document(self, document, mode: str = "File order", geometry_only: bool = False) -> None:
        if document is not self.document:
            self.playback_step_ms.clear()
        self.document = document
        self.mode = mode
        self.clear()
        self.setColumnCount(4 if mode == "Playback" else 2)
        self.setHeaderLabels(["Line", "Type", "STEP source", "Step (ms)"] if mode == "Playback" else ["Entity", "Type"])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(2 if mode == "Playback" else 1, QHeaderView.ResizeMode.Stretch)
        self.header().setStretchLastSection(mode != "Playback")
        if mode == "Playback":
            self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.header().resizeSection(1, 42)
            self.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            self.headerItem().setToolTip(1, "Entity type; hover over an icon for the exact STEP type")
            self.headerItem().setToolTip(
                3, "During playback: elapsed time since the previous step completed, including the "
                "playback delay and intervening UI work. Manual steps show rebuild and view-update time. "
                "A trailing ⚠ marks steps that took 75 ms or more."
            )
        self.setColumnHidden(1, mode == "Playback" and not self.step_line_emojis)
        self.semantic_coloring = mode == "Semantic"
        self.geometry_only = geometry_only
        self.visible_entity_ids: list[int] = []
        self.items_by_id: dict[int, QTreeWidgetItem] = {}
        self.playback_items: dict[int, QTreeWidgetItem] = {}
        if mode == "Semantic":
            roots = [entity for entity in document.entities if not document.incoming.get(entity.entity_id)]
            if not roots:
                roots = document.entities[:1]
            for entity in roots:
                self._add_semantic(self.invisibleRootItem(), entity.entity_id, set(), 0)
        else:
            for entity in document.entities:
                if mode != "Playback" and not self._is_visible(entity):
                    continue
                if mode == "Playback":
                    source = entity.raw.replace("\r", " ").replace("\n", " ").strip()
                    item = QTreeWidgetItem([str(entity.span.line), "", source, ""])
                    item.setData(0, Qt.ItemDataRole.UserRole, entity.entity_id)
                    item.setToolTip(2, entity.raw)
                    self._style_playback_item(item, entity)
                    self.playback_items[entity.entity_id] = item
                    if entity.entity_id in self.playback_step_ms:
                        self.set_playback_step_time(entity.entity_id, self.playback_step_ms[entity.entity_id])
                else:
                    item = self._item_for(entity)
                self.addTopLevelItem(item)
                self.items_by_id.setdefault(entity.entity_id, item)
                self.visible_entity_ids.append(entity.entity_id)
        self.expandToDepth(1)

    def set_playback_step_time(self, entity_id: int, elapsed_ms: float) -> None:
        self.playback_step_ms[entity_id] = elapsed_ms
        item = self.playback_items.get(entity_id)
        if item is not None:
            suffix = " ⚠" if elapsed_ms >= 75.0 else ""
            item.setText(3, f"{elapsed_ms:.2f}{suffix}")
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    @timed("ui.tree_focus")
    def focus_playback_entity(self, entity_id: int) -> None:
        item = self.playback_items.get(entity_id)
        if item is not None:
            self.setCurrentItem(item)
            self.scrollToItem(item)

    def focus_entity(self, entity_id: int) -> None:
        item = self.items_by_id.get(entity_id)
        if item is not None:
            self.setCurrentItem(item)
            self.scrollToItem(item)

    def _add_semantic(self, parent, entity_id: int, path: set[int], depth: int) -> None:
        entity = self.document.entity(entity_id)
        if entity is None or entity_id in path:
            return
        next_path = path | {entity_id}
        if not self._is_visible(entity):
            # Keep semantic traversal useful when an organizational entity is
            # between the root and the actual shape topology.
            for child_id in self.document.outgoing.get(entity_id, ()):
                self._add_semantic(parent, child_id, next_path, depth)
            return
        item = self._item_for(entity, depth)
        parent.addChild(item)
        self.items_by_id.setdefault(entity_id, item)
        self.visible_entity_ids.append(entity_id)
        for child_id in self.document.outgoing.get(entity_id, ()):
            self._add_semantic(item, child_id, next_path, depth + 1)

    def _is_visible(self, entity) -> bool:
        return not self.geometry_only or category_for_entity(entity) in {"Geometry", "Topology"}

    def _item_for(self, entity, depth: int = 0):
        item = QTreeWidgetItem([f"#{entity.entity_id}", entity.type_name])
        item.setData(0, Qt.ItemDataRole.UserRole, entity.entity_id)
        if self.semantic_coloring:
            category = category_for_entity(entity)
            red, green, blue = color_for_depth(category, depth)
            color = QColor.fromRgbF(red, green, blue)
            item.setForeground(0, color)
            item.setForeground(1, color)
        return item

    def _clicked(self, item, _column) -> None:
        entity_id = item.data(0, Qt.ItemDataRole.UserRole)
        if entity_id is not None:
            self.selected_entity.emit(int(entity_id))

    def _double_clicked(self, item, _column) -> None:
        if self.mode != "Playback" or self.document is None:
            return
        entity_id = item.data(0, Qt.ItemDataRole.UserRole)
        entity = self.document.entity(entity_id) if entity_id is not None else None
        if entity is not None and entity.type_name.upper() == "ADVANCED_FACE":
            self.face_analysis_requested.emit(entity.entity_id)
