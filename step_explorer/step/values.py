"""Values used by the pragmatic STEP parser.

The classes deliberately retain STEP's distinctions between omitted, derived,
enumerated, typed, and referenced values.  This makes the generic parser useful
even for entities that the application does not understand semantically yet.
"""

from dataclasses import dataclass
from typing import Iterator, TypeAlias


@dataclass(frozen=True)
class StepNull:
    """The ``$`` value: an explicitly unset attribute."""


@dataclass(frozen=True)
class StepOmitted:
    """The ``*`` value: an attribute derived by the STEP schema."""


@dataclass(frozen=True)
class StepReference:
    entity_id: int


@dataclass(frozen=True)
class StepString:
    value: str
    raw: str


@dataclass(frozen=True)
class StepNumber:
    value: int | float
    raw: str


@dataclass(frozen=True)
class StepEnumeration:
    value: str


@dataclass(frozen=True)
class StepIdentifier:
    value: str


@dataclass(frozen=True)
class StepAggregate:
    values: tuple["StepValue", ...]


@dataclass(frozen=True)
class StepTypedValue:
    name: str
    arguments: tuple["StepValue", ...]


@dataclass(frozen=True)
class StepComplexValue:
    """A complex entity instance: ``(A(...) B(...) C(...))``."""

    components: tuple[StepTypedValue, ...]


StepValue: TypeAlias = (
    StepNull
    | StepOmitted
    | StepReference
    | StepString
    | StepNumber
    | StepEnumeration
    | StepIdentifier
    | StepAggregate
    | StepTypedValue
    | StepComplexValue
)


def iter_references(value: StepValue) -> Iterator[int]:
    """Yield every entity reference nested inside *value*, in source order."""

    if isinstance(value, StepReference):
        yield value.entity_id
    elif isinstance(value, (StepAggregate, StepTypedValue, StepComplexValue)):
        if isinstance(value, StepAggregate):
            values = value.values
        elif isinstance(value, StepTypedValue):
            values = value.arguments
        else:
            values = value.components
        for child in values:
            yield from iter_references(child)


def value_to_text(value: StepValue) -> str:
    """Return a compact, human-readable representation for the details panel."""

    if isinstance(value, StepNull):
        return "$"
    if isinstance(value, StepOmitted):
        return "*"
    if isinstance(value, StepReference):
        return f"#{value.entity_id}"
    if isinstance(value, StepString):
        return value.raw
    if isinstance(value, StepNumber):
        return value.raw
    if isinstance(value, StepEnumeration):
        return f".{value.value}."
    if isinstance(value, StepIdentifier):
        return value.value
    if isinstance(value, StepAggregate):
        return "(" + ", ".join(value_to_text(item) for item in value.values) + ")"
    if isinstance(value, StepTypedValue):
        return f"{value.name}(" + ", ".join(value_to_text(item) for item in value.arguments) + ")"
    if isinstance(value, StepComplexValue):
        return "(" + " ".join(value_to_text(item) for item in value.components) + ")"
    return repr(value)
