"""Captura screenshots do dashboard administrativo do Time Is Money.

Fluxo:
  1. Navega para a URL de login
  2. Clica no radio "Usuario Admin" (texto, não input escondido)
  3. Preenche email + senha
  4. Clica Entrar
  5. Aguarda /admin-dashboard carregar
  6. 1º print — crop dinâmico: do topo da viewport até o card "Taxa de
     colaboradores ativos"
  7. 2º print — o card "Histórico de atividade" (gráfico de linhas), que fica
     bem abaixo da dobra

Mesma interface best-effort dos outros captures, mas retorna um
`TimCaptureResult` em vez de tupla — são 2 imagens e 2 erros independentes.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

from src.config import TimeIsMoneyConfig

logger = logging.getLogger("sure_backup_agent.timeismoney_capture")

# Tempo extra apos navegar pro dashboard pra cards carregarem suas metricas
RENDER_SETTLE_MS = 2500

# Tempo extra apos rolar ate o grafico — ele so anima quando entra na viewport.
CHART_SETTLE_MS = 1500


@dataclass(frozen=True)
class TimCaptureResult:
    """Resultado da captura do TIM: 2 prints com destinos de falha diferentes.

    `error` eh fatal (nao ha o que enviar). `chart_error` NAO eh: por decisao
    de produto, se so o 2o print falhar o agente envia o 1o assim mesmo e
    omite o 2o. Por isso os dois erros sao campos separados.
    """
    dashboard: Optional[bytes]
    chart: Optional[bytes]
    error: Optional[str]
    chart_error: Optional[str]


def capture(cfg: TimeIsMoneyConfig, debug_dir: Optional[Path] = None) -> TimCaptureResult:
    """Captura o admin-dashboard do Time Is Money (2 prints)."""
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
                # Ordem importa: o 1o print sai ANTES de qualquer scroll, pra
                # ficar byte-a-byte equivalente ao que era antes desta feature.
                chart_png, chart_err = _try_screenshot_chart_card(page, cfg, debug_dir)
                return TimCaptureResult(png, chart_png, None, chart_err)
            finally:
                browser.close()
    except PlaywrightTimeout as exc:
        return TimCaptureResult(None, None, f"TimeIsMoney timeout: {exc}", None)
    except Exception as exc:
        logger.exception("TimeIsMoney erro inesperado")
        return TimCaptureResult(
            None, None, f"TimeIsMoney {exc.__class__.__name__}: {str(exc)[:200]}", None
        )


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


def _try_screenshot_chart_card(
    page: Page, cfg: TimeIsMoneyConfig, debug_dir: Optional[Path]
) -> tuple[Optional[bytes], Optional[str]]:
    """Captura o card "Historico de atividade" — nunca propaga excecao.

    Politica de falha (decisao de produto): se o 2o print quebrar, o 1o segue
    normalmente pro Teams e este volta (None, motivo). A ausencia eh SILENCIOSA
    na mensagem, entao este log em WARNING eh o principal alerta que sobra.
    """
    try:
        png = _screenshot_chart_card(page, cfg)
        logger.info("TimeIsMoney 2o print OK (%d bytes)", len(png))
        _maybe_save_step(page, debug_dir, "05_chart_card")
        return png, None
    except PlaywrightTimeout as exc:
        msg = f"card '{cfg.chart_anchor_text}' nao encontrado: {str(exc)[:150]}"
        logger.warning("TimeIsMoney 2o print FALHOU (1o print segue): %s", msg)
        _maybe_save_step(page, debug_dir, "05_chart_FAIL")
        return None, msg
    except Exception as exc:
        msg = f"{exc.__class__.__name__}: {str(exc)[:150]}"
        logger.warning("TimeIsMoney 2o print FALHOU (1o print segue): %s", msg)
        return None, msg


# Ancestral mais proximo que ja contem um grafico. `local-name()` em vez de
# `.//canvas or .//svg`: <svg> vive no namespace SVG e XPath 1.0 resolve nome
# sem prefixo contra o namespace HTML — `.//svg` nunca casa, silenciosamente.
_XPATH_CARD_COM_GRAFICO = (
    "xpath=ancestor::*[.//*[local-name()='canvas' or local-name()='svg']][1]"
)


def _chart_card_locator(page: Page, cfg: TimeIsMoneyConfig):
    """Resolve o card do grafico a partir do titulo dele.

    `exact=True` NAO eh detalhe: com substring (o default) o texto
    "Historico de atividade" casa tambem com o tile "COM HISTORICO DE
    ATIVIDADE" da faixa de cima — que vem antes no DOM, vence o `.first`, e
    faz a subida por ancestral terminar num container gigante. O resultado eh
    um print de meia pagina, sem erro nenhum. Ja aconteceu (2026-08-19).

    `exact=True` ainda normaliza espacos em branco, entao nao quebra com
    indentacao ou quebra de linha no HTML — so exige o texto igual.
    """
    title = page.get_by_text(cfg.chart_anchor_text, exact=True).first
    title.wait_for(state="visible")
    return title.locator(_XPATH_CARD_COM_GRAFICO)


def _screenshot_chart_card(page: Page, cfg: TimeIsMoneyConfig) -> bytes:
    """Screenshot do card inteiro do grafico "Historico de atividade".

    Usa `locator.screenshot()` — NAO `page.screenshot(clip=...)` — de proposito:

      - o card fica ~1500px abaixo do topo, fora dos 1100px de viewport. O
        `clip` do Playwright eh clampado pela viewport, entao recorte por
        coordenada devolveria imagem cortada SEM erro nenhum;
      - `locator.screenshot()` rola o elemento pra dentro da viewport sozinho e
        recorta exatamente a caixa dele, sem aritmetica de bounding box;
      - assim `viewport_height` continua 1100 e o 1o print sai identico ao de
        antes desta feature (requisito explicito).

    Quem resolve QUAL elemento capturar eh `_chart_card_locator` — inclusive
    as duas armadilhas de seletor documentadas la.
    """
    card = _chart_card_locator(page, cfg)
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(CHART_SETTLE_MS)  # o grafico so anima ao entrar na viewport
    return card.screenshot(type="png")


def _maybe_save_step(page: Page, debug_dir: Optional[Path], label: str) -> None:
    if debug_dir is None:
        return
    try:
        path = debug_dir / f"tim_{label}.png"
        page.screenshot(path=str(path), full_page=False)
        logger.debug("TimeIsMoney debug screenshot: %s", path)
    except Exception as exc:
        logger.warning("falha ao salvar debug screenshot %s: %s", label, exc)
