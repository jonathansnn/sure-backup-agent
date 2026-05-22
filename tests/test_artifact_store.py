"""Testes do contrato shared_dir entre VMs (modo split)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src import artifact_store


# ---------- write_tim ----------

def test_write_creates_image_and_meta(tmp_path):
    artifact_store.write_tim(tmp_path, image=b"\x89PNG_FAKE", error=None, hostname="VM_TIM")

    img = (tmp_path / "tim_image.png").read_bytes()
    meta = json.loads((tmp_path / "tim_meta.json").read_text(encoding="utf-8"))

    assert img == b"\x89PNG_FAKE"
    assert meta["error"] == ""
    assert meta["hostname"] == "VM_TIM"
    assert meta["image_bytes"] == len(b"\x89PNG_FAKE")
    # timestamp parseavel
    datetime.fromisoformat(meta["timestamp"])


def test_write_failure_case_zero_byte_png(tmp_path):
    """Quando captura TIM falha, image=None -> PNG fica vazio, error preservado."""
    artifact_store.write_tim(tmp_path, image=None, error="timeout no login", hostname="VM_TIM")
    assert (tmp_path / "tim_image.png").read_bytes() == b""
    meta = json.loads((tmp_path / "tim_meta.json").read_text(encoding="utf-8"))
    assert meta["error"] == "timeout no login"
    assert meta["image_bytes"] == 0


def test_write_creates_dir_if_missing(tmp_path):
    target = tmp_path / "nao" / "existe" / "ainda"
    artifact_store.write_tim(target, image=b"x", error=None)
    assert (target / "tim_image.png").exists()


# ---------- read_tim ----------

def test_read_returns_image_when_fresh_and_ok(tmp_path):
    artifact_store.write_tim(tmp_path, image=b"\x89PNG_VALID", error=None)
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert err is None
    assert img == b"\x89PNG_VALID"


def test_read_returns_error_when_meta_says_failure(tmp_path):
    artifact_store.write_tim(tmp_path, image=None, error="timeout no login")
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert img is None
    assert err == "timeout no login"


def test_read_returns_error_when_artifact_missing(tmp_path):
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert img is None
    assert "ausente" in err.lower()


def test_read_returns_error_when_stale(tmp_path):
    # Escreve com timestamp 2h no passado
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    artifact_store.write_tim(tmp_path, image=b"old", error=None, timestamp=past)
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert img is None
    assert "stale" in err.lower()


def test_read_returns_error_when_meta_corrupted(tmp_path):
    artifact_store.write_tim(tmp_path, image=b"x", error=None)
    (tmp_path / "tim_meta.json").write_text("{nao eh json valido", encoding="utf-8")
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert img is None
    assert "corrompido" in err.lower()


def test_read_handles_meta_says_ok_but_png_empty(tmp_path):
    """Edge case: meta esta bem mas o PNG foi truncado pra zero bytes."""
    artifact_store.write_tim(tmp_path, image=b"valid", error=None)
    (tmp_path / "tim_image.png").write_bytes(b"")
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert img is None
    assert "vazio" in err.lower()


# ---------- roundtrip ----------

def test_roundtrip_success(tmp_path):
    """Producer escreve, consumer le, valores batem."""
    artifact_store.write_tim(tmp_path, image=b"PIPELINE_BYTES", error=None, hostname="VM_TIM")
    img, err = artifact_store.read_tim(tmp_path, max_age_minutes=60)
    assert img == b"PIPELINE_BYTES"
    assert err is None
