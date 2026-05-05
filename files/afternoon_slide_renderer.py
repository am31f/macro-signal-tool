"""
afternoon_slide_renderer.py
Kairós — Renderer post pomeridiano (post singolo 1080×1350)

Layout completamente diverso dal carosello delle 9:
  - Formato verticale 1080×1350 (4:5, ideale per feed Instagram)
  - Sfondo Ink scuro (#0E0E0C) — inverso rispetto al carosello Paper
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

# ─── Palette Kairós (invertita rispetto al carosello) ────────────────────────
INK        = "#0E0E0C"
PAPER      = "#F5F2E6"
GOLD       = "#B8893B"
GOLD_SOFT  = "#C9A062"
INK_50     = "#6B6B62"
INK_15     = "#D4D3C7"

def _hex(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

C_INK       = _hex(INK)
C_PAPER     = _hex(PAPER)
C_GOLD      = _hex(GOLD)
C_GOLD_SOFT = _hex(GOLD_SOFT)
C_INK_50    = _hex(INK_50)
C_INK_15    = _hex(INK_15)

# ─── Font ─────────────────────────────────────────────────────────────────────
FONTS_DIR = Path(__file__).parent / "fonts"

def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        FONTS_DIR / name,
        Path("/usr/share/fonts/truetype") / name,
        Path("/usr/share/fonts") / name,
    ]
    for p in candidates:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()

def _get_fonts():
    return {
        "eyebrow":   _load_font("JetBrainsMono-Regular.ttf", 28),
        "headline":  _load_font("CormorantGaramond-SemiBold.ttf", 96),
        "headline_sm": _load_font("CormorantGaramond-SemiBold.ttf", 72),
        "subline":   _load_font("InterTight-Regular.ttf", 38),
        "logo":      _load_font("CormorantGaramond-SemiBold.ttf", 44),
        "tagline":   _load_font("InterTight-Regular.ttf", 26),
    }


# ─── Utilità testo ────────────────────────────────────────────────────────────

def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Divide il testo in righe che non superano max_width pixel."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_centered(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                         y: int, color: tuple, max_width: int) -> int:
    """Disegna testo centrato, va a capo se supera max_width. Ritorna y finale."""
    lines = _wrap_text(text, font, max_width, draw)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (W - w) // 2
        draw.text((x, y), line, font=font, fill=color)
        y += (bbox[3] - bbox[1]) + 12
    return y


def _draw_headline_with_accent(draw: ImageDraw.ImageDraw, headline: str, accent_word: str,
                                 font: ImageFont.FreeTypeFont, y: int, max_width: int) -> int:
    """
    Disegna l'headline centrata con una parola in Gold.
    Se accent_word è vuota, disegna tutto in PAPER.
    """
    if not accent_word or accent_word.lower() not in headline.lower():
        return _draw_text_centered(draw, headline, font, y, C_PAPER, max_width)

    lines = _wrap_text(headline, font, max_width, draw)
    line_h = None

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        if line_h is None:
            line_h = lh
        x_start = (W - lw) // 2

        # Controlla se questa riga contiene la parola accentata
        lower_line = line.lower()
        lower_accent = accent_word.lower()

        if lower_accent in lower_line:
            # Trova la posizione della parola nella riga
            idx = lower_line.find(lower_accent)
            before = line[:idx]
            accent_part = line[idx:idx+len(accent_word)]
            after = line[idx+len(accent_word):]

            # Misura le parti
            x = x_start
            if before:
                draw.text((x, y), before, font=font, fill=C_PAPER)
                b_bbox = draw.textbbox((0, 0), before, font=font)
                x += b_bbox[2] - b_bbox[0]

            draw.text((x, y), accent_part, font=font, fill=C_GOLD)
            a_bbox = draw.textbbox((0, 0), accent_part, font=font)
            x += a_bbox[2] - a_bbox[0]

            if after:
                draw.text((x, y), after, font=font, fill=C_PAPER)
        else:
            draw.text((x_start, y), line, font=font, fill=C_PAPER)

        y += lh + 14

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

    theme     = c.get("theme", "CURIOSITA_MACRO")
    headline  = c.get("headline", "")
    subline   = c.get("subline", "")
    eyebrow   = c.get("eyebrow", "")
    accent    = c.get("accent_word", "")

    fonts = _get_fonts()

    # ── Canvas ────────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), C_INK)
    draw = ImageDraw.Draw(img)

    MARGIN = 80
    CONTENT_W = W - MARGIN * 2

    # ── Linea oro in cima (brand mark) ────────────────────────────────────────
    draw.rectangle([(0, 0), (W, 5)], fill=C_GOLD)

    # ── Eyebrow label ─────────────────────────────────────────────────────────
    eyebrow_y = 80
    eyebrow_text = eyebrow.upper()
    eb_bbox = draw.textbbox((0, 0), eyebrow_text, font=fonts["eyebrow"])
    eb_w = eb_bbox[2] - eb_bbox[0]
    draw.text(((W - eb_w) // 2, eyebrow_y), eyebrow_text, font=fonts["eyebrow"], fill=C_GOLD_SOFT)

    # ── Linea separatore sottile sotto eyebrow ────────────────────────────────
    sep_y = eyebrow_y + 48
    draw.rectangle([(W//2 - 40, sep_y), (W//2 + 40, sep_y + 1)], fill=C_INK_50)

    # ── Headline (grande, centrata, con accento gold) ─────────────────────────
    # Scegli dimensione font in base alla lunghezza
    hl_font = fonts["headline"] if len(headline) <= 35 else fonts["headline_sm"]

    headline_y = H // 2 - 160
    headline_end_y = _draw_headline_with_accent(
        draw, headline, accent, hl_font, headline_y, CONTENT_W
    )

    # ── Subline ───────────────────────────────────────────────────────────────
    if subline:
        sub_y = headline_end_y + 28
        _draw_text_centered(draw, subline, fonts["subline"], sub_y, C_INK_15, CONTENT_W)

    # ── Decorazione: tre punti gold ───────────────────────────────────────────
    dot_y = H - 200
    for i, dx in enumerate([-16, 0, 16]):
        dot_x = W // 2 + dx
        r = 3
        draw.ellipse([(dot_x - r, dot_y - r), (dot_x + r, dot_y + r)],
                     fill=C_GOLD if i == 1 else C_INK_50)

    # ── Logo Kairós in basso ──────────────────────────────────────────────────
    logo_text = "KAIRÓS"
    logo_bbox = draw.textbbox((0, 0), logo_text, font=fonts["logo"])
    logo_w = logo_bbox[2] - logo_bbox[0]
    logo_y = H - 155
    draw.text(((W - logo_w) // 2, logo_y), logo_text, font=fonts["logo"], fill=C_PAPER)

    # ── Tagline ───────────────────────────────────────────────────────────────
    tagline = "We don't predict the market. We mark the moment."
    tl_bbox = draw.textbbox((0, 0), tagline, font=fonts["tagline"])
    tl_w = tl_bbox[2] - tl_bbox[0]
    draw.text(((W - tl_w) // 2, logo_y + 56), tagline, font=fonts["tagline"], fill=C_INK_50)

    # ── Linea oro in fondo ────────────────────────────────────────────────────
    draw.rectangle([(0, H - 5), (W, H)], fill=C_GOLD)

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

    # Contenuto mock per test
    class MockContent:
        theme = "CURIOSITA_MACRO"
        headline = "Perché il dollaro sale quando c'è crisi?"
        subline = "Il paradosso del safe haven più usato al mondo."
        eyebrow = "CURIOSITÀ MACRO"
        caption = "Test caption"
        hashtags = ["macro", "kairos"]
        accent_word = "dollaro"

    with tempfile.TemporaryDirectory() as td:
        path = render_afternoon_post(MockContent(), td)
        dest = Path(__file__).parent.parent / Path(path).name
        shutil.copy(path, dest)
        print(f"\nPost generato: {dest}")
        print(f"Dimensione: {dest.stat().st_size // 1024}KB")
