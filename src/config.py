"""Leitura de config.toml + Windows Credential Manager via keyring."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import keyring

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"


@dataclass(frozen=True)
class VeeamConfig:
    console_path: str
    window_title_regex: str
    launch_timeout_seconds: int
    crop_top: int = 0
    crop_left: int = 0
    crop_right: int = 0
    crop_bottom: int = 0


@dataclass(frozen=True)
class PpdmConfig:
    url: str
    username: str
    password: str  # carregado do keyring, nunca do TOML
    login_timeout_seconds: int
    browser: str
    headless: bool
    viewport_width: int
    viewport_height: int
    ignore_https_errors: bool = False


@dataclass(frozen=True)
class TimeIsMoneyConfig:
    url: str
    dashboard_url: str
    username: str
    password: str  # carregado do keyring
    login_timeout_seconds: int
    browser: str
    headless: bool
    viewport_width: int
    viewport_height: int
    bottom_anchor_text: str
    bottom_padding_px: int


@dataclass(frozen=True)
class TeamsConfig:
    webhook_url: str  # carregado do keyring, nunca do TOML
    http_timeout_seconds: int
    retry_attempts: int
    retry_backoff_seconds: int


@dataclass(frozen=True)
class LoggingConfig:
    log_dir: Path
    level: str


# Modos de operacao do agente em deploy multi-servidor.
# Cada VM tem seu config.toml com um valor diferente em [mode].name.
MODE_ALL = "all"                  # captura V+P+TIM e envia (single-server, default)
MODE_VEEAM_PPDM = "veeam_ppdm"    # captura V+P, le TIM de shared_dir, envia
MODE_TIMEISMONEY = "timeismoney"  # captura TIM, escreve em shared_dir, NAO envia
VALID_MODES = {MODE_ALL, MODE_VEEAM_PPDM, MODE_TIMEISMONEY}


@dataclass(frozen=True)
class ModeConfig:
    """Modo de operacao em deploy multi-servidor.

    A ponte entre as VMs eh feita por 3 fluxos Power Automate distintos.
    Cada VM tem seu proprio `teams_webhook` (mesmo nome de secret, valor
    diferente por VM apontando pro fluxo certo).
    """
    name: str


@dataclass(frozen=True)
class Config:
    veeam: VeeamConfig
    ppdm: PpdmConfig
    timeismoney: TimeIsMoneyConfig
    teams: TeamsConfig
    logging: LoggingConfig
    mode: ModeConfig


class ConfigError(Exception):
    """Erro de configuração — secret faltando, TOML mal formado, etc."""


def _get_secret(service: str, username: str) -> str:
    value = keyring.get_password(service, username)
    if not value:
        raise ConfigError(
            f"Secret não encontrado no Windows Credential Manager: "
            f"target='{service}' username='{username}'. "
            f"Configure com: python -m keyring set {service} {username}"
        )
    return value


def load(path: Path = CONFIG_PATH, *, require_ppdm_password: bool = True) -> Config:
    """Carrega configuração do TOML + secrets do Credential Manager.

    Quais secrets sao exigidos depende do `[mode].name`:
      - all          -> webhook + ppdm + tim
      - veeam_ppdm   -> webhook + ppdm  (sem tim; le artefato do shared_dir)
      - timeismoney  -> tim             (sem webhook nem ppdm; nao envia)

    `require_ppdm_password=False` continua suportado pra desenvolvimento, mas
    a checagem efetiva eh feita por modo abaixo.
    """
    if not path.exists():
        raise ConfigError(f"config.toml não encontrado em {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    mode_data = data.get("mode", {})
    mode_name = mode_data.get("name", MODE_ALL)
    if mode_name not in VALID_MODES:
        raise ConfigError(
            f"[mode].name='{mode_name}' invalido. Valores aceitos: {sorted(VALID_MODES)}"
        )

    keyring_cfg = data.get("keyring", {})
    service_teams = keyring_cfg.get("service_teams_webhook", "sure-backup-agent/teams_webhook")
    service_ppdm = keyring_cfg.get("service_ppdm", "sure-backup-agent/ppdm")
    service_tim = keyring_cfg.get("service_timeismoney", "sure-backup-agent/timeismoney")

    # Quais secrets exigir depende do modo. Cada modo POSTa pra um fluxo PA
    # diferente, mas todos usam o mesmo NOME de secret (teams_webhook) — o
    # VALOR muda por VM. Modo TIM nao precisa de senha PPDM; modo V+P nao
    # precisa de senha TIM.
    needs_webhook = True
    needs_ppdm = mode_name in (MODE_ALL, MODE_VEEAM_PPDM) and require_ppdm_password
    needs_tim = mode_name in (MODE_ALL, MODE_TIMEISMONEY) and require_ppdm_password

    webhook_url = _get_secret(service_teams, "url") if needs_webhook else ""
    ppdm_password = _get_secret(service_ppdm, data["ppdm"]["username"]) if needs_ppdm else ""
    tim_password = _get_secret(service_tim, data["timeismoney"]["username"]) if needs_tim else ""

    log_dir = PROJECT_ROOT / data["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)

    veeam_data = data["veeam"]
    return Config(
        veeam=VeeamConfig(
            console_path=veeam_data["console_path"],
            window_title_regex=veeam_data["window_title_regex"],
            launch_timeout_seconds=veeam_data["launch_timeout_seconds"],
            crop_top=veeam_data.get("crop_top", 0),
            crop_left=veeam_data.get("crop_left", 0),
            crop_right=veeam_data.get("crop_right", 0),
            crop_bottom=veeam_data.get("crop_bottom", 0),
        ),
        ppdm=PpdmConfig(
            url=data["ppdm"]["url"],
            username=data["ppdm"]["username"],
            password=ppdm_password,
            login_timeout_seconds=data["ppdm"]["login_timeout_seconds"],
            browser=data["ppdm"]["browser"],
            headless=data["ppdm"]["headless"],
            viewport_width=data["ppdm"]["viewport_width"],
            viewport_height=data["ppdm"]["viewport_height"],
            ignore_https_errors=data["ppdm"].get("ignore_https_errors", False),
        ),
        timeismoney=TimeIsMoneyConfig(
            url=data["timeismoney"]["url"],
            dashboard_url=data["timeismoney"]["dashboard_url"],
            username=data["timeismoney"]["username"],
            password=tim_password,
            login_timeout_seconds=data["timeismoney"]["login_timeout_seconds"],
            browser=data["timeismoney"]["browser"],
            headless=data["timeismoney"]["headless"],
            viewport_width=data["timeismoney"]["viewport_width"],
            viewport_height=data["timeismoney"]["viewport_height"],
            bottom_anchor_text=data["timeismoney"]["bottom_anchor_text"],
            bottom_padding_px=data["timeismoney"]["bottom_padding_px"],
        ),
        teams=TeamsConfig(
            webhook_url=webhook_url,
            http_timeout_seconds=data["teams"]["http_timeout_seconds"],
            retry_attempts=data["teams"]["retry_attempts"],
            retry_backoff_seconds=data["teams"]["retry_backoff_seconds"],
        ),
        logging=LoggingConfig(log_dir=log_dir, level=data["logging"]["level"]),
        mode=ModeConfig(name=mode_name),
    )
