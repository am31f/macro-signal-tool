"""
story_renderer.py
Kairós — Renderer Story Instagram (1080×1920, formato 9:16)

Layout editoriale su sfondo Paper (#F5F2E6):
  - Barra gold sottile in cima (6px)
  - Eyebrow label centrato — mono gold
  - Linea separatrice gold sottile
  - Headline dominante centrata — Cormorant Garamond grande, accent word in Gold
  - Subline in Inter sotto
  - Tre punti gold come separatore decorativo minimo
  - Logo KAIRÓS in Ink + tagline in INK_50
  - Barra gold sottile in fondo (6px)
"""

import logging
import tempfile
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("story_renderer")

# ─── Dimensioni ───────────────────────────────────────────────────────────────
W, H = 1080, 1920

# ─── Palette ──────────────────────────────────────────────────────────────────
INK       = (14, 14, 12)
INK_70    = (58, 58, 53)
INK_50    = (107, 107, 98)
INK_15    = (212, 211, 199)
PAPER     = (245, 242, 230)
GOLD      = (184, 137, 59)
GOLD_SOFT = (201, 160, 98)
GOLD_DEEP = (140, 102, 36)

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
        "eyebrow":      _load_font("JetBrainsMono-Regular.ttf", 34),
        "headline":     _load_font("CormorantGaramond-Medium.ttf", 128),
        "headline_sm":  _load_font("CormorantGaramond-Medium.ttf", 100),
        "headline_xs":  _load_font("CormorantGaramond-Medium.ttf", 80),
        "subline":      _load_font("Inter-Regular.ttf", 44),
        "logo":         _load_font("CormorantGaramond-Medium.ttf", 56),
        "tagline":      _load_font("Inter-Regular.ttf", 30),
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


def _draw_centered(draw, text: str, font, y: int, color, max_width: int, line_gap: int = 16) -> int:
    """Disegna testo centrato con word-wrap. Ritorna y finale."""
    lines = _wrap_text(text, font, max_width, draw)
    for line in lines:
        w = _text_w(draw, line, font)
        x = (W - w) // 2
        draw.text((x, y), line, font=font, fill=color)
        y += _text_h(draw, line, font) + line_gap
    return y


def _draw_headline_accent(draw, headline: str, accent: str, font, y: int, max_width: int, line_gap: int = 20) -> int:
    """Headline centrata con una parola in Gold. Ritorna y finale."""
    if not accent or accent.lower() not in headline.lower():
        return _draw_centered(draw, headline, font, y, INK, max_width, line_gap)

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
                draw.text((x, y), before, font=font, fill=INK)
                x += _text_w(draw, before, font)
            draw.text((x, y), accent_part, font=font, fill=GOLD)
            x += _text_w(draw, accent_part, font)
            if after:
                draw.text((x, y), after, font=font, fill=INK)
        else:
            draw.text((x_start, y), line, font=font, fill=INK)

        y += lh + line_gap
    return y


# ─── Renderer principale ──────────────────────────────────────────────────────

def render_story(content, output_dir: str) -> str:
    """
    Renderizza la Story come PNG 1080×1920 su sfondo Paper.

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

    theme    = c.get("theme", "DATO_MACRO")
    headline = c.get("headline", "")
    subline  = c.get("subline", "")
    eyebrow  = c.get("eyebrow", "")
    accent   = c.get("accent_word", "")

    fonts = _get_fonts()

    # ── Canvas Paper ──────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    MARGIN    = 96
    CONTENT_W = W - MARGIN * 2

    # ── Barra gold in cima (6px) ──────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, 6)], fill=GOLD)

    # ── Eyebrow label ─────────────────────────────────────────────────────────
    eyebrow_y = 110
    eb_text = eyebrow.upper()
    eb_w = _text_w(draw, eb_text, fonts["eyebrow"])
    draw.text(((W - eb_w) // 2, eyebrow_y), eb_text, font=fonts["eyebrow"], fill=GOLD_DEEP)

    # ── Linea separatrice gold ────────────────────────────────────────────────
    sep_y = eyebrow_y + 60
    draw.rectangle([(W//2 - 60, sep_y), (W//2 + 60, sep_y + 2)], fill=GOLD)

    # ── Headline — posizionata nel terzo superiore-centrale ───────────────────
    # Scegli font in base alla lunghezza headline
    if len(headline) <= 25:
        hl_font   = fonts["headline"]
        line_gap  = 24
    elif len(headline) <= 45:
        hl_font   = fonts["headline_sm"]
        line_gap  = 20
    else:
        hl_font   = fonts["headline_xs"]
        line_gap  = 16

    # Calcola altezza blocco headline + subline
    hl_lines    = _wrap_text(headline, hl_font, CONTENT_W, draw)
    hl_total_h  = sum(_text_h(draw, l, hl_font) + line_gap for l in hl_lines)

    sub_lines   = _wrap_text(subline, fonts["subline"], CONTENT_W, draw) if subline else []
    sub_total_h = sum(_text_h(draw, l, fonts["subline"]) + 14 for l in sub_lines) + 60 if sub_lines else 0

    block_h = hl_total_h + sub_total_h

    # Posizioni fisse logo e linea separatrice in basso
    logo_y   = H - 220
    line_y   = H - 290

    # Centra il blocco testo esattamente tra il separatore e la linea logo
    avail_top    = sep_y + 60
    avail_bottom = line_y - 60
    avail_h      = avail_bottom - avail_top

    # Posiziona a un terzo dall'alto — regola tipografica per formati verticali
    headline_y = avail_top + (avail_h - block_h) // 3
    headline_y = max(avail_top, headline_y)

    headline_end_y = _draw_headline_accent(
        draw, headline, accent, hl_font, headline_y, CONTENT_W, line_gap
    )

    # ── Subline ───────────────────────────────────────────────────────────────
    if subline:
        sub_y = headline_end_y + 48
        _draw_centered(draw, subline, fonts["subline"], sub_y, INK_50, CONTENT_W, line_gap=14)

    # ── Linea orizzontale sottile sopra logo ──────────────────────────────────
    draw.rectangle([(MARGIN, line_y), (W - MARGIN, line_y + 1)], fill=INK_15)

    # ── Tre punti decorativi gold ─────────────────────────────────────────────
    dot_y = line_y + 30
    for i, dx in enumerate([-18, 0, 18]):
        dot_x = W // 2 + dx
        r = 4
        draw.ellipse(
            [(dot_x - r, dot_y - r), (dot_x + r, dot_y + r)],
            fill=GOLD if i == 1 else INK_15
        )

    # ── Logo KAIRÓS in Ink ────────────────────────────────────────────────────
    logo_text = "KAIRÓS"
    logo_w    = _text_w(draw, logo_text, fonts["logo"])
    draw.text(((W - logo_w) // 2, logo_y), logo_text, font=fonts["logo"], fill=INK)

    # ── Tagline ───────────────────────────────────────────────────────────────
    tagline = "We don't predict the market. We mark the moment."
    tl_w    = _text_w(draw, tagline, fonts["tagline"])
    draw.text(((W - tl_w) // 2, logo_y + 68), tagline, font=fonts["tagline"], fill=INK_50)

    # ── Barra gold in fondo (6px) ─────────────────────────────────────────────
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
        theme        = "LO_SAPEVI_CHE"
        eyebrow      = "💡 LO SAPEVI CHE..."
        headline     = "Nel 1998 la Bank of England fece l'opposto di tutti"
        subline      = "Vendette oro ai minimi storici. Oggi vale 5 volte di più."
        accent_word  = "1998"
        caption      = "Test caption"
        hashtags     = ["finanza", "kairos"]

    with tempfile.TemporaryDirectory() as td:
        path = render_story(MockContent(), td)
        dest = Path(__file__).parent.parent / Path(path).name
        shutil.copy(path, dest)
        print(f"\nStory generata: {dest}")
        print(f"Dimensione: {dest.stat().st_size // 1024}KB")
