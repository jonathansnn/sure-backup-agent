"""Entry point de producao do sure-backup-agent.

Chamado pelo Task Scheduler todo dia as 08:00 via run_daily.bat.

Suporta 3 modos de operacao via config.toml [mode].name:

  all          - single-server: captura V+P+TIM, envia tudo num POST pro
                 Fluxo PA "Send Daily Full" (legado, backward-compat).

  timeismoney  - VM produtora: captura TIM, POSTa pro Fluxo PA "Store TIM
                 Artifact" (que salva o PNG no OneDrive). NAO posta no Teams.

  veeam_ppdm   - VM agregadora: captura V+P, POSTa pro Fluxo PA
                 "Aggregate + Send" (que le TIM do OneDrive, combina com
                 V+P do payload, posta no Teams).

A "ponte" entre as 2 VMs no modo split eh feita pelos fluxos PA + OneDrive.
Cada VM tem seu proprio `teams_webhook` no keyring com a URL do fluxo certo.

Logs vao pra logs/agent.log (rotacionado diariamente, 30 dias de retencao).

Exit codes:
  0 - sucesso (POST pro PA retornou 2xx)
  1 - POST pro PA falhou apos todas as tentativas (payload salvo em logs/FAILED_*.json)
  2 - config invalida (TOML mal-formado, secret faltando, modo invalido)
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, ppdm_capture, teams_sender, timeismoney_capture, veeam_capture


def main() -> int:
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"[FATAL] config invalida: {exc}", file=sys.stderr)
        return 2

    log = logger.setup(cfg.logging.log_dir, cfg.logging.level, name="sure_backup_agent")
    log.info("================ INICIO (modo=%s) ================", cfg.mode.name)
    log.info("sure-backup-agent run iniciado em %s", socket.gethostname())

    if cfg.mode.name == config.MODE_TIMEISMONEY:
        return _run_timeismoney_producer(cfg, log)
    if cfg.mode.name == config.MODE_PPDM:
        return _run_ppdm_producer(cfg, log)
    if cfg.mode.name == config.MODE_VEEAM:
        return _run_veeam_aggregator(cfg, log)
    if cfg.mode.name == config.MODE_VEEAM_PPDM:
        return _run_veeam_ppdm_aggregator(cfg, log)
    return _run_all(cfg, log)


def _capture_veeam(cfg, log):
    log.info("Capturando Veeam Console...")
    img, err = veeam_capture.capture(cfg.veeam)
    if err:
        log.error("Veeam captura falhou: %s", err)
    else:
        log.info("Veeam captura OK: %d bytes", len(img))
    return img, err


def _capture_ppdm(cfg, log):
    log.info("Capturando PPDM Protection Jobs...")
    img, err = ppdm_capture.capture(cfg.ppdm)
    if err:
        log.error("PPDM captura falhou: %s", err)
    else:
        log.info("PPDM captura OK: %d bytes", len(img))
    return img, err


def _capture_tim(cfg, log):
    log.info("Capturando Time Is Money admin-dashboard...")
    img, err = timeismoney_capture.capture(cfg.timeismoney)
    if err:
        log.error("TimeIsMoney captura falhou: %s", err)
    else:
        log.info("TimeIsMoney captura OK: %d bytes", len(img))
    return img, err


def _send_and_finalize(cfg, log, payload, kind_label):
    """Wrapper comum: POSTa pro webhook configurado, loga, retorna exit code."""
    log.info("Enviando payload (%s) pro Power Automate...", kind_label)
    result = teams_sender.send(
        payload,
        webhook_url=cfg.teams.webhook_url,
        timeout=cfg.teams.http_timeout_seconds,
        max_attempts=cfg.teams.retry_attempts,
        backoff_seconds=cfg.teams.retry_backoff_seconds,
        fallback_dir=cfg.logging.log_dir,
    )
    if result.success:
        log.info("Envio OK (HTTP %s, run_id=%s, tentativas=%d)",
                 result.status_code, result.run_id, result.attempts)
        log.info("================ FIM (sucesso) ================")
        return 0
    log.error("Envio FALHOU apos %d tentativas: %s", result.attempts, result.error_message)
    log.error("Payload salvo como fallback em %s", cfg.logging.log_dir)
    log.info("================ FIM (falha) ================")
    return 1


def _run_all(cfg, log):
    """Modo legado/single-server: 1 VM faz tudo, manda payload completo."""
    veeam_img, veeam_err = _capture_veeam(cfg, log)
    ppdm_img, ppdm_err = _capture_ppdm(cfg, log)
    tim_img, tim_err = _capture_tim(cfg, log)
    payload = teams_sender.build_payload(
        veeam_image=veeam_img, veeam_error=veeam_err,
        ppdm_image=ppdm_img, ppdm_error=ppdm_err,
        timeismoney_image=tim_img, timeismoney_error=tim_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "full V+P+TIM")


def _run_timeismoney_producer(cfg, log):
    """Modo produtor TIM: captura e POSTa pro fluxo 'Store TIM Artifact'."""
    tim_img, tim_err = _capture_tim(cfg, log)
    payload = teams_sender.build_tim_only_payload(
        timeismoney_image=tim_img, timeismoney_error=tim_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "TIM-only")


def _run_ppdm_producer(cfg, log):
    """Modo produtor PPDM: captura e POSTa pro fluxo 'Store PPDM Artifact'.

    Espelha o modo timeismoney: nao envia mensagem pro Teams, so deposita
    o PNG no OneDrive (via fluxo PA) pro agregador ler depois.
    """
    ppdm_img, ppdm_err = _capture_ppdm(cfg, log)
    payload = teams_sender.build_ppdm_only_payload(
        ppdm_image=ppdm_img, ppdm_error=ppdm_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "PPDM-only")


def _run_veeam_aggregator(cfg, log):
    """Modo agregador full-split: captura Veeam, POSTa pro fluxo Aggregate+Send.

    O fluxo PA le PPDM E TIM do OneDrive (gravados pelos respectivos
    produtores) e combina os 3 numa mensagem unica pro Teams.
    """
    veeam_img, veeam_err = _capture_veeam(cfg, log)
    payload = teams_sender.build_veeam_only_payload(
        veeam_image=veeam_img, veeam_error=veeam_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "Veeam-only (P+TIM vem do OneDrive)")


def _run_veeam_ppdm_aggregator(cfg, log):
    """Modo agregador V+P: captura e POSTa pro fluxo 'Aggregate + Send'.

    O fluxo PA le o TIM do OneDrive (gravado mais cedo pela VM-TIM) e
    combina com V+P do payload antes de postar no Teams.
    """
    veeam_img, veeam_err = _capture_veeam(cfg, log)
    ppdm_img, ppdm_err = _capture_ppdm(cfg, log)
    payload = teams_sender.build_veeam_ppdm_payload(
        veeam_image=veeam_img, veeam_error=veeam_err,
        ppdm_image=ppdm_img, ppdm_error=ppdm_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "V+P (TIM vem do OneDrive)")


if __name__ == "__main__":
    sys.exit(main())
