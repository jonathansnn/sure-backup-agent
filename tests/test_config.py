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


def _write_toml_with_mode(tmp_path, mode_name):
    base = _write_toml(tmp_path).read_text(encoding="utf-8")
    mode_section = textwrap.dedent(f"""
        [mode]
        name = "{mode_name}"
    """)
    path = tmp_path / "config.toml"
    path.write_text(base + mode_section, encoding="utf-8")
    return path


def test_mode_veeam_ppdm_does_not_require_tim_secret(mocker, tmp_path):
    """No agregador, secret do TIM nem eh consultado. PA cuida do TIM."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam_ppdm")
    secrets = {("test/teams", "url"): "wh-vp", ("test/ppdm", "svc_test"): "p"}
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: secrets.get((s, u)),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_VEEAM_PPDM
    assert c.timeismoney.password == ""
    assert c.ppdm.password == "p"
    assert c.teams.webhook_url == "wh-vp"


def test_mode_timeismoney_does_not_require_webhook_nor_ppdm(mocker, tmp_path):
    """No produtor TIM, so o webhook (apontando pro Fluxo B) e o TIM importam."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "timeismoney")
    secrets = {
        ("test/teams", "url"): "wh-tim-upload",
        ("test/tim", "rootadmin@test.com"): "t",
    }
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: secrets.get((s, u)),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_TIMEISMONEY
    assert c.timeismoney.password == "t"
    assert c.teams.webhook_url == "wh-tim-upload"
    assert c.ppdm.password == ""


def test_mode_ppdm_requires_ppdm_secret_but_not_tim(mocker, tmp_path):
    """Modo produtor PPDM: webhook (Fluxo D) + senha PPDM. TIM ignorado."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "ppdm")
    secrets = {
        ("test/teams", "url"): "wh-ppdm-upload",
        ("test/ppdm", "svc_test"): "p",
    }
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: secrets.get((s, u)),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_PPDM
    assert c.ppdm.password == "p"
    assert c.timeismoney.password == ""
    assert c.teams.webhook_url == "wh-ppdm-upload"


def test_mode_veeam_aggregator_does_not_require_ppdm_nor_tim(mocker, tmp_path):
    """Modo agregador full-split: so webhook (Fluxo C'). P+TIM vem do OneDrive."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam")
    secrets = {("test/teams", "url"): "wh-veeam-aggregator"}
    mocker.patch(
        "src.config.keyring.get_password",
        side_effect=lambda s, u: secrets.get((s, u)),
    )
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.mode.name == cfg.MODE_VEEAM
    assert c.ppdm.password == ""
    assert c.timeismoney.password == ""
    assert c.teams.webhook_url == "wh-veeam-aggregator"


def test_invalid_mode_raises(mocker, tmp_path):
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "modo_invalido")
    with pytest.raises(cfg.ConfigError, match="invalido"):
        cfg.load(path=path, require_ppdm_password=False)


# ---------- webhooks via config ([webhooks] + config.local.toml) ----------

def _append(path: Path, extra: str) -> Path:
    path.write_text(path.read_text(encoding="utf-8") + textwrap.dedent(extra), encoding="utf-8")
    return path


def test_webhook_from_config_section_no_keyring(mocker, tmp_path):
    """[webhooks].<modo> no config dispensa o keyring pro webhook."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam")
    _append(path, """
        [webhooks]
        veeam = "https://flow-c-linha"
    """)
    # keyring nao tem webhook; nem deve ser consultado pra ele
    mocker.patch("src.config.keyring.get_password", return_value=None)
    c = cfg.load(path=path, require_ppdm_password=False)
    assert c.teams.webhook_url == "https://flow-c-linha"


def test_webhook_multiple_urls_mode_selects_right_one(mocker, tmp_path):
    """Varias URLs no mesmo arquivo; o modo ativo escolhe a sua."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "ppdm")
    _append(path, """
        [webhooks]
        ppdm = "https://flow-d-ppdm"
        veeam = "https://flow-c-veeam"
        timeismoney = "https://flow-b-tim"
    """)
    secrets = {("test/ppdm", "svc_test"): "p"}
    mocker.patch("src.config.keyring.get_password", side_effect=lambda s, u: secrets.get((s, u)))
    c = cfg.load(path=path, require_ppdm_password=True)
    assert c.teams.webhook_url == "https://flow-d-ppdm"


def test_webhook_default_fallback_when_mode_absent(mocker, tmp_path):
    """Sem entrada pro modo, usa [webhooks].default."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam")
    _append(path, """
        [webhooks]
        default = "https://flow-unico"
    """)
    mocker.patch("src.config.keyring.get_password", return_value=None)
    c = cfg.load(path=path, require_ppdm_password=False)
    assert c.teams.webhook_url == "https://flow-unico"


def test_config_local_toml_overrides_base(mocker, tmp_path):
    """config.local.toml (gitignored) sobrepoe o config.toml versionado."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam")
    _append(path, """
        [webhooks]
        veeam = "PLACEHOLDER_NAO_USAR"
    """)
    # arquivo local (mesma pasta) com a URL real
    (tmp_path / "config.local.toml").write_text(textwrap.dedent("""
        [webhooks]
        veeam = "https://url-real-do-local"
    """), encoding="utf-8")
    mocker.patch("src.config.keyring.get_password", return_value=None)
    c = cfg.load(path=path, require_ppdm_password=False)
    assert c.teams.webhook_url == "https://url-real-do-local"


def test_webhook_falls_back_to_keyring_when_config_empty(mocker, tmp_path):
    """Sem [webhooks], cai no keyring legado (retrocompat)."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam")
    mocker.patch("src.config.keyring.get_password",
                 side_effect=lambda s, u: "wh-legado" if s == "test/teams" else None)
    c = cfg.load(path=path, require_ppdm_password=False)
    assert c.teams.webhook_url == "wh-legado"


def test_webhook_raises_when_nowhere(mocker, tmp_path):
    """Sem [webhooks] e sem keyring -> ConfigError claro."""
    mocker.patch("src.config.PROJECT_ROOT", tmp_path)
    path = _write_toml_with_mode(tmp_path, "veeam")
    mocker.patch("src.config.keyring.get_password", return_value=None)
    with pytest.raises(cfg.ConfigError, match="[Ww]ebhook"):
        cfg.load(path=path, require_ppdm_password=False)


def test_load_raises_when_toml_missing(tmp_path):
    with pytest.raises(cfg.ConfigError, match="não encontrado"):
        cfg.load(path=tmp_path / "nope.toml")
