"""Pragmatic, generic ISO-10303-21 parser.

It parses syntax and references without requiring a complete EXPRESS schema.
Unknown entity types remain ordinary :class:`StepEntity` instances.
"""

from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

from .lexer import Token, StepLexError, lex
from .values import (
    StepAggregate,
    StepComplexValue,
    StepEnumeration,
    StepIdentifier,
    StepNull,
    StepNumber,
    StepOmitted,
    StepReference,
    StepString,
    StepTypedValue,
    StepValue,
    iter_references,
)


class StepParseError(ValueError):
    """A syntactic error with a useful source location."""


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class StepRecord:
    name: str
    arguments: tuple[StepValue, ...]
    raw: str
    span: SourceSpan


@dataclass(frozen=True)
class StepEntity:
    entity_id: int
    type_name: str
    arguments: tuple[StepValue, ...]
    raw: str
    span: SourceSpan
    order: int

    @property
    def references(self) -> tuple[int, ...]:
        return tuple(ref for argument in self.arguments for ref in iter_references(argument))


@dataclass
class StepDocument:
    source: str
    header: list[StepRecord] = field(default_factory=list)
    entities: list[StepEntity] = field(default_factory=list)
    outgoing: dict[int, tuple[int, ...]] = field(default_factory=dict)
    incoming: dict[int, tuple[int, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_id = {entity.entity_id: entity for entity in self.entities}
        self.outgoing = {entity.entity_id: entity.references for entity in self.entities}
        reverse: dict[int, list[int]] = {entity.entity_id: [] for entity in self.entities}
        for source_id, targets in self.outgoing.items():
            for target_id in targets:
                reverse.setdefault(target_id, []).append(source_id)
        self.incoming = {entity_id: tuple(ids) for entity_id, ids in reverse.items()}

    def entity(self, entity_id: int) -> StepEntity | None:
        return self.by_id.get(entity_id)

    @classmethod
    def from_file(cls, path: str | Path) -> "StepDocument":
        source = Path(path).read_text(encoding="utf-8", errors="replace")
        return parse_step(source)


class _Parser:
    def __init__(self, source: str):
        self.source = source
        try:
            self.tokens = lex(source)
        except StepLexError as exc:
            raise StepParseError(str(exc)) from exc
        self.index = 0
        # Newline offsets, once. span() is called once per record AND once per error
        # site, and it used to recount newlines from offset 0 every time -- O(n) per
        # call, O(n^2) per file. That alone was 80s of a 142s parse.
        self._newlines = [i for i, ch in enumerate(source) if ch == "\n"]

    @property
    def token(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.token
        self.index += 1
        return token

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        token = self.token
        if token.kind == kind and (value is None or token.value.upper() == value.upper()):
            return self.advance()
        return None

    def expect(self, kind: str, value: str | None = None) -> Token:
        token = self.accept(kind, value)
        if token is None:
            wanted = value or kind
            raise self.error(f"expected {wanted}, found {self.token.value or 'end of file'}")
        return token

    def error(self, message: str, token: Token | None = None) -> StepParseError:
        token = token or self.token
        line = self.source.count("\n", 0, token.start) + 1
        line_start = self.source.rfind("\n", 0, token.start) + 1
        column = token.start - line_start + 1
        return StepParseError(f"{message} at line {line}, column {column}")

    def value(self) -> StepValue:
        token = self.token
        if self.accept("NULL"):
            return StepNull()
        if self.accept("OMITTED"):
            return StepOmitted()
        if token.kind == "REFERENCE":
            self.advance()
            return StepReference(int(token.value[1:]))
        if token.kind == "STRING":
            self.advance()
            raw = token.value
            return StepString(raw[1:-1].replace("''", "'"), raw)
        if token.kind == "NUMBER":
            self.advance()
            return StepNumber(float(token.value) if any(c in token.value for c in ".Ee") else int(token.value), token.value)
        if token.kind == "ENUM":
            self.advance()
            return StepEnumeration(token.value[1:-1])
        if token.kind == "LPAREN":
            self.advance()
            values: list[StepValue] = []
            used_implicit_separator = False
            if not self.accept("RPAREN"):
                while True:
                    values.append(self.value())
                    if self.accept("RPAREN"):
                        break
                    if self.accept("COMMA"):
                        continue
                    # Complex entity instances use adjacent typed values,
                    # unlike ordinary aggregates which use commas.
                    if all(isinstance(item, StepTypedValue) for item in values) and self.token.kind == "IDENTIFIER":
                        used_implicit_separator = True
                        continue
                    self.expect("COMMA")
            if used_implicit_separator:
                return StepComplexValue(tuple(values))
            return StepAggregate(tuple(values))
        if token.kind == "IDENTIFIER":
            self.advance()
            name = token.value
            if self.accept("LPAREN"):
                arguments: list[StepValue] = []
                if not self.accept("RPAREN"):
                    while True:
                        arguments.append(self.value())
                        if self.accept("RPAREN"):
                            break
                        self.expect("COMMA")
                return StepTypedValue(name, tuple(arguments))
            return StepIdentifier(name)
        raise self.error(f"unexpected value {token.value or 'end of file'}", token)

    def arguments(self) -> tuple[StepValue, ...]:
        self.expect("LPAREN")
        values: list[StepValue] = []
        if not self.accept("RPAREN"):
            while True:
                values.append(self.value())
                if self.accept("RPAREN"):
                    break
                self.expect("COMMA")
        return tuple(values)

    def parse_record(self, start: int, name: str) -> StepRecord:
        arguments = self.arguments()
        end_token = self.expect("SEMICOLON")
        return StepRecord(name, arguments, self.source[start : end_token.end], self.span(start, end_token.end))

    def span(self, start: int, end: int) -> SourceSpan:
        # bisect over the precomputed newline offsets: same line/column as counting
        # from offset 0, without rescanning the file for every record.
        before = bisect_left(self._newlines, start)
        line_start = self._newlines[before - 1] + 1 if before else 0
        return SourceSpan(start, end, before + 1, start - line_start + 1)

    def parse(self) -> StepDocument:
        header: list[StepRecord] = []
        entities: list[StepEntity] = []
        self.expect("IDENTIFIER", "ISO-10303-21") if self.token.value.upper() == "ISO-10303-21" else None
        # The exchange structure has a few records before HEADER; skip them while
        # retaining the actual HEADER/DATA contents and accepting common variants.
        while self.token.kind != "EOF" and self.token.value.upper() not in {"HEADER", "DATA"}:
            self.advance()
        if self.accept("IDENTIFIER", "HEADER"):
            self.expect("SEMICOLON")
            while not (self.token.kind == "IDENTIFIER" and self.token.value.upper() == "ENDSEC"):
                if self.token.kind == "EOF":
                    raise self.error("unterminated HEADER section")
                start = self.token.start
                name = self.expect("IDENTIFIER").value
                header.append(self.parse_record(start, name))
            self.expect("IDENTIFIER", "ENDSEC")
            self.expect("SEMICOLON")
        while self.token.kind != "EOF" and self.token.value.upper() != "DATA":
            self.advance()
        self.expect("IDENTIFIER", "DATA")
        self.expect("SEMICOLON")
        order = 0
        seen_entity_ids: set[int] = set()
        while not (self.token.kind == "IDENTIFIER" and self.token.value.upper() == "ENDSEC"):
            if self.token.kind == "EOF":
                raise self.error("unterminated DATA section")
            start = self.token.start
            ref = self.expect("REFERENCE")
            entity_id = int(ref.value[1:])
            if entity_id in seen_entity_ids:
                raise self.error(f"duplicate entity #{entity_id}", ref)
            seen_entity_ids.add(entity_id)
            self.expect("EQUALS")
            value = self.value()
            end_token = self.expect("SEMICOLON")
            if isinstance(value, StepTypedValue):
                type_name = value.name
                arguments = value.arguments
            elif isinstance(value, StepComplexValue):
                type_name = "COMPLEX"
                arguments = (value,)
            else:
                type_name = "UNTYPED"
                arguments = (value,)
            entities.append(StepEntity(entity_id, type_name, arguments, self.source[start : end_token.end], self.span(start, end_token.end), order))
            order += 1
        self.expect("IDENTIFIER", "ENDSEC")
        self.expect("SEMICOLON")
        self.accept("IDENTIFIER", "END-ISO-10303-21")
        self.accept("SEMICOLON")
        return StepDocument(self.source, header, entities)


def parse_step(source: str) -> StepDocument:
    """Parse STEP source text into a document, preserving entity raw text."""

    return _Parser(source).parse()
