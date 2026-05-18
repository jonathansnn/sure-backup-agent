"""Smoke test end-to-end do teams_sender.

NÃO é teste pytest — é script manual que dispara chamada REAL pra Power Automate.
Roda contra o canal de teste do Teams. Não rodar contra produção.

Uso:
    python -m tests.smoke
"""
from __future__ import annotations

import io
import socket
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, teams_sender


def make_placeholder_png(label: str, color: tuple[int, int, int]) -> bytes:
    """Gera um PNG 800x300 com o texto centralizado — placeholder visual identificável."""
    img = Image.new("RGB", (800, 300), color=color)
    draw = ImageDraw.Draw(img)
    text = f"SMOKE TEST\n{label}\n{datetime.now().isoformat(timespec='seconds')}"
    # ImageDraw com default font; multiline_text centraliza
    draw.multiline_text(
        (400, 150), text, fill="white", anchor="mm", align="center", spacing=8
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    c = config.load(require_ppdm_password=False)
    log = logger.setup(c.logging.log_dir, c.logging.level, name="sure_backup_agent")
    log.info("=== SMOKE TEST iniciado ===")

    veeam_png = make_placeholder_png("VEEAM placeholder", (40, 90, 160))
    ppdm_png = make_placeholder_png("PPDM placeholder", (160, 60, 60))

    payload = teams_sender.build_payload(
        veeam_image=veeam_png,
        veeam_error=None,
        ppdm_image=ppdm_png,
        ppdm_error=None,
        vm_hostname=socket.gethostname(),
    )

    result = teams_sender.send(
        payload,
        webhook_url=c.teams.webhook_url,
        timeout=c.teams.http_timeout_seconds,
        max_attempts=c.teams.retry_attempts,
        backoff_seconds=c.teams.retry_backoff_seconds,
        fallback_dir=c.logging.log_dir,
    )

    if result.success:
        log.info("SMOKE OK — HTTP %s, run_id=%s, tentativas=%d",
                 result.status_code, result.run_id, result.attempts)
        print(f"\n[OK] Mensagem enviada. Run ID: {result.run_id}")
        print("Vá no canal de teste do Teams pra confirmar que chegou com os 2 placeholders.")
        return 0

    log.error("SMOKE FALHOU — %s (tentativas=%d)", result.error_message, result.attempts)
    print(f"\n[FAIL] {result.error_message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
