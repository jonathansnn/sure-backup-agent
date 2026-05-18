"""Smoke E2E COMPLETO: veeam_capture + ppdm_capture + envio Teams.

Executa exatamente o que o agendamento de produção vai fazer todo dia às 08:00.
Rode na VM-com-Veeam pra validar pipeline inteiro antes do agendamento.

Uso:
  1. Abra Veeam Console manualmente (ou deixe rodar lá)
  2. python -m scripts.full_e2e_to_teams
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, ppdm_capture, teams_sender, veeam_capture


def main() -> int:
    cfg = config.load()
    log = logger.setup(cfg.logging.log_dir, cfg.logging.level, name="sure_backup_agent")
    log.info("=== FULL E2E -> TEAMS iniciado ===")

    # Veeam
    log.info("--- Capturando Veeam ---")
    veeam_img, veeam_err = veeam_capture.capture(cfg.veeam)
    if veeam_err:
        log.error("Veeam falhou: %s", veeam_err)
    else:
        log.info("Veeam OK: %d bytes", len(veeam_img))

    # PPDM
    log.info("--- Capturando PPDM ---")
    ppdm_img, ppdm_err = ppdm_capture.capture(cfg.ppdm)
    if ppdm_err:
        log.error("PPDM falhou: %s", ppdm_err)
    else:
        log.info("PPDM OK: %d bytes", len(ppdm_img))

    # Envio
    log.info("--- Enviando pro Teams ---")
    payload = teams_sender.build_payload(
        veeam_image=veeam_img,
        veeam_error=veeam_err,
        ppdm_image=ppdm_img,
        ppdm_error=ppdm_err,
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
        print(f"\n[OK] Mensagem enviada. Run ID: {result.run_id}")
        return 0
    print(f"\n[FAIL] {result.error_message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
