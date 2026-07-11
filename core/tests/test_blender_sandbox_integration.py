"""C1c — Tests d'intégration du sandbox bwrap (exécution réelle, Fedora/host).

Ces tests lancent réellement bwrap et prouvent le confinement effectif :
home/.env illisibles, secrets d'env absents, réseau injoignable, écriture hors
output refusée, écriture dans output autorisée, rendu EEVEE GPU fonctionnel, et
destruction du groupe de processus au timeout (C1c #6/#9).

Skip automatique si bwrap (ou Blender/GPU/pgrep pour les tests concernés) est
absent — la suite reste verte sur une machine sans ces prérequis.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from app.clients import blender_sandbox as sbx
from app.clients.blender_sandbox import (
    PROFILE_RENDER,
    PROFILE_STRICT,
    SANDBOX_ENV_VAR,
    build_sandbox_plan,
)

BWRAP = shutil.which("bwrap")
SYS_PY = "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else None
BLENDER = "/usr/bin/blender" if Path("/usr/bin/blender").is_file() else shutil.which("blender")
HAS_GPU = bool(sbx.gpu_device_paths())

pytestmark = pytest.mark.skipif(
    BWRAP is None or SYS_PY is None,
    reason="bwrap + /usr/bin/python3 requis pour les tests d'intégration C1c",
)


def _mk_output(tmp_path):
    root = tmp_path / "outputs" / "blender"
    od = root / f"req_{uuid.uuid4().hex[:8]}"
    od.mkdir(parents=True)
    return str(root), str(od)


def test_strict_confinement_real(monkeypatch, tmp_path):
    """Le profil strict confine réellement l'exécution d'un script arbitraire."""
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")  # force le sandbox actif
    # secret d'environnement planté (ne doit jamais atteindre le process)
    monkeypatch.setenv("AAC_FAKE_SECRET", "leak-me-7723")

    root, od = _mk_output(tmp_path)
    real_home = os.path.expanduser("~")
    # Cibles d'écriture HORS output, hors /tmp (le sandbox remplace /tmp par un
    # tmpfs éphémère, donc /tmp n'est pas un témoin valide d'évasion vers l'hôte).
    home_breach = os.path.join(real_home, f"aac_breach_{uuid.uuid4().hex}.txt")
    etc_breach = "/etc/aac_breach.txt"
    result_path = os.path.join(od, "result.json")

    probe = f"""
import os, json, socket
res = {{}}
# 1) lire l'arborescence home réelle (couvre ~/.ssh, ~/.env) — doit échouer
try:
    os.listdir({real_home!r}); res["home_listable"] = True
except Exception:
    res["home_listable"] = False
# 2) secret d'environnement — doit être absent
res["env_secret_present"] = "AAC_FAKE_SECRET" in os.environ
res["env_value_leak"] = any("leak-me" in v for v in os.environ.values())
# 3) réseau — injoignable
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3).close(); res["network"] = True
except Exception:
    res["network"] = False
# 4) écrire dans le home réel — doit échouer (non monté)
try:
    open({home_breach!r}, "w").write("breach"); res["home_write"] = True
except Exception:
    res["home_write"] = False
# 5) écrire dans /etc — doit échouer (ro-bind)
try:
    open({etc_breach!r}, "w").write("breach"); res["etc_write"] = True
except Exception:
    res["etc_write"] = False
# 6) écrire dans output — doit réussir
try:
    open(os.path.join({od!r}, "inside.txt"), "w").write("ok"); res["inside_write"] = True
except Exception:
    res["inside_write"] = False
res["env_keys"] = sorted(os.environ.keys())
json.dump(res, open({result_path!r}, "w"))
"""
    probe_path = os.path.join(od, "probe.py")
    Path(probe_path).write_text(probe)

    plan = build_sandbox_plan(
        [SYS_PY, probe_path],
        output_dir=od, profile=PROFILE_STRICT, output_root=root,
    )
    assert plan.active is True and plan.backend == "bwrap"
    subprocess.run(plan.argv, capture_output=True, text=True, timeout=60)

    res = json.loads(Path(result_path).read_text())
    assert res["home_listable"] is False, "home réel ne doit pas être listable (.ssh/.env)"
    assert res["env_secret_present"] is False, "le secret d'env a fuité dans le sandbox"
    assert res["env_value_leak"] is False, "une valeur secrète a fuité dans l'env"
    assert res["network"] is False, "le réseau doit être injoignable"
    assert res["home_write"] is False, "écriture dans le home doit être refusée"
    assert res["etc_write"] is False, "écriture dans /etc (ro) doit être refusée"
    assert res["inside_write"] is True, "écriture dans output doit être autorisée"
    assert "AAC_FAKE_SECRET" not in res["env_keys"]
    # aucune évasion réelle côté host
    assert not Path(home_breach).exists()
    assert not Path(etc_breach).exists()
    assert Path(od, "inside.txt").is_file()  # témoin positif : output rw fonctionne


def test_strict_report_roundtrip_via_extra_rw(monkeypatch, tmp_path):
    """Un fichier rapport hors output, monté via extra_rw_paths, est bien
    écrit dans le sandbox et relu côté host (cas du validator d'inspection)."""
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")
    root, od = _mk_output(tmp_path)
    report = tmp_path / "report.json"               # hors output (comme /tmp système)
    report.write_text("{}")
    probe_path = os.path.join(od, "probe.py")
    Path(probe_path).write_text(
        f'import json; json.dump({{"ok": 1}}, open({str(report)!r}, "w"))'
    )
    plan = build_sandbox_plan(
        [SYS_PY, probe_path],
        output_dir=od, profile=PROFILE_STRICT, output_root=root,
        extra_rw_paths=[str(report)],
    )
    subprocess.run(plan.argv, capture_output=True, text=True, timeout=60)
    assert json.loads(report.read_text()) == {"ok": 1}


@pytest.mark.skipif(BLENDER is None or not HAS_GPU,
                    reason="Blender + GPU requis pour le rendu EEVEE")
def test_render_profile_produces_png(monkeypatch, tmp_path):
    """Le profil render produit réellement un PNG EEVEE sous sandbox GPU."""
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")
    root, od = _mk_output(tmp_path)
    png = os.path.join(od, "p.png")
    script = os.path.join(od, "render.py")
    Path(script).write_text(
        "import bpy\n"
        "sc = bpy.context.scene\n"
        "sc.render.engine = 'BLENDER_EEVEE'\n"
        "sc.render.image_settings.file_format = 'PNG'\n"
        "sc.render.resolution_x = 128\n"
        "sc.render.resolution_y = 128\n"
        f"sc.render.filepath = {png!r}\n"
        "bpy.ops.render.render(write_still=True)\n"
    )
    plan = build_sandbox_plan(
        [BLENDER, "--background", "--factory-startup", "--disable-autoexec",
         "--python", script],
        output_dir=od, profile=PROFILE_RENDER, output_root=root,
    )
    assert plan.active is True
    subprocess.run(plan.argv, capture_output=True, text=True, timeout=180)
    assert Path(png).is_file() and Path(png).stat().st_size > 0


@pytest.mark.skipif(shutil.which("pgrep") is None or shutil.which("bash") is None,
                    reason="pgrep + bash requis")
def test_timeout_destroys_process_group(monkeypatch, tmp_path):
    """Au timeout, tuer bwrap (PID ns) détruit tout le groupe : aucun
    descendant ne survit (C1c #6)."""
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")
    root, od = _mk_output(tmp_path)
    marker = 40000 + (uuid.uuid4().int % 10000)     # durée-sentinelle unique
    bash = shutil.which("bash")
    plan = build_sandbox_plan(
        [bash, "-c", f"sleep {marker} & sleep {marker}"],
        output_dir=od, profile=PROFILE_STRICT, output_root=root,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(plan.argv, capture_output=True, timeout=2)
    time.sleep(1.0)  # laisser le noyau réclamer les processus du namespace
    survivors = subprocess.run(
        ["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True
    )
    assert survivors.stdout.strip() == "", f"survivants: {survivors.stdout!r}"
