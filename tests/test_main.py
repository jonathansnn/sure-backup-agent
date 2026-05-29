"""Testes do dispatch de modos no main — foco no produtor PPDM last-known-good."""
from __future__ import annotations

import types

from src import main


def _fake_cfg(attempts=3, delay=0):
    """cfg minimo pro _run_ppdm_producer. teams/logging so usados no sucesso."""
    return types.SimpleNamespace(
        ppdm=types.SimpleNamespace(
            capture_retry_attempts=attempts,
            capture_retry_delay_seconds=delay,
        ),
        teams=types.SimpleNamespace(
            webhook_url="http://fake",
            http_timeout_seconds=10,
            retry_attempts=1,
            retry_backoff_seconds=0,
        ),
        logging=types.SimpleNamespace(log_dir="."),
    )


def _ok_send_result():
    return types.SimpleNamespace(
        success=True, status_code=202, run_id="r1", attempts=1, error_message=None,
    )


def test_ppdm_producer_does_not_send_when_all_attempts_fail(mocker):
    """Captura falha nas 3 tentativas -> NAO envia (preserva last-known-good)."""
    cfg = _fake_cfg(attempts=3, delay=0)
    log = mocker.MagicMock()
    cap = mocker.patch("src.main.ppdm_capture.capture", return_value=(None, "timeout"))
    send = mocker.patch("src.main.teams_sender.send")

    rc = main._run_ppdm_producer(cfg, log)

    assert rc == 1
    assert cap.call_count == 3            # tentou exatamente 3x
    send.assert_not_called()              # nada enviado -> artefato no OneDrive intacto


def test_ppdm_producer_sends_on_first_success(mocker):
    """Sucesso de primeira -> envia, sem retry."""
    cfg = _fake_cfg(attempts=3, delay=0)
    log = mocker.MagicMock()
    cap = mocker.patch("src.main.ppdm_capture.capture", return_value=(b"\x89PNG_real", None))
    send = mocker.patch("src.main.teams_sender.send", return_value=_ok_send_result())

    rc = main._run_ppdm_producer(cfg, log)

    assert rc == 0
    assert cap.call_count == 1            # nao retentou
    send.assert_called_once()


def test_ppdm_producer_retries_then_succeeds(mocker):
    """Falha na 1a, sucesso na 2a -> envia o print da 2a tentativa."""
    cfg = _fake_cfg(attempts=3, delay=0)
    log = mocker.MagicMock()
    cap = mocker.patch(
        "src.main.ppdm_capture.capture",
        side_effect=[(None, "timeout"), (b"\x89PNG_real", None)],
    )
    send = mocker.patch("src.main.teams_sender.send", return_value=_ok_send_result())

    rc = main._run_ppdm_producer(cfg, log)

    assert rc == 0
    assert cap.call_count == 2
    send.assert_called_once()


def test_ppdm_producer_never_sends_red_png_payload(mocker):
    """Garante que, no sucesso, o payload vai com ppdm_error vazio (nunca PNG erro)."""
    cfg = _fake_cfg(attempts=2, delay=0)
    log = mocker.MagicMock()
    mocker.patch("src.main.ppdm_capture.capture", return_value=(b"\x89PNG_real", None))
    send = mocker.patch("src.main.teams_sender.send", return_value=_ok_send_result())

    main._run_ppdm_producer(cfg, log)

    payload = send.call_args.args[0]      # send(payload, webhook_url=...)
    assert payload["ppdm_error"] == ""    # sinal canonico de sucesso
