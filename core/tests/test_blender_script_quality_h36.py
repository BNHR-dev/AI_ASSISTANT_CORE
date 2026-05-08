from app.engine.output_contracts import get_output_contract


def test_build_contract_cube_uses_explicit_primitive_params():
    contract = get_output_contract("build")
    rules_text = " ".join(contract["rules"]).lower()

    assert "primitive_cube_add" in rules_text
    assert "location=(0, 0, 0)" in rules_text


def test_build_contract_cube_clears_default_scene_before_add():
    contract = get_output_contract("build")
    rules_text = " ".join(contract["rules"]).lower()

    assert "select_all" in rules_text
    assert "bpy.ops.object.delete" in rules_text


def test_build_contract_cube_requires_pydata_geometry_for_manual_mesh():
    contract = get_output_contract("build")
    rules_text = " ".join(contract["rules"]).lower()

    assert "bpy.data.meshes.new" in rules_text
    assert "from_pydata" in rules_text
    assert "vertices" in rules_text
    assert "faces" in rules_text
    assert "mesh vide" in rules_text
    assert "cube" in rules_text
