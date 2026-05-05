"""
story_renderer.py
Kairós — Renderer Story Instagram (1080×1920, formato 9:16)

Layout:
  - Sfondo Ink scuro (#0E0E0C)
  - Barra gold in cima e in fondo
  - Eyebrow label centrato in alto (mono, gold soft)
  - Separatore dorato sottile
  - Headline grande centrata con accent word in Gold
  - Subline in sans sotto
  - Decorazione geometrica centrale (cerchio tratteggiato gold)
  - Logo KAIRÓS in basso + tagline
"""

import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("story_renderer")

# ─── Dimensioni ───────────────────────────────────────────────────────────────
W, H = 1080, 1920

# ─── Palette ──────────────────────────────────────────────────────────────────
INK        = (14, 14, 12)
PAPER      = (245, 242, 230)
GOLD       = (184, 137, 59)
GOLD_SOFT  = (201, 160, 98)
INK_50     = (107, 107, 98)
INK_15     = (212, 211, 199)

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
        "eyebrow":      _load_font("JetBrainsMono-Regular.ttf", 32),
        "headline":     _load_font("CormorantGaramond-SemiBold.ttf", 112),
        "headline_sm":  _load_font("CormorantGaramond-SemiBold.ttf", 84),
        "headline_xs":  _load_font("CormorantGaramond-SemiBold.ttf", 68),
        "subline":      _load_font("InterTight-Regular.ttf", 42),
        "logo":         _load_font("CormorantGaramond-SemiBold.ttf", 52),
        "tagline":      _load_font("InterTight-Regular.ttf", 28),
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _wrap_text(text: str, font, max_width: int, draw) -> list:
    words = text.split()
    lines, current = [], ""
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
    return lines or [""]


def _text_w(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0]


def _text_h(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[3] - bbox[1]


def _draw_centered(draw, text: str, font, y: int, color, max_width: int) -> int:
    """Disegna testo centrato con word-wrap. Ritorna y finale."""
    lines = _wrap_text(text, font, max_width, draw)
    for line in lines:
        w = _text_w(draw, line, font)
        x = (W - w) // 2
        draw.text((x, y), line, font=font, fill=color)
        y += _text_h(draw, line, font) + 14
    return y


def _draw_headline_accent(draw, headline: str, accent: str, font, y: int, max_width: int) -> int:
    """Headline centrata con una parola in Gold. Ritorna y finale."""
    if not accent or accent.lower() not in headline.lower():
        return _draw_centered(draw, headline, font, y, PAPER, max_width)

    lines = _wrap_text(headline, font, max_width, draw)
    for line in lines:
        lw = _text_w(draw, line, font)
        lh = _text_h(draw, line, font)
        x_start = (W - lw) // 2
        lower_line = line.lower()
        lower_accent = accent.lower()

        if lower_accent in lower_line:
            idx = lower_line.find(lower_accent)
            before = line[:idx]
            accent_part = line[idx:idx+len(accent)]
            after = line[idx+len(accent):]
            x = x_start
            if before:
                draw.text((x, y), before, font=font, fill=PAPER)
                x += _text_w(draw, before, font)
            draw.text((x, y), accent_part, font=font, fill=GOLD)
            x += _text_w(draw, accent_part, font)
            if after:
                draw.text((x, y), after, font=font, fill=PAPER)
        else:
            draw.text((x_start, y), line, font=font, fill=PAPER)

        y += lh + 16
    return y


def _draw_dashed_circle(draw, cx: int, cy: int, r: int, color, segments: int = 24, dash: int = 8):
    """Cerchio tratteggiato decorativo."""
    import math
    step = 2 * math.pi / segments
    for i in range(segments):
        if i % 2 == 0:
            a1 = i * step
            a2 = a1 + step * (dash / 10)
            x1 = cx + r * math.cos(a1)
            y1 = cy + r * math.sin(a1)
            x2 = cx + r * math.cos(a2)
            y2 = cy + r * math.sin(a2)
            draw.line([(x1, y1), (x2, y2)], fill=color, width=2)


# ─── Renderer principale ──────────────────────────────────────────────────────

def render_story(content, output_dir: str) -> str:
    """
    Renderizza la Story come PNG 1080×1920.

    Args:
        content: StoryContent (o dict con stessi campi)
        output_dir: directory dove salvare il PNG

    Returns:
        Percorso assoluto al file PNG generato
    """
    if hasattr(content, "__dict__"):
        c = content.__dict__
    else:
        c = dict(content)

    theme     = c.get("theme", "DATO_MACRO")
    headline  = c.get("headline", "")
    subline   = c.get("subline", "")
    eyebrow   = c.get("eyebrow", "")
    accent    = c.get("accent_word", "")

    fonts = _get_fonts()

    # ── Canvas ────────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    MARGIN = 90
    CONTENT_W = W - MARGIN * 2

    # ── Barra gold in cima ────────────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, 6)], fill=GOLD)

    # ── Eyebrow ───────────────────────────────────────────────────────────────
    eyebrow_y = 100
    eb_text = eyebrow.upper()
    eb_w = _text_w(draw, eb_text, fonts["eyebrow"])
    draw.text(((W - eb_w) // 2, eyebrow_y), eb_text, font=fonts["eyebrow"], fill=GOLD_SOFT)

    # ── Separatore sottile ────────────────────────────────────────────────────
    sep_y = eyebrow_y + 56
    draw.rectangle([(W//2 - 50, sep_y), (W//2 + 50, sep_y + 1)], fill=INK_50)

    # ── Cerchio decorativo centrale (dietro il testo) ─────────────────────────
    circle_cx = W // 2
    circle_cy = H // 2
    _draw_dashed_circle(draw, circle_cx, circle_cy, 320, GOLD_SOFT, segments=32, dash=6)
    # Cerchio interno più piccolo
    draw.ellipse(
        [(circle_cx - 8, circle_cy - 8), (circle_cx + 8, circle_cy + 8)],
        fill=GOLD
    )

    # ── Headline ──────────────────────────────────────────────────────────────
    # Scegli font in base alla lunghezza
    if len(headline) <= 30:
        hl_font = fonts["headline"]
        line_spacing = 20
    elif len(headline) <= 50:
        hl_font = fonts["headline_sm"]
        line_spacing = 16
    else:
        hl_font = fonts["headline_xs"]
        line_spacing = 14

    # Calcola altezza totale headline per centratura verticale
    hl_lines = _wrap_text(headline, hl_font, CONTENT_W, draw)
    hl_total_h = sum(_text_h(draw, l, hl_font) + line_spacing for l in hl_lines)

    # Subline height
    sub_lines = _wrap_text(subline, fonts["subline"], CONTENT_W, draw) if subline else []
    sub_total_h = sum(_text_h(draw, l, fonts["subline"]) + 14 for l in sub_lines) if sub_lines else 0

    # Centro verticale considerando headline + subline
    block_h = hl_total_h + (40 + sub_total_h if subline else 0)
    headline_y = H // 2 - block_h // 2

    headline_end_y = _draw_headline_accent(draw, headline, accent, hl_font, headline_y, CONTENT_W)

    # ── Subline ───────────────────────────────────────────────────────────────
    if subline:
        sub_y = headline_end_y + 36
        _draw_centered(draw, subline, fonts["subline"], sub_y, INK_15, CONTENT_W)

    # ── Tre punti gold (decorazione bassa) ───────────────────────────────────
    dot_y = H - 280
    for i, dx in enumerate([-20, 0, 20]):
        dot_x = W // 2 + dx
        r = 4
        draw.ellipse(
            [(dot_x - r, dot_y - r), (dot_x + r, dot_y + r)],
            fill=GOLD if i == 1 else INK_50
        )

    # ── Logo KAIRÓS ───────────────────────────────────────────────────────────
    logo_text = "KAIRÓS"
    logo_w = _text_w(draw, logo_text, fonts["logo"])
    logo_y = H - 230
    draw.text(((W - logo_w) // 2, logo_y), logo_text, font=fonts["logo"], fill=PAPER)

    # ── Tagline ───────────────────────────────────────────────────────────────
    tagline = "We don't predict the market. We mark the moment."
    tl_w = _text_w(draw, tagline, fonts["tagline"])
    draw.text(((W - tl_w) // 2, logo_y + 66), tagline, font=fonts["tagline"], fill=INK_50)

    # ── Barra gold in fondo ───────────────────────────────────────────────────
    draw.rectangle([(0, H - 6), (W, H)], fill=GOLD)

    # ── Salva ─────────────────────────────────────────────────────────────────
    filename = f"story_{theme.lower()}.png"
    out_path = Path(output_dir) / filename
    img.save(str(out_path), "PNG", quality=95)
    logger.info(f"story_renderer: salvato {out_path} ({out_path.stat().st_size // 1024}KB)")

    return str(out_path)


# ─── Test standalone ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    class MockContent:
        theme = "PAROLA_DEL_GIORNO"
        eyebrow = "📖 PAROLA DEL GIORNO"
        headline = "Contango"
        subline = "Quando il prezzo futuro supera quello spot — il mercato prezza scarsità futura."
        accent_word = "Contango"
        caption = "Test caption"
        hashtags = ["finanza", "kairos"]

    with tempfile.TemporaryDirectory() as td:
        path = render_story(MockContent(), td)
        dest = Path(__file__).parent.parent / Path(path).name
        shutil.copy(path, dest)
        print(f"\nStory generata: {dest}")
        print(f"Dimensione: {dest.stat().st_size // 1024}KB")
