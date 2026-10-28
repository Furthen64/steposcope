import pytest

from step_explorer.step.parser import StepParseError, parse_step
from step_explorer.step.values import (
    StepAggregate,
    StepEnumeration,
    StepNull,
    StepNumber,
    StepString,
    StepTypedValue,
)


SAMPLE = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('demo','two lines'), '2;1');
FILE_NAME('sample.stp', '2026-01-01', ( 'A' ), 'org', 'pre', 'system', 'auth');
ENDSEC;
DATA;
#1 = CARTESIAN_POINT('origin', (0.0, 1, -2.5));
#2 = VERTEX_POINT('', #1);
#3 = CUSTOM_ENTITY(
  #2,
  ( .T., $, *, 'it''s fine', 3.0E+2 ),
  SOME_TYPED(#1)
);
ENDSEC;
END-ISO-10303-21;
"""


def test_primitives_and_multiline_values():
    document = parse_step(SAMPLE)
    assert len(document.header) == 2
    assert [entity.entity_id for entity in document.entities] == [1, 2, 3]
    point = document.entity(1)
    assert point is not None
    assert point.type_name == "CARTESIAN_POINT"
    assert isinstance(point.arguments[0], StepString)
    assert isinstance(point.arguments[1], StepAggregate)
    assert point.arguments[1].values[0] == StepNumber(0.0, "0.0")
    assert point.span.line == 7
    assert point.raw.startswith("#1 =") and point.raw.endswith(";")

    custom = document.entity(3)
    assert custom is not None
    assert isinstance(custom.arguments[1], StepAggregate)
    assert isinstance(custom.arguments[1].values[0], StepEnumeration)
    assert isinstance(custom.arguments[1].values[1], StepNull)
    assert custom.arguments[1].values[2].__class__.__name__ == "StepOmitted"
    assert custom.arguments[1].values[3].value == "it's fine"
    assert isinstance(custom.arguments[2], StepTypedValue)


def test_forward_reverse_reference_graph():
    document = parse_step(SAMPLE)
    assert document.outgoing[3] == (2, 1)
    assert document.incoming[1] == (2, 3)
    assert document.incoming[2] == (3,)


def test_unknown_entity_is_not_a_parse_error():
    document = parse_step("ISO-10303-21;DATA;#99=BRAND_NEW_THING($);ENDSEC;END-ISO-10303-21;")
    assert document.entity(99).type_name == "BRAND_NEW_THING"


def test_duplicate_entity_id_is_rejected_with_source_line():
    with pytest.raises(StepParseError, match=r"duplicate entity #1 at line 4"):
        parse_step(
            "ISO-10303-21;\nDATA;\n#1=FIRST();\n#1=SECOND();\nENDSEC;"
        )


def test_complex_entity_instance_uses_adjacent_typed_components():
    document = parse_step(
        "ISO-10303-21;DATA;"
        "#10=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));"
        "ENDSEC;END-ISO-10303-21;"
    )
    entity = document.entity(10)
    assert entity is not None
    assert entity.type_name == "COMPLEX"
    assert entity.references == ()
