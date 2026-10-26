"""Testes do timeismoney_capture que rodam contra HTML local.

Nao acessam a rede nem o TIM real: montam uma pagina estatica com a mesma
armadilha que o dashboard de verdade tem e verificam qual elemento o codigo
escolhe pra capturar. Precisam do Chromium do Playwright instalado; sem ele,
os testes sao pulados (nao falham).
"""
from __future__ import annotations

from dataclasses import replace

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


# --- Pagina de campanha entre o login e o dashboard -------------------------
#
# O TIM passou a mostrar a campanha Outubro Rosa logo apos o "Entrar" (2026-10).
# Ela so aparece "na 1a vez" (flag guardada no browser); como o Playwright abre
# contexto limpo a cada execucao, pro agente eh SEMPRE a 1a vez. Sem tratar, a
# espera pela ancora do dashboard estourava e o TIM inteiro caia em timeout.
#
# Em producao a URL chega em /admin-dashboard e SO DEPOIS a campanha toma a
# tela — checar a URL logo apos o login nao enxerga ela. O fake "atrasada"
# reproduz exatamente isso.

ANCORA = "Distribuição temporal detalhada"

LOGIN_HTML = """
<html><body>
  <span>Usuário Empresa</span><span>Usuário Admin</span>
  <input placeholder="Email"><input placeholder="Password">
  <button onclick="location.href='{destino}'">Entrar</button>
</body></html>
"""

CAMPANHA_HTML = """
<html><body>
  <h1>Conhecer o próprio corpo é o primeiro cuidado.</h1>
  <a href="/admin-dashboard">Entrar no TIM &rarr;</a>
</body></html>
"""

# /admin-dashboard que abre em branco (dashboard ainda nao renderizado) e,
# 300ms depois, mostra a campanha; o dashboard so aparece apos clicar no CTA.
# Igual ao visto em producao: a ancora NUNCA fica visivel antes da campanha.
DASHBOARD_COM_CAMPANHA_ATRASADA_HTML = """
<html><body>
  <div id="campanha" style="display:none">
    <h1>Outubro Rosa</h1>
    <button onclick="campanha.remove(); dash.style.display='block'">Entrar no TIM</button>
  </div>
  <div id="dash" style="display:none">""" + DASHBOARD_HTML + """</div>
  <script>
    setTimeout(() => { campanha.style.display = 'block'; }, 300);
  </script>
</body></html>
"""


def _fake_tim(page, apos_login: str) -> None:
    """Serve um TIM falso em https://tim.test, sem rede."""
    paginas = {
        "/login": LOGIN_HTML.format(destino=apos_login),
        "/campanha/outubro-rosa": CAMPANHA_HTML,
        "/admin-dashboard": DASHBOARD_HTML,
        "/admin-dashboard-atrasada": DASHBOARD_COM_CAMPANHA_ATRASADA_HTML,
    }

    def handler(route):
        path = "/" + route.request.url.split("//", 1)[1].split("/", 1)[1]
        route.fulfill(status=200, content_type="text/html; charset=utf-8",
                      body=paginas[path])

    page.route("https://tim.test/**", handler)


@pytest.fixture
def blank_page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def _entrar(page, dashboard_url: str = "https://tim.test/admin-dashboard") -> None:
    cfg = replace(_cfg(), bottom_anchor_text=ANCORA, dashboard_url=dashboard_url)
    timeismoney_capture._login(page, cfg, debug_dir=None)
    timeismoney_capture._goto_dashboard(page, cfg, debug_dir=None)


def test_campaign_shown_late_on_dashboard_url_is_skipped(blank_page):
    """Cenario de producao (2026-10-26): URL ja eh /admin-dashboard e a
    campanha aparece depois. Antes, timeout esperando a ancora."""
    _fake_tim(blank_page, apos_login="/admin-dashboard-atrasada")
    _entrar(blank_page, dashboard_url="https://tim.test/admin-dashboard-atrasada")
    assert blank_page.get_by_text(ANCORA).is_visible()
    assert blank_page.get_by_text("Entrar no TIM").count() == 0


def test_campaign_as_its_own_route_is_skipped(blank_page):
    _fake_tim(blank_page, apos_login="/campanha/outubro-rosa")
    _entrar(blank_page)
    assert blank_page.url == "https://tim.test/admin-dashboard"
    assert blank_page.get_by_text(ANCORA).is_visible()


def test_without_campaign_goes_straight_to_dashboard(blank_page):
    """Quando a campanha acabar, o fluxo antigo tem que continuar funcionando."""
    _fake_tim(blank_page, apos_login="/admin-dashboard")
    _entrar(blank_page)
    assert blank_page.get_by_text(ANCORA).is_visible()
