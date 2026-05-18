"""Captura screenshot da tela Jobs > Protection Jobs do PPDM (filtro 24h aplicado).

Best-effort: nunca propaga exceção. Sempre retorna (bytes|None, error|None).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Browser,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from src.config import PpdmConfig

logger = logging.getLogger("sure_backup_agent.ppdm_capture")

# Tempo extra após o último clique para gráficos/tabelas renderizarem antes do print.
RENDER_SETTLE_MS = 2500

# Rota direta para a tela de Protection Jobs (evita navegação por menu).
PROTECTION_JOBS_PATH = "#/mgmt/auth2/jobs/protection"


def capture(cfg: PpdmConfig, debug_dir: Optional[Path] = None) -> tuple[Optional[bytes], Optional[str]]:
    """Captura a tela de Protection Jobs filtrada por 24h.

    Args:
        cfg: configuração do PPDM (URL, usuário, senha, viewport, etc).
        debug_dir: se fornecido, salva screenshots a cada passo (útil em desenvolvimento).

    Returns:
        (png_bytes, None) em sucesso.
        (None, "mensagem amigável") em falha.
    """
    try:
        with sync_playwright() as pw:
            return _capture_with_playwright(pw, cfg, debug_dir)
    except Exception as exc:
        msg = f"PPDM erro inesperado: {exc.__class__.__name__}: {exc}"
        logger.exception(msg)
        return None, msg


def _capture_with_playwright(
    pw: Playwright, cfg: PpdmConfig, debug_dir: Optional[Path]
) -> tuple[Optional[bytes], Optional[str]]:
    browser: Browser = pw.chromium.launch(headless=cfg.headless)
    try:
        context = browser.new_context(
            viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
            ignore_https_errors=cfg.ignore_https_errors,
        )
        context.set_default_timeout(cfg.login_timeout_seconds * 1000)
        page = context.new_page()
        try:
            _login(page, cfg, debug_dir)
            _dismiss_post_login_modals(page, debug_dir)
            _goto_protection_jobs(page, cfg, debug_dir)
            _apply_24h_filter(page, debug_dir)
            page.wait_for_timeout(RENDER_SETTLE_MS)
            png = _screenshot_content_area(page, cfg)
            logger.info("PPDM captura OK (%d bytes)", len(png))
            return png, None
        except PlaywrightTimeout as exc:
            _dump_failure(page, debug_dir, "timeout")
            return None, f"PPDM timeout: {exc.message[:200]}"
        except Exception as exc:
            _dump_failure(page, debug_dir, "error")
            return None, f"PPDM {exc.__class__.__name__}: {str(exc)[:200]}"
    finally:
        browser.close()


def _login(page: Page, cfg: PpdmConfig, debug_dir: Optional[Path]) -> None:
    """Faz login com usuário/senha no formulário clássico do PPDM."""
    logger.info("PPDM navegando para %s", cfg.url)
    page.goto(cfg.url, wait_until="domcontentloaded")
    _maybe_save_step(page, debug_dir, "01_login_page")

    # Padrões de seletor em ordem de robustez. Playwright tenta cada um até funcionar.
    # PPDM 19.x usa Angular Material — get_by_label costuma funcionar.
    username_input = page.get_by_label("Username", exact=False).or_(
        page.get_by_placeholder("Username", exact=False)
    ).or_(page.locator("input[name='username']"))

    password_input = page.get_by_label("Password", exact=False).or_(
        page.get_by_placeholder("Password", exact=False)
    ).or_(page.locator("input[type='password']"))

    login_button = page.get_by_role("button", name="Log In").or_(
        page.get_by_role("button", name="Login")
    ).or_(page.get_by_role("button", name="Sign In"))

    username_input.first.fill(cfg.username)
    password_input.first.fill(cfg.password)
    _maybe_save_step(page, debug_dir, "02_credentials_filled")

    login_button.first.click()
    # Espera sair da página de login (URL muda ou aparece algum elemento do dashboard)
    page.wait_for_url(lambda url: "login" not in url.lower(), timeout=cfg.login_timeout_seconds * 1000)
    _maybe_save_step(page, debug_dir, "03_logged_in")
    logger.info("PPDM login OK")


def _dismiss_post_login_modals(page: Page, debug_dir: Optional[Path]) -> None:
    """Fecha modais pop-up que aparecem após login.

    Atualmente trata:
      - 'What's New in the PowerProtect Data Manager Appliance' (bug conhecido — reaparece
        toda sessão mesmo com 'Don't show again' marcado).

    Best-effort: não falha se modal não estiver presente.
    """
    try:
        # Espera curta — se modal não aparecer rápido, presume que não está lá.
        whats_new = page.get_by_role("dialog").filter(has_text="What's New")
        whats_new.wait_for(state="visible", timeout=5000)
        # Botão alvo: id="whats-new-close" (data-cy="whats-new-close-btn"). Existem 2
        # botões "Close" no modal — o do header (X) e o do footer azul. O id é estável.
        page.locator("#whats-new-close").click()
        whats_new.wait_for(state="hidden", timeout=5000)
        _maybe_save_step(page, debug_dir, "03b_whats_new_dismissed")
        logger.info("PPDM modal 'What's New' fechado")
    except PlaywrightTimeout:
        logger.debug("PPDM modal 'What's New' não apareceu (ou já estava fechado)")
    except Exception as exc:
        # Não escalar — se a UI mudar, capture continua. Só registra.
        logger.warning("PPDM falha ao tentar fechar modal What's New: %s", exc)


def _goto_protection_jobs(page: Page, cfg: PpdmConfig, debug_dir: Optional[Path]) -> None:
    """Vai direto para a tela de Protection Jobs via URL (evita menu lateral)."""
    target = cfg.url.rstrip("/") + "/" + PROTECTION_JOBS_PATH
    logger.info("PPDM navegando direto para %s", target)
    page.goto(target, wait_until="domcontentloaded")
    # Hash routing em SPA não dispara load completo — ancorar num elemento da tela.
    page.get_by_role("heading", name="Protection Jobs", exact=True).wait_for(state="visible")
    _maybe_save_step(page, debug_dir, "04_protection_jobs_loaded")
    logger.info("PPDM Protection Jobs carregada")


def _apply_24h_filter(page: Page, debug_dir: Optional[Path]) -> None:
    """Abre o filtro 'Start Time' (botão 'All' ao lado) e seleciona a 2ª opção (Last 24h)."""
    # O label "Start Time:" e o botão "All" são irmãos no DOM. Ancoramos no label
    # e pegamos o primeiro botão dessa região.
    start_time_label = page.get_by_text("Start Time:", exact=False).first
    start_time_label.wait_for(state="visible")
    # O botão fica como ancestral comum + sibling. Usar XPath é mais direto aqui.
    start_time_button = page.locator(
        "xpath=//*[contains(normalize-space(.), 'Start Time:')]/following-sibling::*//button"
    ).first
    if not start_time_button.is_visible():
        # Fallback: qualquer botão com texto "All" próximo do label
        start_time_button = page.get_by_role("button", name="All").first
    start_time_button.click()
    _maybe_save_step(page, debug_dir, "05_start_time_open")

    # Em Angular Material o <input type="radio"> é visualmente escondido — clicar nele
    # falha por "not visible". Clicar no TEXTO (label associado) dispara o mesmo evento
    # e é o que o usuário humano faz. Texto "Last 24 hours" é único na página.
    page.get_by_text("Last 24 hours", exact=True).click()
    _maybe_save_step(page, debug_dir, "06_filter_24h_selected")

    # Fechar o popup do filtro clicando fora (em cima do título) — alguns dropdowns
    # PPDM ficam abertos sobrepondo a tabela, prejudicando o screenshot.
    page.get_by_role("heading", name="Protection Jobs", exact=True).click()
    page.wait_for_load_state("networkidle")
    _maybe_save_step(page, debug_dir, "07_after_filter")
    logger.info("PPDM filtro 'Last 24 hours' aplicado")


def _screenshot_content_area(page: Page, cfg: PpdmConfig) -> bytes:
    """Cropa screenshot pro retângulo de conteúdo (sem header azul, sem menu lateral).

    Estratégia: usa o título 'Protection Jobs' como âncora — seu x indica onde
    o menu lateral termina; seu y indica onde o header azul termina. Captura
    desse ponto até as bordas da viewport, com pequena margem.
    """
    heading_box = page.get_by_role("heading", name="Protection Jobs", exact=True).bounding_box()
    if not heading_box:
        # Fallback: viewport inteira se algo der errado com o bounding box.
        return page.screenshot(full_page=False, type="png")

    left = max(int(heading_box["x"]) - 20, 0)
    top = max(int(heading_box["y"]) - 20, 0)
    width = max(cfg.viewport_width - left - 10, 100)
    height = max(cfg.viewport_height - top - 10, 100)

    return page.screenshot(
        type="png",
        clip={"x": left, "y": top, "width": width, "height": height},
    )


def _maybe_save_step(page: Page, debug_dir: Optional[Path], label: str) -> None:
    """Salva screenshot intermediário se debug_dir foi fornecido."""
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / f"ppdm_{label}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        logger.debug("PPDM debug screenshot: %s", path)
    except Exception:
        pass  # debug não deve quebrar produção


def _dump_failure(page: Page, debug_dir: Optional[Path], reason: str) -> None:
    """Em caso de falha, salva screenshot + URL atual em arquivo de diagnóstico."""
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(debug_dir / f"ppdm_FAIL_{reason}.png"), full_page=True)
        (debug_dir / f"ppdm_FAIL_{reason}.txt").write_text(
            f"URL no momento da falha: {page.url}\nTítulo: {page.title()}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
