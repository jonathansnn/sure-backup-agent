"""Captura screenshot da tela do Veeam Backup & Replication Console.

Estratégia (descoberta após exploração em scripts/veeam_explore.py):
  - pywinauto.capture_as_image() FALHA com Veeam Console (WPF) — retorna None
  - print_control_identifiers TRAVA ou retorna árvore vazia
  - SOLUÇÃO: usar pywinauto SÓ pra encontrar/manipular janela (focar, maximizar),
    e mss pra captura efetiva dos pixels via Win32 BitBlt
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

import mss
import mss.tools
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.timings import TimeoutError as PywinTimeoutError

from src.config import VeeamConfig
from src.image_utils import auto_trim_bottom

logger = logging.getLogger("sure_backup_agent.veeam_capture")

# Tempos de estabilização (em segundos)
SETTLE_AFTER_FOCUS = 0.5
SETTLE_AFTER_MAXIMIZE = 2.0
WAIT_AFTER_CONNECT = 5.0
CONNECT_DIALOG_PROBE_TIMEOUT = 3.0
EXISTING_WINDOW_PROBE_TIMEOUT = 3.0


def capture(cfg: VeeamConfig) -> tuple[Optional[bytes], Optional[str]]:
    """Captura a janela do Veeam Console (Home > Jobs ou view atual).

    Best-effort: nunca propaga exceção.

    Returns:
        (png_bytes, None) em sucesso.
        (None, "mensagem amigável") em falha.
    """
    try:
        window = _ensure_veeam_open(cfg)
        if window is None:
            return None, "Veeam: nao foi possivel abrir ou conectar a janela"

        _bring_to_foreground(window)

        rect = window.rectangle()
        if rect.width() < 100 or rect.height() < 100:
            return None, (
                f"Veeam: janela com tamanho invalido apos maximize "
                f"({rect.width()}x{rect.height()})"
            )

        png = _capture_window_region(rect, cfg)
        raw_size = len(png)
        png = auto_trim_bottom(png, padding=20)
        logger.info(
            "Veeam captura OK (%d bytes apos auto-trim, %d bytes raw, janela %dx%d, crop top=%d left=%d right=%d bottom=%d)",
            len(png), raw_size, rect.width(), rect.height(),
            cfg.crop_top, cfg.crop_left, cfg.crop_right, cfg.crop_bottom,
        )
        return png, None
    except Exception as exc:
        msg = f"Veeam erro inesperado: {exc.__class__.__name__}: {str(exc)[:200]}"
        logger.exception(msg)
        return None, msg


def _ensure_veeam_open(cfg: VeeamConfig):
    """Garante que Veeam Console esteja aberto com janela principal pronta.

    1. Tenta achar janela existente
    2. Se nao achar, lanca o executavel
    3. Aguarda janela aparecer (pode ser Connect dialog primeiro)
    4. Se Connect dialog detectado, clica Connect
    5. Retorna a janela principal pronta pra uso
    """
    # Passo 1: tentar achar janela já aberta
    try:
        win = Desktop(backend="uia").window(title="Veeam Backup and Replication")
        win.wait("exists", timeout=EXISTING_WINDOW_PROBE_TIMEOUT)
        logger.info("Veeam Console ja estava aberto")
        _try_dismiss_connect_dialog(win)  # caso esteja no diálogo Connect ainda
        return win
    except (ElementNotFoundError, PywinTimeoutError):
        logger.info("Veeam nao estava aberto — lancando...")

    # Passo 2: lançar
    console_path = Path(cfg.console_path)
    if not console_path.exists():
        logger.error("Veeam executavel nao encontrado em %s", console_path)
        return None
    subprocess.Popen([str(console_path)])

    # Passo 3: aguardar janela (pode ser Connect dialog ou main)
    try:
        win = Desktop(backend="uia").window(title="Veeam Backup and Replication")
        win.wait("exists", timeout=cfg.launch_timeout_seconds)
        logger.info("Janela Veeam apareceu")
    except (ElementNotFoundError, PywinTimeoutError) as exc:
        logger.error("Timeout esperando janela Veeam (%ds): %s",
                     cfg.launch_timeout_seconds, exc)
        return None

    # Passo 4: dismissar Connect dialog se existir
    _try_dismiss_connect_dialog(win)

    return win


def _try_dismiss_connect_dialog(win) -> None:
    """Se o Veeam estiver mostrando o diálogo de Connect, clica em Connect.

    Best-effort: se não houver dialog, não faz nada (silencioso).
    """
    try:
        # Procurar botão "Connect" como filho da janela. Em Portugues seria "Conectar"
        # mas Veeam normalmente mantém UI em inglês mesmo em Windows pt-BR.
        connect_btn = win.child_window(title="Connect", control_type="Button")
        if connect_btn.exists(timeout=CONNECT_DIALOG_PROBE_TIMEOUT):
            logger.info("Connect dialog detectado — clicando Connect")
            connect_btn.click_input()
            time.sleep(WAIT_AFTER_CONNECT)
            logger.info("Connect clicado; aguardando UI principal estabilizar")
    except Exception as exc:
        # Sem dialog ou erro tentando — log debug e segue (janela principal já está OK)
        logger.debug("Sem Connect dialog ou nao foi possivel clicar: %s", exc)


def _bring_to_foreground(window) -> None:
    """Traz janela pra frente e maximiza. Best-effort em cada passo."""
    try:
        window.set_focus()
        time.sleep(SETTLE_AFTER_FOCUS)
    except Exception as exc:
        logger.warning("set_focus falhou: %s", exc)

    try:
        if not window.is_maximized():
            window.maximize()
            time.sleep(SETTLE_AFTER_MAXIMIZE)
    except Exception as exc:
        logger.warning("maximize falhou (%s) — tentando capturar mesmo assim", exc)


def _capture_window_region(rect, cfg: VeeamConfig) -> bytes:
    """Captura pixels da região da janela (com crop configurável) via mss + Win32 BitBlt.

    Mais robusto que pywinauto.capture_as_image() pra apps WPF (Veeam Console).

    Crop: aplica offsets de cfg.crop_top/left/right/bottom relativo ao rectangle
    da janela. Defaults removem título+ribbon (topo), tree pane (esquerda) e
    status bar (fundo), deixando só a área de dados (lista de jobs).
    """
    with mss.MSS() as sct:
        # Negative coords (-8,-8) sao normais em janelas maximizadas (invisible borders)
        # — clipamos pra 0 pra nao tentar capturar pixels fora do monitor.
        base_top = max(rect.top, 0)
        base_left = max(rect.left, 0)
        # Aplicar crop sobre a janela visível
        top = base_top + cfg.crop_top
        left = base_left + cfg.crop_left
        width = max(rect.width() - cfg.crop_left - cfg.crop_right, 100)
        height = max(rect.height() - cfg.crop_top - cfg.crop_bottom, 100)
        monitor = {"top": top, "left": left, "width": width, "height": height}
        img = sct.grab(monitor)
        return mss.tools.to_png(img.rgb, img.size)
