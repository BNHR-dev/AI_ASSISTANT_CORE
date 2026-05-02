from app.engine.output_contracts import get_output_contract


def _build_rules_text():
    contract = get_output_contract("build")
    return " ".join(contract["rules"]).lower()


def test_blender_rule_mentions_import_bpy_first():
    rules_text = _build_rules_text()
    assert "import bpy" in rules_text
    assert "commencer" in rules_text


def test_blender_rule_covers_bpy_apis():
    rules_text = _build_rules_text()
    assert "bpy.ops" in rules_text
    assert "bpy.data" in rules_text
    assert "bpy.context" in rules_text


def test_blender_rule_covers_camera_light_materials():
    rules_text = _build_rules_text()
    assert "caméra" in rules_text or "camera" in rules_text
    assert "lumière" in rules_text or "lumiere" in rules_text or "light" in rules_text
    assert "matériaux" in rules_text or "materiaux" in rules_text or "matériau" in rules_text


def test_blender_rule_forbids_hardcoded_paths():
    rules_text = _build_rules_text()
    assert "chemin" in rules_text
    assert "hardcod" in rules_text


def test_blender_rule_forbids_render_unless_explicit():
    rules_text = _build_rules_text()
    assert "bpy.ops.render.render" in rules_text
    assert "sauf demande explicite" in rules_text


def test_blender_render_allowed_when_explicit():
    rules_text = _build_rules_text()
    assert "demande explicite" in rules_text


def test_blender_rule_quality_idioms():
    rules_text = _build_rules_text()
    assert "metallic" in rules_text
    assert "principled bsdf" in rules_text
    assert "1.0" in rules_text
    assert "keyframe_insert" in rules_text
    assert "frame_start" in rules_text
    assert "frame_end" in rules_text
    assert "data.extrude" in rules_text
    assert "nodes.clear" in rules_text
    assert "bpy.math.pi" in rules_text
    assert "output.png" in rules_text or "relatifs" in rules_text
