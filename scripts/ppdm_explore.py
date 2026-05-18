"""Script de exploração/calibração do ppdm_capture.

NÃO é módulo de produção. Roda em modo VISÍVEL (não-headless) com slow-mo,
salva screenshots a cada passo em logs/ppdm_debug/, e segura o navegador
aberto no final pra você inspecionar manualmente.

Uso:
    python -m scripts.ppdm_explore

Se quebrar em algum passo, olhe logs/ppdm_debug/ppdm_FAIL_*.png pra ver
onde o Playwright travou. Compare com os passos anteriores (ppdm_01_*, etc).
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, ppdm_capture


def main() -> int:
    cfg = config.load()  # requer senha PPDM no keyring
    log = logger.setup(cfg.logging.log_dir, "DEBUG", name="sure_backup_agent")
    log.info("=== PPDM EXPLORE iniciado ===")

    # Força modo visível pra debug, mesmo que config.toml diga headless=true
    debug_cfg = replace(cfg.ppdm, headless=False)
    debug_dir = cfg.logging.log_dir / "ppdm_debug"

    log.info("PPDM URL: %s", debug_cfg.url)
    log.info("PPDM user: %s", debug_cfg.username)
    log.info("Headless: %s (forçado pra False neste script)", debug_cfg.headless)
    log.info("Debug dir: %s", debug_dir)

    img, err = ppdm_capture.capture(debug_cfg, debug_dir=debug_dir)

    if img:
        out_path = debug_dir / "ppdm_FINAL.png"
        out_path.write_bytes(img)
        log.info("OK — captura final salva em %s (%d bytes)", out_path, len(img))
        print(f"\n[OK] Captura final: {out_path}")
        return 0

    log.error("FALHOU: %s", err)
    print(f"\n[FAIL] {err}")
    print(f"Olhe os screenshots em {debug_dir} pra ver onde quebrou.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
