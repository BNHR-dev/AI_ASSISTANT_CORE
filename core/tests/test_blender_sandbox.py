"""C1c — Tests unitaires hermétiques du builder de sandbox bwrap.

Aucun de ces tests ne lance bwrap : ils vérifient la COMPOSITION de l'argv et
la logique de mode/validation. La résolution du backend et la découverte GPU
sont monkeypatchées. Les tests d'intégration (vrai bwrap, confinement réel,
rendu GPU, teardown timeout) sont dans `test_blender_sandbox_integration.py`.
"""

from __future__ import annotations

import pytest

from app.clients import blender_sandbox as sbx
from app.clients.blender_sandbox import (
    BACKEND_BWRAP,
    BACKEND_NONE,
    PROFILE_RENDER,
    PROFILE_STRICT,
    SANDBOX_ENV_VAR,
    SandboxError,
    build_sandbox_plan,
    current_mode,
)


def _blender_argv(output_dir: str) -> list[str]:
    return [
        "/usr/bin/blender", "--background", "--factory-startup",
        "--python", f"{output_dir}/scene.py",
    ]


@pytest.fixture
def fake_bwrap(monkeypatch):
    """Force bwrap résolu + utilisable, sans toucher au vrai binaire."""
    monkeypatch.setattr(sbx, "_resolve_bwrap_exe", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sbx, "_bwrap_usable", lambda: True)


@pytest.fixture
def out_dir(tmp_path):
    """(root autorisée, output_dir réel sous la racine)."""
    root = tmp_path / "outputs" / "blender"
    od = root / "req1"
    od.mkdir(parents=True)
    return str(root), str(od)


def _flag_present(argv, *seq) -> bool:
    """True si la sous-séquence `seq` apparaît consécutivement dans argv."""
    n = len(seq)
    return any(tuple(argv[i:i + n]) == seq for i in range(len(argv) - n + 1))


# --------------------------------------------------------------------------- #
# Mode
# --------------------------------------------------------------------------- #
def test_mode_default_is_auto(monkeypatch):
    monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
    assert current_mode() == "auto"


def test_mode_invalid_falls_back_to_auto(monkeypatch):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "bogus")
    assert current_mode() == "auto"


@pytest.mark.parametrize("mode", ["auto", "require", "off"])
def test_mode_valid_passthrough(monkeypatch, mode):
    monkeypatch.setenv(SANDBOX_ENV_VAR, mode.upper())  # insensible à la casse
    assert current_mode() == mode


def test_off_mode_is_passthrough(monkeypatch, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "off")
    root, od = out_dir
    argv = _blender_argv(od)
    plan = build_sandbox_plan(argv, output_dir=od, profile=PROFILE_STRICT, output_root=root)
    assert plan.argv == argv
    assert plan.backend == BACKEND_NONE
    assert plan.active is False


# --------------------------------------------------------------------------- #
# Profil strict — confinement, env, absence GPU
# --------------------------------------------------------------------------- #
def test_strict_plan_structure(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, od = out_dir
    argv = _blender_argv(od)
    plan = build_sandbox_plan(argv, output_dir=od, profile=PROFILE_STRICT, output_root=root)

    assert plan.backend == BACKEND_BWRAP
    assert plan.active is True
    assert plan.argv[0] == "/usr/bin/bwrap"
    # réseau coupé explicitement + env effacé + home neutre
    assert "--unshare-net" in plan.argv
    assert "--clearenv" in plan.argv
    assert _flag_present(plan.argv, "--setenv", "HOME", "/tmp")
    # output monté rw (chemin canonique)
    import os
    canon = os.path.realpath(od)
    assert _flag_present(plan.argv, "--bind", canon, canon)
    # strict = AUCUN /sys, AUCUN device GPU
    assert not _flag_present(plan.argv, "--ro-bind", "/sys", "/sys")
    assert "--dev-bind" not in plan.argv
    # la commande Blender est passée après le séparateur --
    sep = plan.argv.index("--")
    assert plan.argv[sep + 1:] == argv


def test_strict_does_not_leak_secrets(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    # secrets plantés dans l'env hôte
    monkeypatch.setenv("AAC_FAKE_TOKEN", "leak-me-7723")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/1000/keyring/ssh")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "deadbeef")
    root, od = out_dir
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_STRICT, output_root=root)
    joined = " ".join(plan.argv)
    assert "leak-me-7723" not in joined
    assert "AAC_FAKE_TOKEN" not in plan.argv
    assert "SSH_AUTH_SOCK" not in plan.argv
    assert "AWS_SECRET_ACCESS_KEY" not in plan.argv
    assert "deadbeef" not in joined


def test_locale_env_is_allowlisted(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    root, od = out_dir
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_STRICT, output_root=root)
    assert _flag_present(plan.argv, "--setenv", "LANG", "fr_FR.UTF-8")


def test_unshare_net_present_in_both_profiles(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, od = out_dir
    for profile in (PROFILE_STRICT, PROFILE_RENDER):
        plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                                  profile=profile, output_root=root)
        assert "--unshare-net" in plan.argv, profile


# --------------------------------------------------------------------------- #
# Profil render — /sys + devices GPU précis, sans réseau ni home
# --------------------------------------------------------------------------- #
def test_render_plan_adds_sys_and_gpu_devices(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    monkeypatch.setattr(
        sbx, "gpu_device_paths",
        lambda: ["/dev/dri/renderD128", "/dev/nvidia0", "/dev/nvidiactl"],
    )
    root, od = out_dir
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_RENDER, output_root=root)
    assert _flag_present(plan.argv, "--ro-bind", "/sys", "/sys")
    assert _flag_present(plan.argv, "--dev-bind", "/dev/dri/renderD128", "/dev/dri/renderD128")
    assert _flag_present(plan.argv, "--dev-bind", "/dev/nvidia0", "/dev/nvidia0")
    assert _flag_present(plan.argv, "--dev-bind", "/dev/nvidiactl", "/dev/nvidiactl")
    # render reste sans réseau
    assert "--unshare-net" in plan.argv


def test_render_without_gpu_devices_has_no_dev_bind(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    monkeypatch.setattr(sbx, "gpu_device_paths", lambda: [])
    root, od = out_dir
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_RENDER, output_root=root)
    # /sys reste, mais aucun dev-bind si pas de GPU détecté
    assert _flag_present(plan.argv, "--ro-bind", "/sys", "/sys")
    assert "--dev-bind" not in plan.argv


# --------------------------------------------------------------------------- #
# Validation chemin output (C1c #4) — fail-closed sur échappement
# --------------------------------------------------------------------------- #
def test_output_dir_outside_root_raises(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, _ = out_dir
    with pytest.raises(SandboxError):
        build_sandbox_plan(_blender_argv("/etc"), output_dir="/etc",
                           profile=PROFILE_STRICT, output_root=root)


def test_symlink_escape_raises(monkeypatch, fake_bwrap, tmp_path):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root = tmp_path / "outputs" / "blender"
    root.mkdir(parents=True)
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    evil = root / "req_evil"
    evil.symlink_to(outside)  # symlink sous la racine pointant DEHORS
    with pytest.raises(SandboxError):
        build_sandbox_plan(_blender_argv(str(evil)), output_dir=str(evil),
                           profile=PROFILE_STRICT, output_root=str(root))


def test_output_dir_under_root_accepted(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, od = out_dir
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_STRICT, output_root=root)
    assert plan.active is True


# --------------------------------------------------------------------------- #
# Mode require — fail-closed quand bwrap absent / inutilisable
# --------------------------------------------------------------------------- #
def test_require_without_bwrap_raises(monkeypatch, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")
    monkeypatch.setattr(sbx, "_resolve_bwrap_exe", lambda: None)
    root, od = out_dir
    with pytest.raises(SandboxError):
        build_sandbox_plan(_blender_argv(od), output_dir=od,
                           profile=PROFILE_STRICT, output_root=root)


def test_require_with_unusable_bwrap_raises(monkeypatch, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")
    monkeypatch.setattr(sbx, "_resolve_bwrap_exe", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sbx, "_bwrap_usable", lambda: False)
    root, od = out_dir
    with pytest.raises(SandboxError):
        build_sandbox_plan(_blender_argv(od), output_dir=od,
                           profile=PROFILE_STRICT, output_root=root)


def test_auto_without_bwrap_is_passthrough(monkeypatch, out_dir, capsys):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    monkeypatch.setattr(sbx, "_resolve_bwrap_exe", lambda: None)
    root, od = out_dir
    argv = _blender_argv(od)
    plan = build_sandbox_plan(argv, output_dir=od, profile=PROFILE_STRICT, output_root=root)
    assert plan.argv == argv
    assert plan.backend == BACKEND_NONE
    assert plan.active is False
    # avertit fort sur l'absence de sandbox
    assert "bwrap" in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------- #
# extra_rw_paths / extra_ro_paths (binds framework, ex. rapport d'inspection)
# --------------------------------------------------------------------------- #
def test_extra_rw_path_bound_after_tmpfs(monkeypatch, fake_bwrap, out_dir, tmp_path):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, od = out_dir
    report = tmp_path / "report.json"
    report.write_text("{}")
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_STRICT, output_root=root,
                              extra_rw_paths=[str(report)])
    import os
    canon = os.path.realpath(str(report))
    assert _flag_present(plan.argv, "--bind", canon, canon)
    # le bind du rapport doit venir APRÈS le tmpfs pour primer dessus
    tmpfs_idx = plan.argv.index("--tmpfs")
    report_idx = plan.argv.index(canon)
    assert report_idx > tmpfs_idx


def test_extra_ro_path_bound(monkeypatch, fake_bwrap, out_dir, tmp_path):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, od = out_dir
    asset = tmp_path / "asset.txt"
    asset.write_text("x")
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_STRICT, output_root=root,
                              extra_ro_paths=[str(asset)])
    import os
    canon = os.path.realpath(str(asset))
    assert _flag_present(plan.argv, "--ro-bind", canon, canon)


# --------------------------------------------------------------------------- #
# Divers
# --------------------------------------------------------------------------- #
def test_invalid_profile_raises(out_dir):
    root, od = out_dir
    with pytest.raises(ValueError):
        build_sandbox_plan(_blender_argv(od), output_dir=od,
                           profile="weird", output_root=root)


def test_log_line_is_structured(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "require")
    root, od = out_dir
    plan = build_sandbox_plan(_blender_argv(od), output_dir=od,
                              profile=PROFILE_RENDER, output_root=root)
    line = plan.log_line()
    assert "backend=bwrap" in line
    assert "profile=render" in line
    assert "requested_mode=require" in line
    assert "active=true" in line


def test_exe_outside_usr_binds_its_dir(monkeypatch, fake_bwrap, out_dir):
    monkeypatch.setenv(SANDBOX_ENV_VAR, "auto")
    root, od = out_dir
    argv = [f"{od}/custom/blender", "--background", "--python", f"{od}/scene.py"]
    plan = build_sandbox_plan(argv, output_dir=od, profile=PROFILE_STRICT, output_root=root)
    import os
    exe_dir = os.path.realpath(f"{od}/custom")
    assert _flag_present(plan.argv, "--ro-bind", exe_dir, exe_dir)
