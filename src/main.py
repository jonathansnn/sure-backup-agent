"""Entry point de produção do sure-backup-agent.

Chamado pelo Task Scheduler todo dia às 08:00 via run_daily.bat.

Fluxo:
  1. Carrega config + secrets
  2. Captura Veeam (best-effort)
  3. Captura PPDM (best-effort)
  4. Monta payload e envia pro Teams (com retry + fallback em disco se falhar tudo)
  5. Exit code 0 se envio Teams OK; 1 se falhou completamente

Logs vão pra logs/agent.log (rotacionado diariamente, 30 dias de retenção).
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
        # Sem log ainda — config define o log_dir; usar stderr
        print(f"[FATAL] config invalida: {exc}", file=sys.stderr)
        return 2

    log = logger.setup(cfg.logging.log_dir, cfg.logging.level, name="sure_backup_agent")
    log.info("================ INICIO ================")
    log.info("sure-backup-agent run iniciado em %s", socket.gethostname())

    # --- Captura Veeam ---
    log.info("Capturando Veeam Console...")
    veeam_img, veeam_err = veeam_capture.capture(cfg.veeam)
    if veeam_err:
        log.error("Veeam captura falhou: %s", veeam_err)
    else:
        log.info("Veeam captura OK: %d bytes", len(veeam_img))

    # --- Captura PPDM ---
    log.info("Capturando PPDM Protection Jobs...")
    ppdm_img, ppdm_err = ppdm_capture.capture(cfg.ppdm)
    if ppdm_err:
        log.error("PPDM captura falhou: %s", ppdm_err)
    else:
        log.info("PPDM captura OK: %d bytes", len(ppdm_img))

    # --- Captura Time Is Money ---
    log.info("Capturando Time Is Money admin-dashboard...")
    tim_img, tim_err = timeismoney_capture.capture(cfg.timeismoney)
    if tim_err:
        log.error("TimeIsMoney captura falhou: %s", tim_err)
    else:
        log.info("TimeIsMoney captura OK: %d bytes", len(tim_img))

    # --- Envio pro Teams ---
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


if __name__ == "__main__":
    sys.exit(main())
