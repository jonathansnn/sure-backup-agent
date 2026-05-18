"""Captura screenshot do dashboard administrativo do Time Is Money.

Fluxo:
  1. Navega para a URL de login
  2. Clica no radio "Usuario Admin" (texto, não input escondido)
  3. Preenche email + senha
  4. Clica Entrar
  5. Aguarda /admin-dashboard carregar
  6. Crop dinâmico: do topo da viewport até o card "Taxa de colaboradores ativos"

Mesma interface dos outros captures (best-effort, retorna tupla).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

from src.config import TimeIsMoneyConfig

logger = logging.getLogger("sure_backup_agent.timeismoney_capture")

# Tempo extra apos navegar pro dashboard pra cards carregarem suas metricas
RENDER_SETTLE_MS = 2500


def capture(cfg: TimeIsMoneyConfig, debug_dir: Optional[Path] = None) -> tuple[Optional[bytes], Optional[str]]:
    """Captura o admin-dashboard do Time Is Money."""
    try:
        with sync_playwright() as p:
            browser_launcher = getattr(p, cfg.browser)
            browser = browser_launcher.launch(headless=cfg.headless)
            context = browser.new_context(
                viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
            )
            page = context.new_page()
            page.set_default_timeout(cfg.login_timeout_seconds * 1000)
            try:
                _login(page, cfg, debug_dir)
                _goto_dashboard(page, cfg, debug_dir)
                page.wait_for_timeout(RENDER_SETTLE_MS)
                png = _screenshot_with_bottom_anchor(page, cfg)
                logger.info("TimeIsMoney captura OK (%d bytes)", len(png))
                return png, None
            finally:
                browser.close()
    except PlaywrightTimeout as exc:
        return None, f"TimeIsMoney timeout: {exc}"
    except Exception as exc:
        logger.exception("TimeIsMoney erro inesperado")
        return None, f"TimeIsMoney {exc.__class__.__name__}: {str(exc)[:200]}"


def _login(page: Page, cfg: TimeIsMoneyConfig, debug_dir: Optional[Path]) -> None:
    logger.info("TimeIsMoney navegando para %s", cfg.url)
    page.goto(cfg.url, wait_until="domcontentloaded")
    _maybe_save_step(page, debug_dir, "01_login_page")

    # Ordem importa: PRIMEIRO fill, DEPOIS click no radio Admin.
    # Clicar no radio dispara um re-bind do Angular Reactive Form que faz fill
    # subsequente no password se perder no race com a deteccao de mudanca.
    # Como os 2 radios partilham o mesmo form (so muda o value submetido),
    # podemos fillar antes — Angular preserva valores ao trocar a selecao.
    page.get_by_placeholder("Email").fill(cfg.username)
    page.get_by_placeholder("Password").fill(cfg.password)
    page.get_by_text("Usuário Admin", exact=True).click()
    _maybe_save_step(page, debug_dir, "02_credentials_filled")

    page.get_by_role("button", name="Entrar", exact=False).click()
    # SPA Angular: domcontentloaded ja disparou ao carregar /login, entao
    # esperar por load state retorna na hora. Precisamos esperar a navegacao
    # interna do router; page.goto explicito perde o estado de sessao do SPA
    # e o guard manda de volta pra /login.
    page.wait_for_url(f"**{cfg.dashboard_url.split('//', 1)[1].split('/', 1)[1]}**",
                      timeout=cfg.login_timeout_seconds * 1000)
    _maybe_save_step(page, debug_dir, "03_logged_in")
    logger.info("TimeIsMoney login OK (URL=%s)", page.url)


def _goto_dashboard(page: Page, cfg: TimeIsMoneyConfig, debug_dir: Optional[Path]) -> None:
    """Aguarda o anchor do dashboard ficar visivel — _login ja garantiu URL correta."""
    page.get_by_text(cfg.bottom_anchor_text, exact=False).first.wait_for(state="visible")
    _maybe_save_step(page, debug_dir, "04_dashboard_loaded")
    logger.info("TimeIsMoney dashboard carregado")


def _screenshot_with_bottom_anchor(page: Page, cfg: TimeIsMoneyConfig) -> bytes:
    """Captura do topo da viewport até a parte de baixo do anchor + padding.

    Se nao achar bbox do anchor (raro), fallback: viewport inteira.
    """
    anchor = page.get_by_text(cfg.bottom_anchor_text, exact=False).first
    box = anchor.bounding_box()
    if not box:
        logger.warning("anchor sem bounding_box, fallback viewport inteira")
        return page.screenshot(full_page=False, type="png")

    bottom = min(int(box["y"] + box["height"]) + cfg.bottom_padding_px, cfg.viewport_height)
    width = cfg.viewport_width
    logger.info("TimeIsMoney crop: 0,0 -> %d,%d (anchor y=%d h=%d)",
                width, bottom, int(box["y"]), int(box["height"]))
    return page.screenshot(
        type="png",
        clip={"x": 0, "y": 0, "width": width, "height": bottom},
    )


def _maybe_save_step(page: Page, debug_dir: Optional[Path], label: str) -> None:
    if debug_dir is None:
        return
    try:
        path = debug_dir / f"tim_{label}.png"
        page.screenshot(path=str(path), full_page=False)
        logger.debug("TimeIsMoney debug screenshot: %s", path)
    except Exception as exc:
        logger.warning("falha ao salvar debug screenshot %s: %s", label, exc)
