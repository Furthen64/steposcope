import pytest

from step_explorer.step.export import geometry_only_step, save_geometry_only_step
from step_explorer.step.parser import StepDocument, parse_step


def test_geometry_export_keeps_shape_dependencies_and_removes_metadata(tmp_path):
    source = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('original'),'2;1');
FILE_NAME('original.step','2026-01-01',('author'),('company'),'system','','');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
#1=ADVANCED_BREP_SHAPE_REPRESENTATION('',(#2),#3);
#2=MANIFOLD_SOLID_BREP('',#4);
#3=(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNIT_ASSIGNED_CONTEXT((#5)) REPRESENTATION_CONTEXT('',''));
#4=CLOSED_SHELL('',(#6));
#5=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));
#6=ADVANCED_FACE('',(),#7,.T.);
#7=PLANE('',#8);
#8=AXIS2_PLACEMENT_3D('',#9,$,$);
#9=CARTESIAN_POINT('',(0.,0.,0.));
#10=APPROVAL(#11,'approved');
#11=APPROVAL_STATUS('approved');
#12=SHAPE_DEFINITION_REPRESENTATION(#13,#1);
#13=PRODUCT_DEFINITION_SHAPE('','',#14);
#14=PRODUCT_DEFINITION('part','',#15,#16);
#15=PRODUCT_DEFINITION_FORMATION('','',#17);
#16=DESIGN_CONTEXT('',#18,'design');
#17=PRODUCT('part','part','',(#19));
#18=APPLICATION_CONTEXT('mechanical design');
#19=PRODUCT_CONTEXT('',#18,'mechanical');
ENDSEC;
END-ISO-10303-21;
"""
    document = parse_step(source)
    destination = tmp_path / "part's geometry.step"
    assert save_geometry_only_step(document, destination) == 17
    exported = parse_step(destination.read_text(encoding="utf-8"))
    assert {entity.entity_id for entity in exported.entities} == set(range(1, 10)) | set(range(12, 20))
    assert exported.entity(1).raw == document.entity(1).raw
    assert exported.entity(3).type_name == "COMPLEX"
    assert all(ref in exported.by_id for entity in exported.entities for ref in entity.references)
    assert "company" not in exported.source
    assert "part''s geometry.step" in exported.source
    assert exported.header[-1].name == "FILE_SCHEMA"


def test_export_real_step_model_preserves_brep_and_drops_approvals(small_corpus):
    document = None
    for path in small_corpus:
        candidate = StepDocument.from_file(path)
        types = {entity.type_name.upper() for entity in candidate.entities}
        if "MANIFOLD_SOLID_BREP" in types and any("APPROVAL" in name for name in types):
            document = candidate
            break
    if document is None:
        pytest.skip("no corpus model combines B-rep geometry with approvals")
    exported = parse_step(geometry_only_step(document, "geometry_only.step"))
    assert len(exported.entities) < len(document.entities)
    assert any(entity.type_name == "MANIFOLD_SOLID_BREP" for entity in exported.entities)
    assert any(entity.type_name == "ADVANCED_BREP_SHAPE_REPRESENTATION" for entity in exported.entities)
    assert any(entity.type_name == "SHAPE_DEFINITION_REPRESENTATION" for entity in exported.entities)
    assert all("APPROVAL" not in entity.type_name for entity in exported.entities)
    assert all(ref in exported.by_id for entity in exported.entities for ref in entity.references)


def test_export_requires_schema_and_geometry():
    without_schema = parse_step("ISO-10303-21;DATA;#1=CARTESIAN_POINT('',(0.,0.,0.));ENDSEC;END-ISO-10303-21;")
    with pytest.raises(ValueError, match="FILE_SCHEMA"):
        geometry_only_step(without_schema, "out.step")
    without_geometry = parse_step("ISO-10303-21;HEADER;FILE_SCHEMA(('X'));ENDSEC;DATA;#1=APPROVAL_STATUS('ok');ENDSEC;END-ISO-10303-21;")
    with pytest.raises(ValueError, match="no recognized geometry"):
        geometry_only_step(without_geometry, "out.step")
