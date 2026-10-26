"""Script de exploração/calibração do timeismoney_capture.

NÃO é módulo de produção. Roda em modo VISÍVEL (não-headless), salva os DOIS
prints em logs/tim_debug/ e imprime um diagnóstico do 2º print (o card do
gráfico "Histórico de atividade"), que é a parte que depende da estrutura do
DOM do TIM e por isso não tem teste automatizado.

Uso:
    python -m scripts.tim_explore

O que olhar no resultado:
    tim_FINAL_1_dashboard.png  -> deve estar IDÊNTICO ao print que já chega
                                  hoje no Teams (topo até os 3 tiles coloridos)
    tim_FINAL_2_chart.png      -> deve ser o card "Histórico de atividade"
                                  inteiro: título, gráfico de linhas, seletor
                                  7/30/90 dias e legenda

Se o 2º print não sair, o script mostra o motivo e sugere o ajuste — em geral
é o `chart_anchor_text` no config.toml, se o título do card mudou.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, timeismoney_capture


def main() -> int:
    cfg = config.load()  # requer senha do TIM no keyring
    log = logger.setup(cfg.logging.log_dir, "DEBUG", name="sure_backup_agent")
    log.info("=== TIM EXPLORE iniciado ===")

    # Modo visível pra você acompanhar o scroll até o gráfico acontecendo.
    debug_cfg = replace(cfg.timeismoney, headless=False)
    debug_dir = cfg.logging.log_dir / "tim_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    log.info("URL........: %s", debug_cfg.url)
    log.info("Usuario....: %s", debug_cfg.username)
    log.info("Viewport...: %dx%d", debug_cfg.viewport_width, debug_cfg.viewport_height)
    log.info("Ancora 1o..: %r", debug_cfg.bottom_anchor_text)
    log.info("Ancora 2o..: %r", debug_cfg.chart_anchor_text)

    result = timeismoney_capture.capture(debug_cfg, debug_dir=debug_dir)

    if result.error:
        log.error("FALHOU (captura inteira): %s", result.error)
        print(f"\n[FAIL] {result.error}")
        print(f"Screenshots dos passos em {debug_dir}")
        return 1

    p1 = debug_dir / "tim_FINAL_1_dashboard.png"
    p1.write_bytes(result.dashboard)
    print(f"\n[OK] 1o print (dashboard): {p1} ({len(result.dashboard)} bytes)")
    print("     -> compare com o print que chega hoje no Teams: deve ser igual.")

    if result.chart:
        p2 = debug_dir / "tim_FINAL_2_chart.png"
        p2.write_bytes(result.chart)
        print(f"[OK] 2o print (grafico):  {p2} ({len(result.chart)} bytes)")
        print("     -> deve conter o card 'Historico de atividade' inteiro.")
        return 0

    # 1o print OK, 2o falhou: exatamente o cenario que em producao passa
    # despercebido (a politica escolhida omite o 2o em silencio no Teams).
    print(f"[FAIL] 2o print nao saiu: {result.chart_error}")
    print("\nComo ajustar:")
    print(f"  - O titulo do card ainda e exatamente {debug_cfg.chart_anchor_text!r}?")
    print("    Se mudou, corrija [timeismoney].chart_anchor_text no config.toml.")
    print("  - Se o titulo esta certo, o card pode nao conter <canvas>/<svg>.")
    print("    Me mande esta saida que eu ajusto o seletor em _screenshot_chart_card.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
