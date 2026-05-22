"""Contrato de arquivo compartilhado entre as 2 VMs do deploy split.

VM "timeismoney" escreve o resultado de cada captura em <shared_dir>:
    tim_image.png   - bytes do PNG (zero bytes se a captura falhou)
    tim_meta.json   - { "error": str, "timestamp": ISO8601 UTC, "hostname": str }

VM "veeam_ppdm" le esses 2 arquivos no momento de montar o payload pro Teams,
checa staleness via timestamp, e devolve (image|None, error|None) na mesma
interface dos capturadores diretos — assim o main.py trata o caso identico.

Por que arquivo em vez de HTTP?
  - Idempotente: V+P pode reler o mesmo artefato 10x sem efeito colateral
  - Debuggable: voce abre o PNG/JSON manualmente pra ver o que aconteceu
  - Zero infra: nao precisa expor porta nem fazer auth entre VMs

Escrita atomica: PNG + JSON sao escritos em arquivos .tmp e renomeados no
final, evitando que o leitor pegue um arquivo half-written se a leitura
acontecer no meio da escrita (improvavel mas barato de proteger).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sure_backup_agent.artifact_store")

IMAGE_FILENAME = "tim_image.png"
META_FILENAME = "tim_meta.json"


def write_tim(
    shared_dir: Path,
    image: Optional[bytes],
    error: Optional[str],
    timestamp: Optional[datetime] = None,
    hostname: str = "",
) -> None:
    """Escreve o resultado da captura TIM no diretorio compartilhado.

    Sempre escreve os 2 arquivos. Se image=None (captura falhou), o PNG fica
    com 0 bytes — o leitor detecta isso e gera o PNG de erro vermelho.
    """
    shared_dir.mkdir(parents=True, exist_ok=True)
    ts = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    meta = {
        "error": error or "",
        "timestamp": ts.isoformat(timespec="seconds"),
        "hostname": hostname,
        "image_bytes": len(image) if image else 0,
    }

    img_final = shared_dir / IMAGE_FILENAME
    img_tmp = shared_dir / (IMAGE_FILENAME + ".tmp")
    img_tmp.write_bytes(image or b"")
    os.replace(img_tmp, img_final)  # atomico no mesmo FS

    meta_final = shared_dir / META_FILENAME
    meta_tmp = shared_dir / (META_FILENAME + ".tmp")
    meta_tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(meta_tmp, meta_final)

    logger.info(
        "TIM artefato escrito em %s (image=%d bytes, error=%r, ts=%s)",
        shared_dir, meta["image_bytes"], meta["error"], meta["timestamp"],
    )


def read_tim(
    shared_dir: Path, max_age_minutes: int
) -> tuple[Optional[bytes], Optional[str]]:
    """Le o artefato TIM do diretorio compartilhado.

    Returns:
      (image_bytes, None) - sucesso, captura recente, sem erro
      (None, error_msg)   - falha conhecida (captura original falhou OU artefato
                            stale OU artefato ausente). Mensagem ja amigavel pro Teams.
    """
    meta_path = shared_dir / META_FILENAME
    img_path = shared_dir / IMAGE_FILENAME

    if not meta_path.exists():
        return None, f"TIM artefato ausente em {shared_dir} (VM TIM rodou?)"

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"TIM artefato corrompido: {exc.__class__.__name__}"

    # Staleness: comparamos com agora UTC
    try:
        artifact_ts = datetime.fromisoformat(meta["timestamp"])
    except (KeyError, ValueError):
        return None, "TIM artefato sem timestamp valido"

    age_minutes = (datetime.now(timezone.utc) - artifact_ts).total_seconds() / 60
    if age_minutes > max_age_minutes:
        return None, (
            f"TIM artefato stale ({int(age_minutes)}min, limite {max_age_minutes}min) "
            f"- ultima captura: {meta['timestamp']}"
        )

    # Se a captura original ja falhou, propaga o erro original
    if meta.get("error"):
        return None, meta["error"]

    # Captura OK — le os bytes do PNG
    if not img_path.exists():
        return None, "TIM meta diz sucesso mas PNG nao existe"
    image = img_path.read_bytes()
    if not image:
        return None, "TIM meta diz sucesso mas PNG esta vazio"

    logger.info("TIM artefato OK (%d bytes, idade %.1fmin)", len(image), age_minutes)
    return image, None
