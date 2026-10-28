from __future__ import annotations

from ..step.values import value_to_text

try:
    from PySide6.QtWidgets import QPlainTextEdit
except ImportError:  # pragma: no cover
    QPlainTextEdit = object  # type: ignore[misc,assignment]


class EntityDetails(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def show_entity(self, entity, document) -> None:
        if entity is None:
            self.clear()
            return
        outgoing = ", ".join(f"#{item}" for item in document.outgoing.get(entity.entity_id, ())) or "none"
        incoming = ", ".join(f"#{item}" for item in document.incoming.get(entity.entity_id, ())) or "none"
        arguments = "\n".join(f"  [{index}] {value_to_text(value)}" for index, value in enumerate(entity.arguments)) or "  (none)"
        self.setPlainText(
            f"Entity #{entity.entity_id}\n"
            f"Type: {entity.type_name}\n"
            f"Source position: line {entity.span.line}, column {entity.span.column}; order {entity.order}\n\n"
            f"Arguments:\n{arguments}\n\n"
            f"Outgoing references: {outgoing}\n"
            f"Incoming references: {incoming}\n\n"
            f"Raw STEP:\n{entity.raw}"
        )

