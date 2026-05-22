"""Entry point de producao do sure-backup-agent.

Chamado pelo Task Scheduler todo dia as 08:00 via run_daily.bat.

Suporta 3 modos de operacao definidos em config.toml [mode].name:

  all          - single-server: captura V+P+TIM, envia pro Teams (legado)
  veeam_ppdm   - captura V+P, le TIM do shared_dir, envia pro Teams
  timeismoney  - captura TIM, escreve no shared_dir, NAO envia (produtor)

Logs vao pra logs/agent.log (rotacionado diariamente, 30 dias de retencao).

Exit codes:
  0 - sucesso (envio Teams OK, ou produtor TIM gravou artefato)
  1 - envio Teams falhou apos todas as tentativas (so aplica em modos que enviam)
  2 - config invalida (TOML mal-formado, secret faltando, modo invalido)
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import (
    artifact_store,
    config,
    logger,
    ppdm_capture,
    teams_sender,
    timeismoney_capture,
    veeam_capture,
)


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
        return _run_timeismoney_only(cfg, log)
    if cfg.mode.name == config.MODE_VEEAM_PPDM:
        return _run_veeam_ppdm_and_send(cfg, log)
    return _run_all(cfg, log)


def _capture_veeam_and_ppdm(cfg, log):
    """Captura V+P no mesmo servidor. Retorna ((v_img,v_err),(p_img,p_err))."""
    log.info("Capturando Veeam Console...")
    veeam_img, veeam_err = veeam_capture.capture(cfg.veeam)
    if veeam_err:
        log.error("Veeam captura falhou: %s", veeam_err)
    else:
        log.info("Veeam captura OK: %d bytes", len(veeam_img))

    log.info("Capturando PPDM Protection Jobs...")
    ppdm_img, ppdm_err = ppdm_capture.capture(cfg.ppdm)
    if ppdm_err:
        log.error("PPDM captura falhou: %s", ppdm_err)
    else:
        log.info("PPDM captura OK: %d bytes", len(ppdm_img))

    return (veeam_img, veeam_err), (ppdm_img, ppdm_err)


def _capture_tim(cfg, log):
    log.info("Capturando Time Is Money admin-dashboard...")
    tim_img, tim_err = timeismoney_capture.capture(cfg.timeismoney)
    if tim_err:
        log.error("TimeIsMoney captura falhou: %s", tim_err)
    else:
        log.info("TimeIsMoney captura OK: %d bytes", len(tim_img))
    return tim_img, tim_err


def _send_payload(cfg, log, veeam_img, veeam_err, ppdm_img, ppdm_err, tim_img, tim_err):
    log.info("Montando payload e enviando pro Teams...")
    payload = teams_sender.build_payload(
        veeam_image=veeam_img,
        veeam_error=veeam_err,
        ppdm_image=ppdm_img,
        ppdm_error=ppdm_err,
        timeismoney_image=tim_img,
        timeismoney_error=tim_err,
        vm_hostname=socket.gethostname(),
    )
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
    """Modo legado: 1 servidor faz tudo."""
    (veeam_img, veeam_err), (ppdm_img, ppdm_err) = _capture_veeam_and_ppdm(cfg, log)
    tim_img, tim_err = _capture_tim(cfg, log)
    return _send_payload(cfg, log, veeam_img, veeam_err, ppdm_img, ppdm_err, tim_img, tim_err)


def _run_veeam_ppdm_and_send(cfg, log):
    """Modo agregador: V+P aqui, TIM lido do shared_dir."""
    (veeam_img, veeam_err), (ppdm_img, ppdm_err) = _capture_veeam_and_ppdm(cfg, log)

    log.info("Lendo artefato TIM de %s (max_age=%dmin)",
             cfg.mode.shared_artifact_dir, cfg.mode.artifact_max_age_minutes)
    tim_img, tim_err = artifact_store.read_tim(
        cfg.mode.shared_artifact_dir, cfg.mode.artifact_max_age_minutes
    )
    if tim_err:
        log.error("TIM artefato indisponivel: %s", tim_err)
    else:
        log.info("TIM artefato lido OK: %d bytes", len(tim_img))

    return _send_payload(cfg, log, veeam_img, veeam_err, ppdm_img, ppdm_err, tim_img, tim_err)


def _run_timeismoney_only(cfg, log):
    """Modo produtor: so captura TIM e escreve no shared_dir. NAO envia pro Teams."""
    tim_img, tim_err = _capture_tim(cfg, log)
    try:
        artifact_store.write_tim(
            cfg.mode.shared_artifact_dir,
            image=tim_img,
            error=tim_err,
            hostname=socket.gethostname(),
        )
    except OSError as exc:
        log.exception("Falha ao gravar artefato TIM em %s", cfg.mode.shared_artifact_dir)
        log.info("================ FIM (falha de escrita) ================")
        return 1
    log.info("================ FIM (artefato TIM gravado) ================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
