"""Smoke E2E: ppdm_capture em headless + envio real pro Teams via teams_sender.

Valida a integração PPDM → Teams sem o módulo Veeam (que ainda não existe).
Roda em modo de produção (headless=True conforme config.toml).

Uso:
    python -m scripts.ppdm_headless_to_teams
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, ppdm_capture, teams_sender


def main() -> int:
    cfg = config.load()
    log = logger.setup(cfg.logging.log_dir, cfg.logging.level, name="sure_backup_agent")
    log.info("=== PPDM HEADLESS -> TEAMS iniciado ===")
    log.info("Headless: %s", cfg.ppdm.headless)

    img, err = ppdm_capture.capture(cfg.ppdm)
    if err:
        log.error("PPDM falhou: %s", err)
    else:
        log.info("PPDM OK: %d bytes", len(img))

    payload = teams_sender.build_payload(
        veeam_image=None,
        veeam_error="(módulo veeam_capture ainda não implementado)",
        ppdm_image=img,
        ppdm_error=err,
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
