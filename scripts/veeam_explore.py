"""Inspeção do Veeam Console — v3 (mss-based, evita introspection WPF).

Estratégia revisada após descobrirmos que pywinauto reporta coords zeradas
para janelas WPF do Veeam Console. Em vez de tentar introspectar a árvore de
controles (que trava ou retorna vazio), usamos pywinauto apenas pra encontrar
e manipular a janela (focar, maximizar) e mss pra capturar a tela.

Uso:
  1. Abra o Veeam Console manualmente, clique Connect, navegue até Home > Jobs
  2. Rode: python -m scripts.veeam_explore
  3. NÃO interaja com mouse/teclado durante a execução (~10s)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger
from pywinauto import Desktop
import mss


def main() -> int:
    cfg = config.load(require_ppdm_password=False)
    log = logger.setup(cfg.logging.log_dir, "DEBUG", name="sure_backup_agent")
    log.info("=== VEEAM EXPLORE (v3 mss-based) iniciado ===")

    debug_dir = cfg.logging.log_dir / "veeam_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    # === Sempre: tirar screenshot fullscreen como baseline ===
    # Esta é a defesa principal: aconteça o que acontecer com pywinauto,
    # o screenshot da tela inteira é guardado e mostra o que o usuário vê.
    log.info("Capturando fullscreen com mss (sempre, como baseline)...")
    try:
        with mss.MSS() as sct:
            monitors = sct.monitors  # [0]=all combined, [1+]=cada monitor
            log.info("Monitores detectados: %d", len(monitors) - 1)
            for i, m in enumerate(monitors):
                log.info("  monitor[%d]: %s", i, m)
            # Captura cada monitor individual (não o "all" combined, que pode ser estranho)
            for i in range(1, len(monitors)):
                shot_path = debug_dir / f"fullscreen_monitor{i}.png"
                img = sct.grab(monitors[i])
                mss.tools.to_png(img.rgb, img.size, output=str(shot_path))
                log.info("  monitor[%d] salvo em %s (%d bytes)", i, shot_path, shot_path.stat().st_size)
    except Exception as exc:
        log.error("mss fullscreen falhou: %s", exc, exc_info=True)

    # === Tentar achar a janela Veeam pra focar e capturar a região dela ===
    log.info("Procurando janela Veeam (UIA)...")
    veeam_window = None
    try:
        veeam_window = Desktop(backend="uia").window(title="Veeam Backup and Replication")
        veeam_window.wait("exists", timeout=5)
        log.info("Janela encontrada (UIA backend)")
    except Exception as exc:
        log.warning("UIA falhou (%s). Tentando win32...", exc)
        try:
            veeam_window = Desktop(backend="win32").window(title="Veeam Backup and Replication")
            veeam_window.wait("exists", timeout=5)
            log.info("Janela encontrada (win32 backend)")
        except Exception as exc2:
            log.error("win32 também falhou: %s", exc2)
            veeam_window = None

    if veeam_window is None:
        log.error("Não foi possível conectar à janela Veeam. Veja se Console está aberto e logado.")
        return 1

    # === Reportar estado da janela ===
    try:
        rect = veeam_window.rectangle()
        log.info("Rectangle reportado: L=%d T=%d R=%d B=%d (W=%d H=%d)",
                 rect.left, rect.top, rect.right, rect.bottom, rect.width(), rect.height())
    except Exception as exc:
        log.warning("rectangle() falhou: %s", exc)
        rect = None

    try:
        is_min = veeam_window.is_minimized()
        is_max = veeam_window.is_maximized()
        is_vis = veeam_window.is_visible()
        log.info("Estado: minimized=%s maximized=%s visible=%s", is_min, is_max, is_vis)
    except Exception as exc:
        log.warning("Não conseguiu ler estado da janela: %s", exc)

    # === Tentar trazer pra frente e maximizar ===
    try:
        log.info("Trazendo janela pra frente (set_focus)...")
        veeam_window.set_focus()
        time.sleep(1)
    except Exception as exc:
        log.warning("set_focus falhou: %s", exc)

    try:
        log.info("Maximizando janela...")
        veeam_window.maximize()
        time.sleep(2)  # aguarda animação
    except Exception as exc:
        log.warning("maximize() falhou: %s", exc)

    # === Captura pós-maximização ===
    log.info("Capturando fullscreen DEPOIS de focar/maximizar...")
    try:
        with mss.MSS() as sct:
            for i in range(1, len(sct.monitors)):
                shot_path = debug_dir / f"after_focus_monitor{i}.png"
                img = sct.grab(sct.monitors[i])
                mss.tools.to_png(img.rgb, img.size, output=str(shot_path))
                log.info("  after_focus monitor[%d] salvo em %s (%d bytes)", i, shot_path, shot_path.stat().st_size)
    except Exception as exc:
        log.error("Captura pós-foco falhou: %s", exc)

    # === Re-checar rectangle após maximize ===
    try:
        rect2 = veeam_window.rectangle()
        log.info("Rectangle pós-maximize: L=%d T=%d R=%d B=%d (W=%d H=%d)",
                 rect2.left, rect2.top, rect2.right, rect2.bottom, rect2.width(), rect2.height())
        # Se rectangle for válido (área > 0), capturar JUST a região da janela
        if rect2.width() > 100 and rect2.height() > 100:
            log.info("Rectangle válido — capturando região específica da janela")
            with mss.MSS() as sct:
                monitor = {
                    "top": max(rect2.top, 0),
                    "left": max(rect2.left, 0),
                    "width": rect2.width(),
                    "height": rect2.height(),
                }
                img = sct.grab(monitor)
                window_path = debug_dir / "veeam_window_region.png"
                mss.tools.to_png(img.rgb, img.size, output=str(window_path))
                log.info("Janela específica salva em %s (%d bytes)", window_path, window_path.stat().st_size)
    except Exception as exc:
        log.warning("Captura por região falhou: %s", exc)

    print("\n" + "=" * 60)
    print("EXPLORAÇÃO COMPLETA (v3)")
    print("=" * 60)
    print(f"Pasta: {debug_dir}")
    print()
    print("Arquivos gerados:")
    for f in sorted(debug_dir.iterdir()):
        if f.is_file():
            print(f"  - {f.name} ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
