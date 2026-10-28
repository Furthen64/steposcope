"""Named STEP bodies and the renderable entities owned by each body."""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..step.parser import StepDocument
from ..step.values import StepString


BODY_TYPES = {
    "MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP",
    "SHELL_BASED_SURFACE_MODEL", "FACE_BASED_SURFACE_MODEL",
}
SHELL_TYPES = {"CLOSED_SHELL", "OPEN_SHELL", "CONNECTED_FACE_SET"}


@dataclass(frozen=True)
class Body:
    entity_id: int
    name: str
    entity_ids: frozenset[int]


def document_bodies(document: StepDocument) -> list[Body]:
    """Find top-level body records and follow their STEP reference graphs."""
    candidates = [entity for entity in document.entities
                  if entity.type_name.upper() in BODY_TYPES | SHELL_TYPES]
    if not candidates:
        return []
    descendants: dict[int, frozenset[int]] = {}
    for entity in candidates:
        reached = set()
        pending = [entity.entity_id]
        while pending:
            current = pending.pop()
            if current in reached or current not in document.by_id:
                continue
            reached.add(current)
            pending.extend(document.outgoing.get(current, ()))
        descendants[entity.entity_id] = frozenset(reached)
    nested = {
        candidate.entity_id
        for candidate in candidates
        if any(candidate.entity_id != other.entity_id
               and candidate.entity_id in descendants[other.entity_id]
               for other in candidates)
    }
    bodies = []
    for entity in candidates:
        if entity.entity_id in nested:
            continue
        raw_name = entity.arguments[0] if entity.arguments else None
        name = raw_name.value.strip() if isinstance(raw_name, StepString) else ""
        if not name:
            name = f"{entity.type_name.replace('_', ' ').title()} #{entity.entity_id}"
        bodies.append(Body(entity.entity_id, name, descendants[entity.entity_id]))
    return bodies


def hidden_entities(bodies: list[Body], hidden_body_ids: set[int]) -> set[int]:
    """Keep shared geometry visible while at least one owning body is shown."""
    hidden = set().union(*(body.entity_ids for body in bodies
                           if body.entity_id in hidden_body_ids))
    visible = set().union(*(body.entity_ids for body in bodies
                            if body.entity_id not in hidden_body_ids))
    return hidden - visible


class ObjectTree(QTreeWidget):
    visibility_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bodies: list[Body] = []
        self._hidden_cache_key: frozenset[int] | None = None
        self._hidden_cache: set[int] = set()
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setToolTip("Uncheck a body to hide it; double-click to show only that body")
        self.itemChanged.connect(lambda _item, _column: self.visibility_changed.emit())
        self.itemDoubleClicked.connect(self._show_only_item)

    def _show_only_item(self, item: QTreeWidgetItem, _column: int) -> None:
        self._set_visible_body_ids({item.data(0, Qt.ItemDataRole.UserRole)})

    def show_all(self) -> None:
        self._set_visible_body_ids({body.entity_id for body in self.bodies})

    def _set_visible_body_ids(self, visible_body_ids: set[int]) -> None:
        changed = False
        self.blockSignals(True)
        try:
            for index in range(self.topLevelItemCount()):
                item = self.topLevelItem(index)
                state = (Qt.CheckState.Checked if item.data(0, Qt.ItemDataRole.UserRole) in visible_body_ids
                         else Qt.CheckState.Unchecked)
                if item.checkState(0) != state:
                    item.setCheckState(0, state)
                    changed = True
        finally:
            self.blockSignals(False)
        if changed:
            self.visibility_changed.emit()

    def set_document(self, document: StepDocument) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            self.bodies = document_bodies(document)
            self._hidden_cache_key = None
            self._hidden_cache.clear()
            for body in self.bodies:
                item = QTreeWidgetItem([f"{body.name}  (#{body.entity_id})"])
                item.setData(0, Qt.ItemDataRole.UserRole, body.entity_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                self.addTopLevelItem(item)
        finally:
            self.blockSignals(False)

    def hidden_entity_ids(self) -> set[int]:
        hidden_body_ids = frozenset(
            self.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            for index in range(self.topLevelItemCount())
            if self.topLevelItem(index).checkState(0) != Qt.CheckState.Checked
        )
        if hidden_body_ids != self._hidden_cache_key:
            self._hidden_cache = hidden_entities(self.bodies, set(hidden_body_ids))
            self._hidden_cache_key = hidden_body_ids
        return self._hidden_cache
