"""Envia o relatório diário para o canal do Teams via fluxo Power Automate.

Payload esperado pelo fluxo PA (todos os campos string; vazio = "ausente"):
    {
        "veeam_image_b64": str,   # base64 do PNG ou ""
        "veeam_error":     str,   # mensagem amigável ou ""
        "ppdm_image_b64":  str,   # base64 do PNG ou ""
        "ppdm_error":      str,   # mensagem amigável ou ""
        "timestamp":       str,   # ISO 8601 com offset (ex: "2026-05-16T08:00:00-03:00")
        "vm_hostname":     str,   # hostname da VM (ex: "SRVTIM")
    }
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from src.image_utils import render_error_png

logger = logging.getLogger("sure_backup_agent.teams_sender")


@dataclass(frozen=True)
class SendResult:
    success: bool
    status_code: Optional[int]
    run_id: Optional[str]
    error_message: Optional[str]
    attempts: int


def build_payload(
    veeam_image: Optional[bytes],
    veeam_error: Optional[str],
    ppdm_image: Optional[bytes],
    ppdm_error: Optional[str],
    timeismoney_image: Optional[bytes],
    timeismoney_error: Optional[str],
    vm_hostname: str,
    timestamp: Optional[datetime] = None,
) -> dict:
    """Constrói o payload JSON.

    Contrato:
      - Os campos `*_image_b64` SEMPRE contêm bytes válidos de PNG (nunca string vazia).
        Quando a captura falha, geramos um PNG vermelho "FAILED" com o texto do erro,
        pra (a) não quebrar o hostedContents do Power Automate, e (b) dar feedback
        visual claro pra diretoria.
      - Os campos `*_error` continuam sendo o sinal canônico de sucesso/falha:
        vazio = sucesso, não-vazio = falha (com mensagem amigável).
    """
    # Sempre enviar UTC bare (sem offset suffix). PA's convertFromUtc rejeita
    # timestamps com offset e assume input como UTC. Garantir UTC explícito
    # no Python remove ambiguidade independente do fuso do servidor.
    raw_ts = timestamp or datetime.now(timezone.utc)
    ts_utc_bare = raw_ts.astimezone(timezone.utc).replace(tzinfo=None)

    veeam_bytes = veeam_image if veeam_image else render_error_png("VEEAM", veeam_error or "erro desconhecido")
    ppdm_bytes = ppdm_image if ppdm_image else render_error_png("PPDM", ppdm_error or "erro desconhecido")
    tim_bytes = timeismoney_image if timeismoney_image else render_error_png("TIME IS MONEY", timeismoney_error or "erro desconhecido")

    return {
        "veeam_image_b64": base64.b64encode(veeam_bytes).decode("ascii"),
        "veeam_error": veeam_error or "",
        "ppdm_image_b64": base64.b64encode(ppdm_bytes).decode("ascii"),
        "ppdm_error": ppdm_error or "",
        "timeismoney_image_b64": base64.b64encode(tim_bytes).decode("ascii"),
        "timeismoney_error": timeismoney_error or "",
        "timestamp": ts_utc_bare.isoformat(timespec="seconds"),
        "vm_hostname": vm_hostname,
    }


def build_tim_only_payload(
    timeismoney_image: Optional[bytes],
    timeismoney_error: Optional[str],
    vm_hostname: str,
    timestamp: Optional[datetime] = None,
) -> dict:
    """Payload pro Fluxo B (Store TIM Artifact) — modo='timeismoney'.

    Esse fluxo nao posta no Teams; ele salva o PNG do TIM no OneDrive
    pra o Fluxo C (agregador) ler depois. Mesma convencao do build_payload:
    image SEMPRE vem como PNG valido (gera placeholder vermelho se a
    captura falhou), erro como string canonica.
    """
    raw_ts = timestamp or datetime.now(timezone.utc)
    ts_utc_bare = raw_ts.astimezone(timezone.utc).replace(tzinfo=None)
    tim_bytes = timeismoney_image if timeismoney_image else render_error_png(
        "TIME IS MONEY", timeismoney_error or "erro desconhecido"
    )
    return {
        "timeismoney_image_b64": base64.b64encode(tim_bytes).decode("ascii"),
        "timeismoney_error": timeismoney_error or "",
        "timestamp": ts_utc_bare.isoformat(timespec="seconds"),
        "vm_hostname": vm_hostname,
    }


def build_veeam_ppdm_payload(
    veeam_image: Optional[bytes],
    veeam_error: Optional[str],
    ppdm_image: Optional[bytes],
    ppdm_error: Optional[str],
    vm_hostname: str,
    timestamp: Optional[datetime] = None,
) -> dict:
    """Payload pro Fluxo C (Aggregate + Send) — modo='veeam_ppdm'.

    O fluxo C tem V+P do payload e le TIM do OneDrive (gravado pelo Fluxo B
    quando a VM-TIM rodou). Por isso o payload aqui NAO inclui campos TIM.
    """
    raw_ts = timestamp or datetime.now(timezone.utc)
    ts_utc_bare = raw_ts.astimezone(timezone.utc).replace(tzinfo=None)
    veeam_bytes = veeam_image if veeam_image else render_error_png(
        "VEEAM", veeam_error or "erro desconhecido"
    )
    ppdm_bytes = ppdm_image if ppdm_image else render_error_png(
        "PPDM", ppdm_error or "erro desconhecido"
    )
    return {
        "veeam_image_b64": base64.b64encode(veeam_bytes).decode("ascii"),
        "veeam_error": veeam_error or "",
        "ppdm_image_b64": base64.b64encode(ppdm_bytes).decode("ascii"),
        "ppdm_error": ppdm_error or "",
        "timestamp": ts_utc_bare.isoformat(timespec="seconds"),
        "vm_hostname": vm_hostname,
    }


def _parse_pa_error(response: requests.Response) -> Optional[str]:
    """Extrai error.code do payload de erro do Power Automate, se houver."""
    try:
        body = response.json()
        return body.get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None


def _should_retry(status_code: Optional[int], pa_error_code: Optional[str]) -> bool:
    """Decide se um erro vale uma nova tentativa.

    Política (revisitar pós-deploy conforme uso real):
      - Sem resposta (timeout / DNS / conexão recusada) -> retry
      - HTTP 429 (rate limit) -> retry
      - HTTP 5xx (erro do servidor) -> retry
      - HTTP 4xx (exceto 429) -> NÃO retry (config / payload / auth — não resolve sozinho)
    """
    if status_code is None:
        return True
    if status_code == 429:
        return True
    return 500 <= status_code < 600


def _post_once(url: str, payload: dict, timeout: int) -> SendResult:
    """Uma única tentativa de POST. Encapsula todos os modos de falha em SendResult."""
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        run_id = resp.headers.get("x-ms-workflow-run-id")
        if 200 <= resp.status_code < 300:
            return SendResult(
                success=True,
                status_code=resp.status_code,
                run_id=run_id,
                error_message=None,
                attempts=1,
            )
        pa_code = _parse_pa_error(resp)
        return SendResult(
            success=False,
            status_code=resp.status_code,
            run_id=run_id,
            error_message=f"HTTP {resp.status_code} — PA error: {pa_code or resp.text[:200]}",
            attempts=1,
        )
    except requests.Timeout:
        return SendResult(False, None, None, "timeout", 1)
    except requests.RequestException as exc:
        return SendResult(False, None, None, f"request error: {exc.__class__.__name__}: {exc}", 1)


def send(
    payload: dict,
    webhook_url: str,
    *,
    timeout: int = 30,
    max_attempts: int = 3,
    backoff_seconds: int = 5,
    fallback_dir: Optional[Path] = None,
) -> SendResult:
    """Envia o payload com retry; em falha definitiva grava fallback no disco."""
    last_result: Optional[SendResult] = None
    for attempt in range(1, max_attempts + 1):
        result = _post_once(webhook_url, payload, timeout)
        last_result = SendResult(
            success=result.success,
            status_code=result.status_code,
            run_id=result.run_id,
            error_message=result.error_message,
            attempts=attempt,
        )

        if result.success:
            logger.info("Envio OK (HTTP %s, run_id=%s, tentativa %d)",
                        result.status_code, result.run_id, attempt)
            return last_result

        pa_code = None
        if result.status_code is not None:
            # Refaz o parse do error_message já formatado para extrair só o code
            if result.error_message and "PA error: " in result.error_message:
                pa_code = result.error_message.split("PA error: ", 1)[1]

        if not _should_retry(result.status_code, pa_code):
            logger.error("Falha definitiva (não vale retry): %s", result.error_message)
            break

        if attempt < max_attempts:
            wait = backoff_seconds * (2 ** (attempt - 1))
            logger.warning("Falha tentativa %d/%d: %s — aguardando %ds",
                           attempt, max_attempts, result.error_message, wait)
            time.sleep(wait)

    assert last_result is not None
    if not last_result.success and fallback_dir is not None:
        _write_fallback(payload, last_result, fallback_dir)
    return last_result


def _write_fallback(payload: dict, result: SendResult, fallback_dir: Path) -> None:
    """Salva payload em disco quando todas as tentativas falharam."""
    fallback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    path = fallback_dir / f"FAILED_{ts}.json"
    fallback_data = {
        "payload": {**payload, "veeam_image_b64": "<omitido>", "ppdm_image_b64": "<omitido>"},
        "result": {
            "status_code": result.status_code,
            "error_message": result.error_message,
            "attempts": result.attempts,
        },
    }
    path.write_text(json.dumps(fallback_data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.error("Payload salvo para investigação posterior: %s", path)
