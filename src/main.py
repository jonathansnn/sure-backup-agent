"""Entry point de producao do sure-backup-agent.

Chamado pelo Task Scheduler todo dia as 08:00 via run_daily.bat.

Suporta 5 modos de operacao via config.toml [mode].name:

  all          - single-server: captura V+P+TIM, envia tudo num POST pro
                 Fluxo PA "Send Daily Full" (legado, backward-compat).

  timeismoney  - produtor: captura TIM, POSTa pro Fluxo "Store TIM Artifact"
                 (salva o PNG no OneDrive). NAO posta no Teams.

  ppdm         - produtor: captura PPDM, POSTa pro Fluxo "Store PPDM Artifact".
                 Semantica last-known-good: tenta N vezes; se TODAS falharem,
                 NAO envia (preserva o ultimo print bom no OneDrive). Pensado
                 pra rodar varias vezes de madrugada.

  veeam        - agregador full-split: captura Veeam, POSTa pro Fluxo
                 "Aggregate + Send" que le PPDM E TIM do OneDrive e combina.

  veeam_ppdm   - agregador legado: captura V+P, le so TIM do OneDrive, envia.

A "ponte" entre as VMs no modo split eh feita pelos fluxos PA + OneDrive.
Cada VM tem seu proprio `teams_webhook` no keyring com a URL do fluxo certo.

Logs vao pra logs/agent.log (rotacionado diariamente, 30 dias de retencao).

Exit codes:
  0 - sucesso (POST pro PA retornou 2xx)
  1 - POST pro PA falhou apos todas as tentativas (payload salvo em logs/FAILED_*.json)
  2 - config invalida (TOML mal-formado, secret faltando, modo invalido)
"""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config, logger, ppdm_capture, teams_sender, timeismoney_capture, veeam_capture


def main() -> int:
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"[FATAL] config invalida: {exc}", file=sys.stderr)
        return 2

    log = logger.setup(cfg.logging.log_dir, cfg.logging.level, name="sure_backup_agent")
    log.info("================ INICIO (modo=%s) ================", cfg.mode.name)
    log.info("sure-backup-agent run iniciado em %s", socket.gethostname())

    if cfg.mode.name == config.MODE_TIMEISMONEY:
        return _run_timeismoney_producer(cfg, log)
    if cfg.mode.name == config.MODE_PPDM:
        return _run_ppdm_producer(cfg, log)
    if cfg.mode.name == config.MODE_VEEAM:
        return _run_veeam_aggregator(cfg, log)
    if cfg.mode.name == config.MODE_VEEAM_PPDM:
        return _run_veeam_ppdm_aggregator(cfg, log)
    return _run_all(cfg, log)


def _capture_veeam(cfg, log):
    log.info("Capturando Veeam Console...")
    img, err = veeam_capture.capture(cfg.veeam)
    if err:
        log.error("Veeam captura falhou: %s", err)
    else:
        log.info("Veeam captura OK: %d bytes", len(img))
    return img, err


def _capture_ppdm(cfg, log):
    log.info("Capturando PPDM Protection Jobs...")
    img, err = ppdm_capture.capture(cfg.ppdm)
    if err:
        log.error("PPDM captura falhou: %s", err)
    else:
        log.info("PPDM captura OK: %d bytes", len(img))
    return img, err


def _capture_tim(cfg, log):
    log.info("Capturando Time Is Money admin-dashboard...")
    img, err = timeismoney_capture.capture(cfg.timeismoney)
    if err:
        log.error("TimeIsMoney captura falhou: %s", err)
    else:
        log.info("TimeIsMoney captura OK: %d bytes", len(img))
    return img, err


def _send_and_finalize(cfg, log, payload, kind_label):
    """Wrapper comum: POSTa pro webhook configurado, loga, retorna exit code."""
    log.info("Enviando payload (%s) pro Power Automate...", kind_label)
    result = teams_sender.send(
        payload,
        webhook_url=cfg.teams.webhook_url,
        timeout=cfg.teams.http_timeout_seconds,
        max_attempts=cfg.teams.retry_attempts,
        backoff_seconds=cfg.teams.retry_backoff_seconds,
        fallback_dir=cfg.logging.log_dir,
    )
    if result.success:
        log.info("Envio OK (HTTP %s, run_id=%s, tentativas=%d)",
                 result.status_code, result.run_id, result.attempts)
        log.info("================ FIM (sucesso) ================")
        return 0
    log.error("Envio FALHOU apos %d tentativas: %s", result.attempts, result.error_message)
    log.error("Payload salvo como fallback em %s", cfg.logging.log_dir)
    log.info("================ FIM (falha) ================")
    return 1


def _run_all(cfg, log):
    """Modo legado/single-server: 1 VM faz tudo, manda payload completo."""
    veeam_img, veeam_err = _capture_veeam(cfg, log)
    ppdm_img, ppdm_err = _capture_ppdm(cfg, log)
    tim_img, tim_err = _capture_tim(cfg, log)
    payload = teams_sender.build_payload(
        veeam_image=veeam_img, veeam_error=veeam_err,
        ppdm_image=ppdm_img, ppdm_error=ppdm_err,
        timeismoney_image=tim_img, timeismoney_error=tim_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "full V+P+TIM")


def _run_timeismoney_producer(cfg, log):
    """Modo produtor TIM: captura e POSTa pro fluxo 'Store TIM Artifact'."""
    tim_img, tim_err = _capture_tim(cfg, log)
    payload = teams_sender.build_tim_only_payload(
        timeismoney_image=tim_img, timeismoney_error=tim_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "TIM-only")


def _capture_ppdm_with_retry(cfg, log):
    """Captura PPDM com ate N tentativas (config [ppdm].capture_retry_attempts).

    Retorna (image, None) na 1a tentativa que der certo, ou (None, ultimo_erro)
    se todas falharem. Espera capture_retry_delay_seconds entre tentativas.
    """
    attempts = max(1, cfg.ppdm.capture_retry_attempts)
    delay = cfg.ppdm.capture_retry_delay_seconds
    last_err = None
    for attempt in range(1, attempts + 1):
        log.info("PPDM captura tentativa %d/%d...", attempt, attempts)
        img, err = ppdm_capture.capture(cfg.ppdm)
        if not err:
            log.info("PPDM captura OK na tentativa %d: %d bytes", attempt, len(img))
            return img, None
        last_err = err
        log.warning("PPDM tentativa %d/%d falhou: %s", attempt, attempts, err)
        if attempt < attempts and delay > 0:
            log.info("Aguardando %ds antes da proxima tentativa...", delay)
            time.sleep(delay)
    return None, last_err


def _run_ppdm_producer(cfg, log):
    """Modo produtor PPDM com semantica 'last known good'.

    Tenta capturar ate N vezes (config [ppdm].capture_retry_attempts). Se
    conseguir, POSTa pro fluxo 'Store PPDM Artifact' (sobrescreve o artefato
    no OneDrive). Se TODAS falharem, NAO envia nada — assim o ultimo print
    bem-sucedido fica preservado no OneDrive.

    Pensado pra rodar varias vezes de madrugada: qualquer execucao com
    sucesso atualiza o artefato; execucoes que falham sao no-op.
    """
    ppdm_img, ppdm_err = _capture_ppdm_with_retry(cfg, log)
    if ppdm_err:
        log.error("PPDM falhou em todas as %d tentativas: %s",
                  max(1, cfg.ppdm.capture_retry_attempts), ppdm_err)
        log.error("NAO enviando — preservando ultimo artefato bom no OneDrive (last-known-good)")
        log.info("================ FIM (falha, artefato preservado) ================")
        return 1
    # Sucesso garantido aqui — ppdm_error=None, nunca gera PNG vermelho.
    payload = teams_sender.build_ppdm_only_payload(
        ppdm_image=ppdm_img, ppdm_error=None,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "PPDM-only")


def _run_veeam_aggregator(cfg, log):
    """Modo agregador full-split: captura Veeam, POSTa pro fluxo Aggregate+Send.

    O fluxo PA le PPDM E TIM do OneDrive (gravados pelos respectivos
    produtores) e combina os 3 numa mensagem unica pro Teams.
    """
    veeam_img, veeam_err = _capture_veeam(cfg, log)
    payload = teams_sender.build_veeam_only_payload(
        veeam_image=veeam_img, veeam_error=veeam_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "Veeam-only (P+TIM vem do OneDrive)")


def _run_veeam_ppdm_aggregator(cfg, log):
    """Modo agregador V+P: captura e POSTa pro fluxo 'Aggregate + Send'.

    O fluxo PA le o TIM do OneDrive (gravado mais cedo pela VM-TIM) e
    combina com V+P do payload antes de postar no Teams.
    """
    veeam_img, veeam_err = _capture_veeam(cfg, log)
    ppdm_img, ppdm_err = _capture_ppdm(cfg, log)
    payload = teams_sender.build_veeam_ppdm_payload(
        veeam_image=veeam_img, veeam_error=veeam_err,
        ppdm_image=ppdm_img, ppdm_error=ppdm_err,
        vm_hostname=socket.gethostname(),
    )
    return _send_and_finalize(cfg, log, payload, "V+P (TIM vem do OneDrive)")


if __name__ == "__main__":
    sys.exit(main())
