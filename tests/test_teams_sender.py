"""Testes unitários do teams_sender — sem chamadas HTTP reais."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

from src import teams_sender


# ---------- build_payload ----------

def test_build_payload_all_present():
    veeam_png = b"\x89PNG_veeam_fake"
    ppdm_png = b"\x89PNG_ppdm_fake"
    tim_png = b"\x89PNG_tim_fake"
    ts = datetime(2026, 5, 16, 8, 0, 0, tzinfo=timezone.utc)
    payload = teams_sender.build_payload(
        veeam_image=veeam_png,
        veeam_error=None,
        ppdm_image=ppdm_png,
        ppdm_error=None,
        timeismoney_image=tim_png,
        timeismoney_error=None,
        vm_hostname="SRVTIM",
        timestamp=ts,
    )
    assert payload["veeam_image_b64"] == base64.b64encode(veeam_png).decode("ascii")
    assert payload["ppdm_image_b64"] == base64.b64encode(ppdm_png).decode("ascii")
    assert payload["timeismoney_image_b64"] == base64.b64encode(tim_png).decode("ascii")
    assert payload["veeam_error"] == ""
    assert payload["ppdm_error"] == ""
    assert payload["timeismoney_error"] == ""
    assert payload["vm_hostname"] == "SRVTIM"
    # UTC bare format (sem offset) — exigência do PA convertFromUtc
    assert payload["timestamp"] == "2026-05-16T08:00:00"


def test_build_payload_converts_local_tz_to_utc_bare():
    """Timestamp com fuso local deve ser normalizado pra UTC bare."""
    # 00:30 em Brasilia (GMT-3) = 03:30 UTC
    brasilia = timezone(timedelta(hours=-3))
    local_ts = datetime(2026, 5, 18, 0, 30, 0, tzinfo=brasilia)
    payload = teams_sender.build_payload(
        veeam_image=b"x", veeam_error=None,
        ppdm_image=b"y", ppdm_error=None,
        timeismoney_image=b"z", timeismoney_error=None,
        vm_hostname="X", timestamp=local_ts,
    )
    assert payload["timestamp"] == "2026-05-18T03:30:00"  # convertido pra UTC, sem offset


def test_build_payload_both_failed_renders_error_pngs():
    """Quando ambas falham, payload mesmo assim contém PNGs (gerados com mensagem de erro).

    Isso é exigência do hostedContents do Teams (rejeita contentBytes vazio).
    O sinal canônico de falha continua sendo `*_error` não-vazio.
    """
    payload = teams_sender.build_payload(
        veeam_image=None,
        veeam_error="janela do Veeam não encontrada",
        ppdm_image=None,
        ppdm_error="timeout no login PPDM",
        timeismoney_image=None,
        timeismoney_error="TimeIsMoney 503 Service Unavailable",
        vm_hostname="SRVTIM",
    )
    # Imagens geradas — base64 não-vazio, com header de PNG
    for key in ("veeam_image_b64", "ppdm_image_b64", "timeismoney_image_b64"):
        assert payload[key] != "", f"{key} ficou vazio"
        assert base64.b64decode(payload[key]).startswith(b"\x89PNG"), f"{key} nao tem header PNG"
    # Mensagens de erro preservadas — esse é o sinal canônico
    assert payload["veeam_error"] == "janela do Veeam não encontrada"
    assert payload["ppdm_error"] == "timeout no login PPDM"
    assert payload["timeismoney_error"] == "TimeIsMoney 503 Service Unavailable"


def test_build_payload_partial_failure_renders_error_only_for_failed():
    """Veeam OK + PPDM falhou: PPDM vem com PNG de erro, Veeam com PNG real."""
    real_veeam_png = b"\x89PNG\r\n\x1a\nFAKE_VEEAM_BYTES"
    real_tim_png = b"\x89PNG\r\n\x1a\nFAKE_TIM_BYTES"
    payload = teams_sender.build_payload(
        veeam_image=real_veeam_png,
        veeam_error=None,
        ppdm_image=None,
        ppdm_error="PPDM offline",
        timeismoney_image=real_tim_png,
        timeismoney_error=None,
        vm_hostname="X",
    )
    # Veeam e TIM: bytes originais preservados
    assert base64.b64decode(payload["veeam_image_b64"]) == real_veeam_png
    assert payload["veeam_error"] == ""
    assert base64.b64decode(payload["timeismoney_image_b64"]) == real_tim_png
    assert payload["timeismoney_error"] == ""
    # PPDM: PNG de erro gerado (não os bytes originais)
    assert payload["ppdm_image_b64"] != ""
    assert base64.b64decode(payload["ppdm_image_b64"]).startswith(b"\x89PNG")
    assert payload["ppdm_error"] == "PPDM offline"


# ---------- build_tim_only_payload (modo split: produtor TIM) ----------

def test_build_tim_only_payload_includes_only_tim_fields():
    """Payload do Fluxo B nao deve incluir veeam_* nem ppdm_*."""
    ts = datetime(2026, 5, 21, 8, 0, 0, tzinfo=timezone.utc)
    payload = teams_sender.build_tim_only_payload(
        timeismoney_image=b"\x89PNG_TIM",
        timeismoney_error=None,
        vm_hostname="VM-TIM",
        timestamp=ts,
    )
    assert set(payload.keys()) == {
        "timeismoney_image_b64", "timeismoney_error", "timestamp", "vm_hostname",
    }
    assert payload["timeismoney_image_b64"] == base64.b64encode(b"\x89PNG_TIM").decode("ascii")
    assert payload["timeismoney_error"] == ""
    assert payload["vm_hostname"] == "VM-TIM"
    assert payload["timestamp"] == "2026-05-21T08:00:00"


def test_build_tim_only_payload_generates_error_png_on_failure():
    """Captura TIM falhou -> PNG vermelho com mensagem, satisfaz hostedContents."""
    payload = teams_sender.build_tim_only_payload(
        timeismoney_image=None,
        timeismoney_error="timeout no admin-dashboard",
        vm_hostname="VM-TIM",
    )
    assert payload["timeismoney_image_b64"] != ""
    assert base64.b64decode(payload["timeismoney_image_b64"]).startswith(b"\x89PNG")
    assert payload["timeismoney_error"] == "timeout no admin-dashboard"


# ---------- build_veeam_ppdm_payload (modo split: agregador V+P) ----------

def test_build_veeam_ppdm_payload_includes_only_vp_fields():
    """Payload do Fluxo C nao tem campos TIM (PA le do OneDrive)."""
    ts = datetime(2026, 5, 21, 8, 0, 0, tzinfo=timezone.utc)
    payload = teams_sender.build_veeam_ppdm_payload(
        veeam_image=b"\x89PNG_V", veeam_error=None,
        ppdm_image=b"\x89PNG_P", ppdm_error=None,
        vm_hostname="VM-VP",
        timestamp=ts,
    )
    assert set(payload.keys()) == {
        "veeam_image_b64", "veeam_error",
        "ppdm_image_b64", "ppdm_error",
        "timestamp", "vm_hostname",
    }
    assert "timeismoney_image_b64" not in payload
    assert "timeismoney_error" not in payload
    assert payload["timestamp"] == "2026-05-21T08:00:00"


def test_build_veeam_ppdm_payload_propagates_partial_failure():
    """Veeam OK + PPDM falhou -> PPDM vira PNG vermelho, mensagem preservada."""
    payload = teams_sender.build_veeam_ppdm_payload(
        veeam_image=b"\x89PNG_REAL", veeam_error=None,
        ppdm_image=None, ppdm_error="PPDM offline",
        vm_hostname="VM-VP",
    )
    assert base64.b64decode(payload["veeam_image_b64"]) == b"\x89PNG_REAL"
    assert payload["veeam_error"] == ""
    assert payload["ppdm_image_b64"] != ""
    assert base64.b64decode(payload["ppdm_image_b64"]).startswith(b"\x89PNG")
    assert payload["ppdm_error"] == "PPDM offline"


# ---------- build_ppdm_only_payload (modo split: produtor PPDM) ----------

def test_build_ppdm_only_payload_includes_only_ppdm_fields():
    """Payload do Fluxo D (Store PPDM Artifact) nao inclui veeam nem TIM."""
    ts = datetime(2026, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
    payload = teams_sender.build_ppdm_only_payload(
        ppdm_image=b"\x89PNG_PPDM_REAL",
        ppdm_error=None,
        vm_hostname="VM-PPDM",
        timestamp=ts,
    )
    assert set(payload.keys()) == {
        "ppdm_image_b64", "ppdm_error", "timestamp", "vm_hostname",
    }
    assert payload["ppdm_image_b64"] == base64.b64encode(b"\x89PNG_PPDM_REAL").decode("ascii")
    assert payload["ppdm_error"] == ""
    assert payload["vm_hostname"] == "VM-PPDM"
    assert payload["timestamp"] == "2026-05-24T08:00:00"


def test_build_ppdm_only_payload_generates_error_png_on_failure():
    payload = teams_sender.build_ppdm_only_payload(
        ppdm_image=None,
        ppdm_error="PPDM unreachable",
        vm_hostname="VM-PPDM",
    )
    assert payload["ppdm_image_b64"] != ""
    assert base64.b64decode(payload["ppdm_image_b64"]).startswith(b"\x89PNG")
    assert payload["ppdm_error"] == "PPDM unreachable"


# ---------- build_veeam_only_payload (modo split: agregador V-only) ----------

def test_build_veeam_only_payload_includes_only_veeam_fields():
    """Payload do Fluxo C' (Aggregate V+OneDrive(P,T)) so manda Veeam."""
    ts = datetime(2026, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
    payload = teams_sender.build_veeam_only_payload(
        veeam_image=b"\x89PNG_VEEAM",
        veeam_error=None,
        vm_hostname="VM-VEEAM",
        timestamp=ts,
    )
    assert set(payload.keys()) == {
        "veeam_image_b64", "veeam_error", "timestamp", "vm_hostname",
    }
    assert "ppdm_image_b64" not in payload
    assert "timeismoney_image_b64" not in payload
    assert payload["timestamp"] == "2026-05-24T08:00:00"


def test_build_veeam_only_payload_generates_error_png_on_failure():
    payload = teams_sender.build_veeam_only_payload(
        veeam_image=None,
        veeam_error="Veeam window not found",
        vm_hostname="VM-VEEAM",
    )
    assert payload["veeam_image_b64"] != ""
    assert base64.b64decode(payload["veeam_image_b64"]).startswith(b"\x89PNG")
    assert payload["veeam_error"] == "Veeam window not found"


# ---------- _should_retry ----------

@pytest.mark.parametrize("status,expected", [
    (None, True),     # sem resposta (timeout/DNS/conexão) -> retry
    (429, True),      # rate limit -> retry
    (500, True),      # servidor -> retry
    (502, True),
    (503, True),
    (504, True),
    (599, True),      # ainda 5xx
    (400, False),     # bad request -> NÃO retry
    (401, False),     # auth -> NÃO retry
    (403, False),     # forbidden -> NÃO retry
    (404, False),     # URL errada -> NÃO retry
    (200, False),     # sucesso não deveria nem chegar aqui, mas defensivo
    (300, False),     # redirect -> NÃO retry (defensivo)
])
def test_should_retry_by_status(status, expected):
    assert teams_sender._should_retry(status, None) is expected


def test_should_retry_ignores_pa_code_in_current_policy():
    # Política atual decide só por status_code; pa_error_code é parâmetro futuro.
    assert teams_sender._should_retry(400, "WorkflowTriggerIsNotEnabled") is False
    assert teams_sender._should_retry(500, "WorkflowTriggerIsNotEnabled") is True


# ---------- _post_once ----------

def _make_response(status: int, headers=None, body=None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = body if isinstance(body, str) else json.dumps(body or {})
    resp.json.return_value = body or {}
    return resp


def test_post_once_success(mocker):
    mock_post = mocker.patch("src.teams_sender.requests.post")
    mock_post.return_value = _make_response(
        202, headers={"x-ms-workflow-run-id": "run-abc-123"}
    )
    result = teams_sender._post_once("http://x", {"a": 1}, timeout=10)
    assert result.success is True
    assert result.status_code == 202
    assert result.run_id == "run-abc-123"
    assert result.error_message is None
    mock_post.assert_called_once_with("http://x", json={"a": 1}, timeout=10)


def test_post_once_pa_structured_error(mocker):
    mocker.patch(
        "src.teams_sender.requests.post",
        return_value=_make_response(
            400,
            body={"error": {"code": "WorkflowTriggerIsNotEnabled", "message": "x"}},
        ),
    )
    result = teams_sender._post_once("http://x", {}, timeout=10)
    assert result.success is False
    assert result.status_code == 400
    assert "WorkflowTriggerIsNotEnabled" in result.error_message


def test_post_once_timeout(mocker):
    mocker.patch("src.teams_sender.requests.post", side_effect=requests.Timeout())
    result = teams_sender._post_once("http://x", {}, timeout=10)
    assert result.success is False
    assert result.status_code is None
    assert result.error_message == "timeout"


def test_post_once_connection_error(mocker):
    mocker.patch(
        "src.teams_sender.requests.post",
        side_effect=requests.ConnectionError("DNS failure"),
    )
    result = teams_sender._post_once("http://x", {}, timeout=10)
    assert result.success is False
    assert result.status_code is None
    assert "ConnectionError" in result.error_message


# ---------- send (orquestração com retry) ----------

def test_send_success_first_attempt(mocker):
    mocker.patch(
        "src.teams_sender.requests.post",
        return_value=_make_response(202, headers={"x-ms-workflow-run-id": "r1"}),
    )
    result = teams_sender.send({}, "http://x", max_attempts=3, backoff_seconds=0)
    assert result.success is True
    assert result.attempts == 1


def test_send_retries_on_5xx_then_succeeds(mocker):
    mock_post = mocker.patch(
        "src.teams_sender.requests.post",
        side_effect=[
            _make_response(503),
            _make_response(202, headers={"x-ms-workflow-run-id": "r2"}),
        ],
    )
    mocker.patch("src.teams_sender.time.sleep")  # não dormir nos testes
    result = teams_sender.send({}, "http://x", max_attempts=3, backoff_seconds=0)
    assert result.success is True
    assert result.attempts == 2
    assert mock_post.call_count == 2


def test_send_no_retry_on_4xx(mocker):
    mock_post = mocker.patch(
        "src.teams_sender.requests.post",
        return_value=_make_response(
            400, body={"error": {"code": "WorkflowTriggerIsNotEnabled"}}
        ),
    )
    result = teams_sender.send({}, "http://x", max_attempts=3, backoff_seconds=0)
    assert result.success is False
    assert result.attempts == 1
    assert mock_post.call_count == 1  # NÃO retentou


def test_send_exhausts_retries(mocker):
    mocker.patch(
        "src.teams_sender.requests.post",
        return_value=_make_response(503),
    )
    mocker.patch("src.teams_sender.time.sleep")
    result = teams_sender.send({}, "http://x", max_attempts=3, backoff_seconds=0)
    assert result.success is False
    assert result.attempts == 3


def test_send_writes_fallback_on_failure(mocker, tmp_path):
    mocker.patch(
        "src.teams_sender.requests.post",
        return_value=_make_response(503),
    )
    mocker.patch("src.teams_sender.time.sleep")
    payload = {
        "veeam_image_b64": "AAAAAA==",  # deve ser omitido do fallback
        "ppdm_image_b64": "BBBBBB==",
        "timeismoney_image_b64": "CCCCCC==",
        "veeam_error": "",
        "ppdm_error": "",
        "timeismoney_error": "",
        "vm_hostname": "X",
        "timestamp": "2026-05-16T08:00:00",
    }
    result = teams_sender.send(
        payload, "http://x", max_attempts=2, backoff_seconds=0, fallback_dir=tmp_path
    )
    assert result.success is False
    files = list(tmp_path.glob("FAILED_*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["payload"]["veeam_image_b64"] == "<omitido>"
    assert saved["payload"]["ppdm_image_b64"] == "<omitido>"
    assert saved["payload"]["vm_hostname"] == "X"
    assert saved["result"]["status_code"] == 503
