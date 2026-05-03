"""
slide_renderer_pillow.py
Renderer alternativo per Railway — usa Pillow invece di Playwright.
Nessuna dipendenza da Chromium/browser.

Palette Kairós:
  Ink    #0E0E0C  — foreground
  Paper  #F5F2E6  — background
  Gold   #B8893B  — accento
"""

import logging
import os
import textwrap
from pathlib import Path
from typing import Optional

logger = logging.getLogger("slide_renderer_pillow")

# Palette
INK   = (14, 14, 12)
PAPER = (245, 242, 230)
GOLD  = (184, 137, 59)
GOLD_SOFT = (201, 160, 98)
INK_50 = (107, 107, 98)
INK_15 = (212, 211, 199)

W, H = 1080, 1080


def _get_fonts():
    """Trova i font disponibili nel sistema o usa i bundled dal repo."""
    try:
        from PIL import ImageFont

        # Percorsi possibili per i font bundled nel repo
        repo_root = Path(__file__).parent
        fonts_dir = repo_root / "fonts"

        candidates_serif = [
            fonts_dir / "CormorantGaramond-Medium.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
            Path("/System/Library/Fonts/Times.ttc"),
            Path("C:/Windows/Fonts/times.ttf"),
        ]
        candidates_sans = [
            fonts_dir / "InterTight-Regular.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
        candidates_serif_italic = [
            fonts_dir / "CormorantGaramond-MediumItalic.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
        ]
        candidates_mono = [
            fonts_dir / "JetBrainsMono-Regular.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
        ]

        def load(candidates, size):
            for p in candidates:
                if Path(p).exists():
                    try:
                        return ImageFont.truetype(str(p), size)
                    except Exception:
                        continue
            return ImageFont.load_default()

        return {
            "serif_xl":    load(candidates_serif, 72),
            "serif_lg":    load(candidates_serif, 52),
            "serif_md":    load(candidates_serif, 38),
            "serif_sm":    load(candidates_serif, 28),
            "serif_italic":load(candidates_serif_italic, 34),
            "sans_md":     load(candidates_sans, 26),
            "sans_sm":     load(candidates_sans, 20),
            "sans_xs":     load(candidates_sans, 16),
            "mono_sm":     load(candidates_mono, 18),
            "mono_xs":     load(candidates_mono, 14),
        }
    except ImportError:
        return {}


def _draw_slide_base(draw, img, bg=PAPER, border_color=INK):
    """Background + bordo Kairós."""
    from PIL import ImageDraw
    img.paste(bg, [0, 0, W, H])
    # Bordo sottile
    draw.rectangle([0, 0, W-1, H-1], outline=border_color, width=2)
    # Gold bar top
    draw.rectangle([0, 0, W, 4], fill=GOLD)


def _wrap_text(text, font, max_width, draw):
    """Wrappa il testo per stare dentro max_width px."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_eyebrow(draw, text, y, fonts, color=GOLD):
    """Etichetta piccola uppercase mono."""
    font = fonts.get("mono_xs")
    draw.text((60, y), text.upper(), font=font, fill=color)
    return y + 24


def _draw_rule(draw, y, color=INK_15, margin=60):
    """Linea orizzontale sottile."""
    draw.line([(margin, y), (W - margin, y)], fill=color, width=1)
    return y + 1


def _draw_kairos_mark(draw, x, y, size=48, color=INK):
    """Logo K geometrico Kairós."""
    s = size / 100
    # Stelo verticale
    draw.rectangle(
        [x + 20*s, y + 15*s, x + 30*s, y + 85*s],
        fill=color
    )
    # Cuneo superiore
    draw.polygon(
        [(x + 32*s, y + 50*s), (x + 80*s, y + 15*s), (x + 80*s, y + 49*s)],
        fill=GOLD
    )
    # Cuneo inferiore
    draw.polygon(
        [(x + 32*s, y + 50*s), (x + 80*s, y + 51*s), (x + 80*s, y + 85*s)],
        fill=color
    )


def render_slide1(content_dict: dict, output_path: Path, fonts: dict):
    """Slide 1 — Hook title su sfondo Ink."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    # Gold bar top
    draw.rectangle([0, 0, W, 4], fill=GOLD)
    # Bordo angoli gold
    c = 32
    for rx, ry in [(48, 48), (W-48-c, 48), (48, H-48-c), (W-48-c, H-48-c)]:
        draw.rectangle([rx, ry, rx+c, ry+c], outline=GOLD, width=2)

    # Logo mark + wordmark
    _draw_kairos_mark(draw, 60, 60, size=52, color=PAPER)
    wm_font = fonts.get("sans_sm")
    draw.text((122, 72), "KAIRÓS", font=wm_font, fill=PAPER)

    # Eyebrow
    eyebrow_font = fonts.get("mono_xs")
    date_label = content_dict.get("date_label", "")
    draw.text((60, 160), f"· MACRO SIGNAL · {date_label}", font=eyebrow_font, fill=GOLD_SOFT)
    draw.line([(60, 185), (W-60, 185)], fill=GOLD, width=1)

    # Hook title — grande serif
    hook = content_dict.get("hook_title", "")
    title_font = fonts.get("serif_lg")
    lines = _wrap_text(hook, title_font, W - 120, draw)
    y = 220
    for line in lines[:4]:
        draw.text((60, y), line, font=title_font, fill=PAPER)
        y += 64

    # Hook subtitle
    sub = content_dict.get("hook_subtitle", "")
    if sub:
        sub_font = fonts.get("sans_md")
        sub_lines = _wrap_text(sub, sub_font, W - 120, draw)
        y += 16
        for line in sub_lines[:3]:
            draw.text((60, y), line, font=sub_font, fill=INK_15)
            y += 36

    # Footer
    draw.line([(60, H-100), (W-60, H-100)], fill=GOLD, width=1)
    footer_font = fonts.get("mono_xs")
    draw.text((60, H-80), "SEGUI @karios_finance PER I SEGNALI DI OGGI", font=footer_font, fill=INK_50)

    img.save(str(output_path), "PNG")


def render_slide2(content_dict: dict, output_path: Path, fonts: dict):
    """Slide 2 — Context su sfondo Paper."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)
    _draw_slide_base(draw, img)

    y = 60
    _draw_kairos_mark(draw, 60, y, size=40, color=INK)
    draw.text((112, y+6), "KAIRÓS", font=fonts.get("sans_xs"), fill=INK_50)

    y = 140
    draw.text((60, y), "02 / CONTESTO", font=fonts.get("mono_xs"), fill=GOLD)
    y += 32
    draw.line([(60, y), (W-60, y)], fill=INK, width=1)
    y += 32

    context_title = content_dict.get("context_title", "Contesto macroeconomico")
    draw.text((60, y), context_title, font=fonts.get("serif_md"), fill=INK)
    y += 56

    context_body = content_dict.get("context_body", "")
    body_font = fonts.get("sans_md")
    for para in context_body.split("\n")[:6]:
        lines = _wrap_text(para.strip(), body_font, W - 120, draw)
        for line in lines:
            draw.text((60, y), line, font=body_font, fill=INK)
            y += 34
        y += 8
        if y > H - 140:
            break

    draw.line([(60, H-80), (W-60, H-80)], fill=INK_15, width=1)
    draw.text((60, H-60), "→ CONTINUA", font=fonts.get("mono_xs"), fill=GOLD)

    img.save(str(output_path), "PNG")


def render_slide3(content_dict: dict, output_path: Path, fonts: dict):
    """Slide 3 — Historical precedents su sfondo Paper."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)
    _draw_slide_base(draw, img)

    y = 60
    _draw_kairos_mark(draw, 60, y, size=40, color=INK)
    draw.text((112, y+6), "KAIRÓS", font=fonts.get("sans_xs"), fill=INK_50)

    y = 140
    draw.text((60, y), "03 / PRECEDENTI STORICI", font=fonts.get("mono_xs"), fill=GOLD)
    y += 32
    draw.line([(60, y), (W-60, y)], fill=INK, width=1)
    y += 32

    hist_title = content_dict.get("history_title", "Cosa ci dice la storia")
    draw.text((60, y), hist_title, font=fonts.get("serif_md"), fill=INK)
    y += 56

    hist_body = content_dict.get("history_body", "")
    body_font = fonts.get("sans_md")
    for para in hist_body.split("\n")[:6]:
        lines = _wrap_text(para.strip(), body_font, W - 120, draw)
        for line in lines:
            draw.text((60, y), line, font=body_font, fill=INK)
            y += 34
        y += 8
        if y > H - 140:
            break

    draw.line([(60, H-80), (W-60, H-80)], fill=INK_15, width=1)
    draw.text((60, H-60), "→ CONTINUA", font=fonts.get("mono_xs"), fill=GOLD)

    img.save(str(output_path), "PNG")


def render_slide4(content_dict: dict, output_path: Path, fonts: dict):
    """Slide 4 — Settori e asset impattati."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)
    _draw_slide_base(draw, img)

    y = 60
    _draw_kairos_mark(draw, 60, y, size=40, color=INK)
    draw.text((112, y+6), "KAIRÓS", font=fonts.get("sans_xs"), fill=INK_50)

    y = 140
    draw.text((60, y), "04 / ASSET & SETTORI", font=fonts.get("mono_xs"), fill=GOLD)
    y += 32
    draw.line([(60, y), (W-60, y)], fill=INK, width=1)
    y += 32

    sectors_title = content_dict.get("sectors_title", "Chi viene impattato")
    draw.text((60, y), sectors_title, font=fonts.get("serif_md"), fill=INK)
    y += 56

    sectors = content_dict.get("sectors_list", [])
    if isinstance(sectors, str):
        sectors = [s.strip() for s in sectors.split(",") if s.strip()]

    item_font = fonts.get("sans_md")
    mono_font = fonts.get("mono_xs")
    for sector in sectors[:6]:
        draw.rectangle([60, y, 68, y+26], fill=GOLD)
        draw.text((84, y), sector, font=item_font, fill=INK)
        y += 44
        if y > H - 140:
            break

    sectors_body = content_dict.get("sectors_body", "")
    if sectors_body and y < H - 200:
        y += 16
        draw.line([(60, y), (W-60, y)], fill=INK_15, width=1)
        y += 20
        for line in _wrap_text(sectors_body, item_font, W-120, draw)[:3]:
            draw.text((60, y), line, font=item_font, fill=INK_50)
            y += 34

    draw.line([(60, H-80), (W-60, H-80)], fill=INK_15, width=1)
    draw.text((60, H-60), "→ CONTINUA", font=mono_font, fill=GOLD)

    img.save(str(output_path), "PNG")


def render_slide5(content_dict: dict, output_path: Path, fonts: dict):
    """Slide 5 — CTA finale su sfondo Ink."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 4], fill=GOLD)
    c = 32
    for rx, ry in [(48, 48), (W-48-c, 48), (48, H-48-c), (W-48-c, H-48-c)]:
        draw.rectangle([rx, ry, rx+c, ry+c], outline=GOLD, width=2)

    _draw_kairos_mark(draw, 60, 60, size=52, color=PAPER)
    draw.text((122, 72), "KAIRÓS", font=fonts.get("sans_sm"), fill=PAPER)

    y = 280
    draw.text((60, y), "05 / COSA FARE ORA", font=fonts.get("mono_xs"), fill=GOLD_SOFT)
    y += 40
    draw.line([(60, y), (W-60, y)], fill=GOLD, width=1)
    y += 48

    cta_title = content_dict.get("cta_title", "Il momento giusto è adesso")
    title_font = fonts.get("serif_lg")
    for line in _wrap_text(cta_title, title_font, W-120, draw)[:3]:
        draw.text((60, y), line, font=title_font, fill=PAPER)
        y += 66

    y += 24
    cta_body = content_dict.get("cta_body", "")
    body_font = fonts.get("sans_md")
    for line in _wrap_text(cta_body, body_font, W-120, draw)[:4]:
        draw.text((60, y), line, font=body_font, fill=INK_15)
        y += 36

    # CTA box
    y = H - 220
    draw.rectangle([60, y, W-60, y+120], outline=GOLD, width=2)
    draw.text((80, y+20), "SEGUI PER I SEGNALI DI OGNI MATTINA", font=fonts.get("mono_xs"), fill=GOLD)
    draw.text((80, y+52), "@karios_finance", font=fonts.get("serif_md"), fill=PAPER)
    draw.text((80, y+90), "il canale Telegram @kairos.macro", font=fonts.get("sans_xs"), fill=INK_15)

    img.save(str(output_path), "PNG")


def render_carousel_slides_pillow(content_dict: dict, output_dir) -> list:
    """
    Renderizza 5 slide del carosello Kairós con Pillow.
    Fallback senza Playwright/Chromium — funziona su Railway.

    Returns: lista di Path ai file PNG
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("Pillow non installato. pip install Pillow")
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fonts = _get_fonts()
    signal_id = content_dict.get("signal_id", "unknown")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(signal_id))[:40]

    renderers = [
        (f"{safe_id}_slide1_hook.png",     render_slide1),
        (f"{safe_id}_slide2_context.png",  render_slide2),
        (f"{safe_id}_slide3_history.png",  render_slide3),
        (f"{safe_id}_slide4_sectors.png",  render_slide4),
        (f"{safe_id}_slide5_cta.png",      render_slide5),
    ]

    output_paths = []
    for filename, renderer in renderers:
        out_path = output_dir / filename
        try:
            renderer(content_dict, out_path, fonts)
            output_paths.append(out_path)
            logger.info(f"  Slide Pillow OK: {filename}")
        except Exception as e:
            logger.error(f"  Errore slide {filename}: {e}", exc_info=True)

    logger.info(f"Renderizzate {len(output_paths)}/5 slide con Pillow in {output_dir}")
    return output_paths


# ─── Test ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys, tempfile
    logging.basicConfig(level=logging.INFO)

    mock = {
        "signal_id": "test_pillow_001",
        "date_label": "03 Maggio 2026",
        "hook_title": "L'OPEC aumenta la produzione: cosa significa per i tuoi investimenti",
        "hook_subtitle": "Il cartello del petrolio sorprende i mercati con +188k barili/giorno",
        "context_title": "Il contesto macroeconomico",
        "context_body": "L'OPEC+ ha deciso di accelerare il ritmo di ripristino della produzione, aggiungendo 188.000 barili al giorno a partire da giugno.\n\nLa mossa arriva in un contesto di prezzi del petrolio già sotto pressione, con il Brent che tratta intorno a $70.",
        "history_title": "I precedenti storici",
        "history_body": "Nelle ultime tre occasioni in cui l'OPEC ha aumentato la produzione in modo significativo, il prezzo del petrolio è sceso del 8-15% nei 30 giorni successivi.\n\nI settori più colpiti sono stati energia e trasporti.",
        "sectors_title": "Chi viene impattato",
        "sectors_list": ["Energia (Short)", "Compagnie aeree (Long)", "Petrolchimico (Short)", "Auto elettriche (Long)"],
        "sectors_body": "I produttori di petrolio registrano pressione ribassista mentre i consumatori di energia beneficiano.",
        "cta_title": "Il momento giusto per agire",
        "cta_body": "Monitora i livelli chiave di WTI e Brent. Un break sotto $68 apre spazio a ulteriori ribassi.",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        slides = render_carousel_slides_pillow(mock, tmpdir)
        print(f"\n{'='*50}")
        print(f"Slide generate: {len(slides)}/5")
        for s in slides:
            size = Path(s).stat().st_size
            print(f"  {Path(s).name}: {size:,} bytes")

        # Copia per visualizzazione
        import shutil
        out_dir = Path("/sessions/great-pensive-carson/mnt/macro-signal-tool")
        for s in slides:
            dest = out_dir / Path(s).name
            shutil.copy(s, dest)
            print(f"  Copiato: {dest}")
