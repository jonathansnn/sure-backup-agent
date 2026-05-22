"""Testes do loader de config — TOML + keyring."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src import config as cfg


def _write_toml(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent("""
        [veeam]
        console_path = "C:/fake/Veeam.exe"
        window_title_regex = "Veeam"
        launch_timeout_seconds = 30

        [ppdm]
        url = "https://ppdm.test"
        username = "svc_test"
        login_timeout_seconds = 30
        browser = "chromium"
        headless = true
        viewport_width = 1600
        viewport_height = 900

        [timeismoney]
        url = "https://tim.test/login"
        dashboard_url = "https://tim.test/admin-dashboard"
        username = "rootadmin@test.com"
        login_timeout_seconds = 30
        browser = "chromium"
        headless = true
        viewport_width = 1600
        viewport_height = 900
        bottom_anchor_text = "Taxa de colaboradores ativos"
        bottom_padding_px = 60

        [teams]
        http_timeout_seconds = 30
        retry_attempts = 3
        retry_backoff_seconds = 5

        [logging]
        log_dir = "logs_test"
        level = "INFO"

        [keyring]
        service_ppdm = "test/ppdm"
        service_timeismoney = "test/tim"
        service_teams_webhook = "test/teams"
    """), encoding="utf-8")
    return path


def test_load_with_all_secrets(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml(tmp_path)

    fake_secrets = {
        ("test/teams", "url"): "https://webhook.example/abc?sig=xyz",
        ("test/ppdm", "svc_test"): "super-secret-pw",
        ("test/tim", "rootadmin@test.com"): "tim-secret-pw",
    }
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: fake_secrets.get((s, u)),
    )

    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.teams.webhook_url == "https://webhook.example/abc?sig=xyz"
    assert c.ppdm.password == "super-secret-pw"
    assert c.ppdm.username == "svc_test"
    assert c.timeismoney.password == "tim-secret-pw"
    assert c.timeismoney.username == "rootadmin@test.com"
    assert c.timeismoney.dashboard_url == "https://tim.test/admin-dashboard"
    assert c.veeam.window_title_regex == "Veeam"
    assert c.logging.log_dir == tmp_path / "logs_test"
    assert c.logging.log_dir.is_dir()  # criado automaticamente


def test_load_without_ppdm_password_when_not_required(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml(tmp_path)

    # Só webhook configurado, PPDM nem definido
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: "wh-url" if s == "test/teams" else None,
    )
    c = cfg.load(path=path, require_ppdm_password=False)
    assert c.teams.webhook_url == "wh-url"
    assert c.ppdm.password == ""


def test_load_raises_when_webhook_missing(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml(tmp_path)
    mocker.patch("src.config.keyring.get_password", return_value=None)
    with pytest.raises(cfg.ConfigError, match="teams_webhook|teams"):
        cfg.load(path=path, require_ppdm_password=False)


def test_load_raises_when_ppdm_password_missing_and_required(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml(tmp_path)
    # webhook ok, ppdm faltando
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: "wh-url" if s == "test/teams" else None,
    )
    with pytest.raises(cfg.ConfigError, match="ppdm|svc_test"):
        cfg.load(path=path, require_ppdm_password=True)


def test_default_mode_is_all_when_omitted(mocker, tmp_path):
    """TOML sem [mode] = retrocompatibilidade, default 'all'."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml(tmp_path)
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: {"test/teams": "wh", "test/ppdm": "p", "test/tim": "t"}.get(s),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_ALL
    assert c.mode.shared_artifact_dir is None
    assert c.mode.artifact_max_age_minutes == 60


def _write_toml_with_mode(tmp_path, mode_name, shared_dir=r"\\fileserver\share"):
    base = _write_toml(tmp_path).read_text(encoding="utf-8")
    # Aspas simples no TOML = literal string, sem escape (ideal pra paths Windows)
    mode_section = textwrap.dedent(f"""
        [mode]
        name = "{mode_name}"
        shared_artifact_dir = '{shared_dir}'
        artifact_max_age_minutes = 30
    """)
    path = tmp_path / "config.toml"
    path.write_text(base + mode_section, encoding="utf-8")
    return path


def test_mode_veeam_ppdm_does_not_require_tim_secret(mocker, tmp_path):
    """No agregador, secret do TIM nem eh consultado."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam_ppdm")
    # So webhook + ppdm setados. TIM ausente nao deve ser fatal.
    secrets = {("test/teams", "url"): "wh", ("test/ppdm", "svc_test"): "p"}
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: secrets.get((s, u)),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_VEEAM_PPDM
    assert c.timeismoney.password == ""  # nao carregado
    assert c.ppdm.password == "p"
    assert c.teams.webhook_url == "wh"
    assert c.mode.artifact_max_age_minutes == 30


def test_mode_timeismoney_does_not_require_webhook_nor_ppdm(mocker, tmp_path):
    """No produtor TIM, so o secret do TIM importa."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "timeismoney")
    secrets = {("test/tim", "rootadmin@test.com"): "t"}
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: secrets.get((s, u)),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_TIMEISMONEY
    assert c.timeismoney.password == "t"
    assert c.teams.webhook_url == ""
    assert c.ppdm.password == ""


def test_invalid_mode_raises(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "modo_invalido")
    with pytest.raises(cfg.ConfigError, match="invalido"):
        cfg.load(path=path, require_ppdm_password=False)


def test_split_mode_requires_shared_dir(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    # shared_dir vazio
    path = _write_toml_with_mode(tmp_path, "veeam_ppdm", shared_dir="")
    with pytest.raises(cfg.ConfigError, match="shared_artifact_dir"):
        cfg.load(path=path, require_ppdm_password=False)


def test_load_raises_when_toml_missing(tmp_path):
    with pytest.raises(cfg.ConfigError, match="não encontrado"):
        cfg.load(path=tmp_path / "nope.toml")
