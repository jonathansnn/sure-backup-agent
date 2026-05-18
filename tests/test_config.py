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


def test_load_raises_when_toml_missing(tmp_path):
    with pytest.raises(cfg.ConfigError, match="não encontrado"):
        cfg.load(path=tmp_path / "nope.toml")
