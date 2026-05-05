"""
afternoon_slide_renderer.py
Kairós — Renderer post pomeridiano (post singolo 1080×1350)

Layout editoriale su sfondo Paper (#F5F2E6):
  - Formato verticale 1080×1350 (4:5, ideale per feed Instagram)
  - Sfondo Paper crema — stesso del carosello delle 9
  - Headline grande centrata, serif Cormorant Garamond
  - Una parola in Gold per accento visivo
  - Eyebrow label in mono sopra
  - Subline in sans sotto
  - Logo Kairós in basso
  - Nessun dato numerico, nessuna tabella — solo testo editoriale
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ─── Dimensioni ───────────────────────────────────────────────────────────────
W, H = 1080, 1350

# ─── Palette Kairós ──────────────────────────────────────────────────────────
INK       = (14, 14, 12)
INK_50    = (107, 107, 98)
INK_15    = (212, 211, 199)
PAPER     = (245, 242, 230)
GOLD      = (184, 137, 59)
GOLD_DEEP = (140, 102, 36)


# ─── Font loader (identico al carosello che funziona) ────────────────────────

def _load_fonts():
    d = Path(__file__).parent / "fonts"

    def ttf(name, size):
        p = d / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
        logger.warning(f"afternoon_renderer: font non trovato {name} size={size}")
        return ImageFont.load_default()

    serif = "CormorantGaramond-Medium.ttf"
    sans  = "Inter-Regular.ttf"
    mono  = "JetBrainsMono-Regular.ttf"

    return {
        "eyebrow":     ttf(mono,  28),
        "headline":    ttf(serif, 96),
        "headline_sm": ttf(serif, 72),
        "subline":     ttf(sans,  38),
        "logo":        ttf(serif, 44),
        "tagline":     ttf(sans,  26),
    }


# ─── Utilità testo ────────────────────────────────────────────────────────────

def _wrap(draw, text, font, max_w):
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _tw(draw, text, font):
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0]


def _th(draw, text, font):
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[3] - bbox[1]


def _draw_centered(draw, text, font, y, color, max_w, gap=12):
    """Testo centrato con word-wrap. Ritorna y finale."""
    for line in _wrap(draw, text, font, max_w):
        w = _tw(draw, line, font)
        draw.text(((W - w) // 2, y), line, font=font, fill=color)
        y += _th(draw, line, font) + gap
    return y


def _draw_headline_accent(draw, headline, accent, font, y, max_w, gap=16):
    """Headline centrata con accent_word in Gold. Ritorna y finale."""
    if not accent or accent.lower() not in headline.lower():
        return _draw_centered(draw, headline, font, y, INK, max_w, gap)

    for line in _wrap(draw, headline, font, max_w):
        lw = _tw(draw, line, font)
        lh = _th(draw, line, font)
        x = (W - lw) // 2
        lower_line = line.lower()
        lower_accent = accent.lower()

        if lower_accent in lower_line:
            idx = lower_line.find(lower_accent)
            before      = line[:idx]
            accent_part = line[idx:idx+len(accent)]
            after       = line[idx+len(accent):]
            cx = x
            if before:
                draw.text((cx, y), before, font=font, fill=INK)
                cx += _tw(draw, before, font)
            draw.text((cx, y), accent_part, font=font, fill=GOLD)
            cx += _tw(draw, accent_part, font)
            if after:
                draw.text((cx, y), after, font=font, fill=INK)
        else:
            draw.text((x, y), line, font=font, fill=INK)

        y += lh + gap
    return y


# ─── Renderer principale ──────────────────────────────────────────────────────

def render_afternoon_post(content, output_dir: str) -> str:
    """
    Renderizza il post pomeridiano come PNG 1080×1350.

    Args:
        content: AfternoonPostContent (o dict con stessi campi)
        output_dir: directory dove salvare il PNG

    Returns:
        Percorso assoluto al file PNG generato
    """
    if hasattr(content, "__dict__"):
        c = content.__dict__
    else:
        c = dict(content)

    theme    = c.get("theme", "CURIOSITA_MACRO")
    headline = c.get("headline", "")
    subline  = c.get("subline", "")
    eyebrow  = c.get("eyebrow", "")
    accent   = c.get("accent_word", "")

    f = _load_fonts()

    # ── Canvas Paper ──────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    MARGIN    = 80
    CONTENT_W = W - MARGIN * 2

    # ── Barra gold in cima ────────────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, 6)], fill=GOLD)

    # ── Eyebrow label ─────────────────────────────────────────────────────────
    eyebrow_y   = 80
    eyebrow_txt = eyebrow.upper()
    eb_w = _tw(draw, eyebrow_txt, f["eyebrow"])
    draw.text(((W - eb_w) // 2, eyebrow_y), eyebrow_txt, font=f["eyebrow"], fill=GOLD_DEEP)

    # ── Linea separatrice gold ────────────────────────────────────────────────
    sep_y = eyebrow_y + 52
    draw.rectangle([(W//2 - 50, sep_y), (W//2 + 50, sep_y + 2)], fill=GOLD)

    # ── Headline ──────────────────────────────────────────────────────────────
    hl_font    = f["headline"] if len(headline) <= 35 else f["headline_sm"]
    headline_y = sep_y + 80

    headline_end_y = _draw_headline_accent(
        draw, headline, accent, hl_font, headline_y, CONTENT_W
    )

    # ── Subline ───────────────────────────────────────────────────────────────
    if subline:
        sub_y = headline_end_y + 36
        _draw_centered(draw, subline, f["subline"], sub_y, INK_50, CONTENT_W)

    # ── Linea separatrice sopra logo ──────────────────────────────────────────
    line_y = H - 210
    draw.rectangle([(MARGIN, line_y), (W - MARGIN, line_y + 1)], fill=INK_15)

    # ── Tre punti decorativi ──────────────────────────────────────────────────
    dot_y = line_y + 28
    for i, dx in enumerate([-16, 0, 16]):
        dot_x = W // 2 + dx
        r = 4
        draw.ellipse([(dot_x - r, dot_y - r), (dot_x + r, dot_y + r)],
                     fill=GOLD if i == 1 else INK_15)

    # ── Logo KAIRÓS ───────────────────────────────────────────────────────────
    logo_text = "KAIRÓS"
    logo_w    = _tw(draw, logo_text, f["logo"])
    logo_y    = H - 165
    draw.text(((W - logo_w) // 2, logo_y), logo_text, font=f["logo"], fill=INK)

    # ── Tagline ───────────────────────────────────────────────────────────────
    tagline = "We don't predict the market. We mark the moment."
    tl_w    = _tw(draw, tagline, f["tagline"])
    draw.text(((W - tl_w) // 2, logo_y + 58), tagline, font=f["tagline"], fill=INK_50)

    # ── Barra gold in fondo ───────────────────────────────────────────────────
    draw.rectangle([(0, H - 6), (W, H)], fill=GOLD)

    # ── Salva ─────────────────────────────────────────────────────────────────
    filename = f"afternoon_post_{theme.lower()}.png"
    out_path = Path(output_dir) / filename
    img.save(str(out_path), "PNG", quality=95)
    logger.info(f"afternoon_slide_renderer: salvato {out_path} ({out_path.stat().st_size // 1024}KB)")

    return str(out_path)


# ─── Test standalone ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    class MockContent:
        theme        = "CURIOSITA_MACRO"
        headline     = "Perché il dollaro sale quando c'è crisi?"
        subline      = "Il paradosso del safe haven più usato al mondo."
        eyebrow      = "CURIOSITÀ MACRO"
        caption      = "Test caption"
        hashtags     = ["macro", "kairos"]
        accent_word  = "dollaro"

    with tempfile.TemporaryDirectory() as td:
        path = render_afternoon_post(MockContent(), td)
        dest = Path(__file__).parent.parent / Path(path).name
        shutil.copy(path, dest)
        print(f"\nPost generato: {dest}")
        print(f"Dimensione: {dest.stat().st_size // 1024}KB")
