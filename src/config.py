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
    # Modo produtor (name="ppdm"): tentativas de captura por execucao antes de
    # desistir. Se todas falharem, o agente NAO envia (preserva o ultimo print
    # bom no OneDrive). So tem efeito no modo 'ppdm' producer.
    capture_retry_attempts: int = 1
    capture_retry_delay_seconds: int = 10


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
MODE_VEEAM_PPDM = "veeam_ppdm"    # legado split: captura V+P, le TIM do OneDrive, envia
MODE_TIMEISMONEY = "timeismoney"  # produtor: captura TIM, posta pro Fluxo "Store TIM"
MODE_PPDM = "ppdm"                # produtor: captura PPDM, posta pro Fluxo "Store PPDM"
MODE_VEEAM = "veeam"              # agregador full-split: captura V, le P+TIM do OneDrive, envia
VALID_MODES = {MODE_ALL, MODE_VEEAM_PPDM, MODE_TIMEISMONEY, MODE_PPDM, MODE_VEEAM}


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


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge recursivo: tabelas aninhadas mesclam; escalares do override vencem.

    Usado pra sobrepor o config.toml (versionado, com placeholders) com o
    config.local.toml (gitignored, com URLs/valores reais).
    """
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _resolve_webhook(data: dict, mode_name: str, service_teams: str) -> str:
    """Resolve a URL do webhook do Power Automate pro modo atual.

    Prioridade:
      1. [webhooks].<modo>   no config (real vem do config.local.toml, gitignored)
      2. [webhooks].default  no config (URL unica que serve qualquer modo)
      3. keyring service_teams_webhook  (fallback legado dos deploys antigos)

    Suporta ter varias URLs no mesmo arquivo (uma por rotina); o modo ativo
    seleciona a sua. Assim 2 checkouts no mesmo servidor nao colidem.
    """
    webhooks = data.get("webhooks", {})
    url = str(webhooks.get(mode_name) or webhooks.get("default") or "").strip()
    if url:
        return url
    # Fallback legado: Windows Credential Manager
    legacy = keyring.get_password(service_teams, "url")
    return (legacy or "").strip()


def load(path: Path = CONFIG_PATH, *, require_ppdm_password: bool = True) -> Config:
    """Carrega configuração do TOML + secrets.

    Mescla um `config.local.toml` opcional (mesma pasta, gitignored) por cima
    do `config.toml` — use-o pra guardar as URLs reais dos webhooks (que contem
    `sig=` secreto) fora do git.

    Webhook do Power Automate: vem de `[webhooks].<modo>` no config (ver
    `_resolve_webhook`), com fallback pro keyring legado. Senhas (PPDM, TIM)
    continuam no Windows Credential Manager via keyring.

    `require_ppdm_password=False` continua suportado pra desenvolvimento.
    """
    if not path.exists():
        raise ConfigError(f"config.toml não encontrado em {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    # Override opcional fora do git (URLs reais, secrets): config.local.toml
    local_path = path.with_name("config.local.toml")
    if local_path.exists():
        with local_path.open("rb") as f:
            data = _deep_merge(data, tomllib.load(f))

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

    # Cada modo POSTa pro fluxo PA correspondente; a URL vem de [webhooks].<modo>
    # (config.local.toml > config.toml > keyring legado). Senhas (PPDM/TIM) so
    # sao exigidas pelas fontes que esse modo captura localmente.
    needs_ppdm = mode_name in (MODE_ALL, MODE_VEEAM_PPDM, MODE_PPDM) and require_ppdm_password
    needs_tim = mode_name in (MODE_ALL, MODE_TIMEISMONEY) and require_ppdm_password

    webhook_url = _resolve_webhook(data, mode_name, service_teams)
    if not webhook_url:
        raise ConfigError(
            f"Webhook do Power Automate nao configurado pro modo '{mode_name}'. "
            f"Preencha [webhooks].{mode_name} (ou [webhooks].default) no "
            f"config.local.toml (recomendado, fora do git) ou no config.toml. "
            f"Fallback legado: keyring '{service_teams}' username 'url'."
        )
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
            capture_retry_attempts=data["ppdm"].get("capture_retry_attempts", 1),
            capture_retry_delay_seconds=data["ppdm"].get("capture_retry_delay_seconds", 10),
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
