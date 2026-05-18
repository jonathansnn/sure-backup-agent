"""Utilidades de geração de imagens (placeholders e renderização de erros)."""
from __future__ import annotations

import io
from datetime import datetime

from PIL import Image, ImageDraw


def render_error_png(label: str, error_text: str, width: int = 1200, height: int = 400) -> bytes:
    """Gera PNG vermelho indicando falha de captura, pra exibir no Teams.

    Args:
        label: 'VEEAM' ou 'PPDM' — vai como título do erro.
        error_text: mensagem amigável do erro (truncada se muito longa).
        width, height: dimensões do PNG. Default casa com a captura cropada do PPDM (~1200x900).

    Returns:
        Bytes do PNG.
    """
    color = (160, 40, 40)  # vermelho escuro, contraste com texto branco
    img = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(img)

    # Texto truncado pra caber visualmente — erro completo já está no log.
    short_error = error_text.strip()
    if len(short_error) > 200:
        short_error = short_error[:197] + "..."

    text = (
        f"⚠️ {label} — CAPTURA FALHOU\n\n"
        f"{short_error}\n\n"
        f"Tentativa em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    draw.multiline_text(
        (width // 2, height // 2),
        text,
        fill="white",
        anchor="mm",
        align="center",
        spacing=12,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def auto_trim_bottom(
    png_bytes: bytes,
    padding: int = 20,
    ignore_bottom_px: int = 80,
    variance_threshold: int = 5,
    sample_step: int = 10,
) -> bytes:
    """Detecta o fim do conteúdo (último row com variância de cor) e crop o resto.

    Estratégia: scan de baixo pra cima, ignorando os últimos `ignore_bottom_px`
    (que tipicamente contêm status bars / footers que não queremos confundir
    com conteúdo). Em cada linha, amostra cores a cada `sample_step` pixels;
    se a linha tem mais que `variance_threshold` cores distintas, é "conteúdo".
    Trim acontece um pouco depois desse último row de conteúdo (margem = padding).

    Args:
        png_bytes: PNG de entrada.
        padding: pixels de respiro abaixo do último row de conteúdo.
        ignore_bottom_px: pixels do fundo a ignorar no scan (status bar etc).
        variance_threshold: # de cores distintas que define "conteúdo".
        sample_step: a cada N pixels da linha (otimização — pixel-a-pixel seria lento).

    Se a imagem toda for "uniforme" (sem conteúdo detectado), devolve como veio.
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    width, height = img.size
    pixels = img.load()

    scan_start = max(height - ignore_bottom_px - 1, 0)

    for y in range(scan_start, -1, -1):
        unique_colors = set()
        for x in range(0, width, sample_step):
            unique_colors.add(pixels[x, y])
            if len(unique_colors) > variance_threshold:
                break
        if len(unique_colors) > variance_threshold:
            new_bottom = min(y + padding, height)
            if new_bottom < height - padding:  # só vale se ganhar espaço significativo
                cropped = img.crop((0, 0, width, new_bottom))
                out = io.BytesIO()
                cropped.save(out, format="PNG")
                return out.getvalue()
            break

    return png_bytes
