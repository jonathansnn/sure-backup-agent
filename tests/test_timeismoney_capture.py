"""Testes do timeismoney_capture que rodam contra HTML local.

Nao acessam a rede nem o TIM real: montam uma pagina estatica com a mesma
armadilha que o dashboard de verdade tem e verificam qual elemento o codigo
escolhe pra capturar. Precisam do Chromium do Playwright instalado; sem ele,
os testes sao pulados (nao falham).
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from src import timeismoney_capture
from src.config import TimeIsMoneyConfig


# Reproduz a estrutura do /admin-dashboard nas duas faixas que colidem:
#   - a faixa de tiles finos, onde existe "COM HISTORICO DE ATIVIDADE" (decoy,
#     e vem ANTES no DOM), dentro de um container grande que TAMBEM contem o
#     grafico de barras;
#   - o card do "Historico de atividade", que e o alvo de verdade.
DASHBOARD_HTML = """
<html><body>
  <div id="wrapper-grande">
    <div id="card-distribuicao">
      <h3>Distribuição temporal detalhada</h3>
      <svg width="400" height="120"><rect width="100" height="20"></rect></svg>
    </div>
    <div id="tiles">
      <div id="tile-adesao"><span>ADESÃO HOJE</span><b>50.6%</b></div>
      <div id="tile-decoy"><span>COM HISTÓRICO DE ATIVIDADE</span><b>9.671</b></div>
    </div>
  </div>
  <div id="card-historico">
    <h3>Histórico de atividade</h3>
    <p>Evolução de empresas e colaboradores ativos ao longo do tempo</p>
    <svg width="400" height="120"><polyline points="0,0 10,10"></polyline></svg>
  </div>
</body></html>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser nao instalado nesta maquina
            pytest.skip(f"Chromium do Playwright indisponivel: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    pg.set_content(DASHBOARD_HTML)
    yield pg
    pg.close()


def _cfg(anchor: str = "Histórico de atividade") -> TimeIsMoneyConfig:
    return TimeIsMoneyConfig(
        url="https://tim.test/login",
        dashboard_url="https://tim.test/admin-dashboard",
        username="u", password="p",
        login_timeout_seconds=5,
        browser="chromium", headless=True,
        viewport_width=1600, viewport_height=1100,
        bottom_anchor_text="irrelevante aqui",
        bottom_padding_px=120,
        chart_anchor_text=anchor,
    )


def test_chart_card_locator_ignores_uppercase_substring_tile(page):
    """Regressao: "COM HISTORICO DE ATIVIDADE" nao pode roubar a ancora.

    Casar por substring sem diferenciar maiuscula fazia o tile da faixa de
    cima vencer (vem antes no DOM) e a subida por ancestral terminava no
    container grande, capturando meia pagina em vez do card do grafico.
    """
    card = timeismoney_capture._chart_card_locator(page, _cfg())
    assert card.get_attribute("id") == "card-historico"


def test_chart_card_locator_resolves_card_not_the_title_element(page):
    """O alvo e o card inteiro (titulo + grafico), nao so o <h3> do titulo."""
    card = timeismoney_capture._chart_card_locator(page, _cfg())
    assert card.locator("svg").count() == 1
    assert "Evolução de empresas" in card.inner_text()

