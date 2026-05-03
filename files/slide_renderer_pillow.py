"""
slide_renderer_pillow.py
Renderer alternativo per Railway — usa Pillow invece di Playwright.
Nessuna dipendenza da Chromium/browser.

Usa i campi di IGCarouselContent:
  hook_title, hook_subtitle, hook_eyebrow, date_label
  context_title, context_stats (list of {value, label})
  historical_title, historical_rows (list of {label, value, positive})
  sectors_title, bullish_sectors, bearish_sectors
  cta_question, cta_body, cta_channel

Palette Kairós:
  Ink    #0E0E0C  — foreground
  Paper  #F5F2E6  — background
  Gold   #B8893B  — accento
"""

import logging
from pathlib import Path

logger = logging.getLogger("slide_renderer_pillow")

# Palette
INK      = (14, 14, 12)
PAPER    = (245, 242, 230)
GOLD     = (184, 137, 59)
GOLD_SOFT= (201, 160, 98)
INK_50   = (107, 107, 98)
INK_15   = (212, 211, 199)
POS      = (45, 95, 63)
NEG      = (140, 45, 45)

W, H = 1080, 1080


def _get_fonts():
    """Trova i font disponibili nel sistema o usa i bundled dal repo."""
    try:
        from PIL import ImageFont

        repo_root = Path(__file__).parent
        fonts_dir = repo_root / "fonts"

        candidates_serif = [
            fonts_dir / "CormorantGaramond-Medium.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        ]
        candidates_sans = [
            fonts_dir / "InterTight-Regular.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
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
            "serif_xl":  load(candidates_serif, 68),
            "serif_lg":  load(candidates_serif, 52),
            "serif_md":  load(candidates_serif, 38),
            "serif_sm":  load(candidates_serif, 28),
            "sans_lg":   load(candidates_sans, 30),
            "sans_md":   load(candidates_sans, 24),
            "sans_sm":   load(candidates_sans, 20),
            "sans_xs":   load(candidates_sans, 16),
            "mono_sm":   load(candidates_mono, 18),
            "mono_xs":   load(candidates_mono, 14),
        }
    except ImportError:
        return {}


def _wrap(draw, text, font, max_w):
    """Wrappa testo per stare in max_w px."""
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        try:
            bbox = draw.textbbox((0, 0), test, font=font)
            wide = bbox[2]
        except Exception:
            wide = len(test) * 12
        if wide <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_kairos_mark(draw, x, y, size=48, color=INK):
    """Logo K geometrico Kairós."""
    s = size / 100
    draw.rectangle([x + 20*s, y + 15*s, x + 30*s, y + 85*s], fill=color)
    draw.polygon([(x+32*s, y+50*s), (x+80*s, y+15*s), (x+80*s, y+49*s)], fill=GOLD)
    draw.polygon([(x+32*s, y+50*s), (x+80*s, y+51*s), (x+80*s, y+85*s)], fill=color)


def render_slide1(c: dict, output_path: Path, fonts: dict):
    """Slide 1 — Hook su sfondo Ink."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    # Gold bar top
    draw.rectangle([0, 0, W, 4], fill=GOLD)
    # Corner marks
    cs = 28
    for rx, ry in [(48, 48), (W-48-cs, 48), (48, H-48-cs), (W-48-cs, H-48-cs)]:
        draw.rectangle([rx, ry, rx+cs, ry+cs], outline=GOLD, width=2)

    # Wordmark
    _draw_kairos_mark(draw, 60, 60, size=48, color=PAPER)
    draw.text((118, 70), "KAIRÓS", font=fonts.get("sans_sm"), fill=PAPER)

    # Eyebrow + date
    eyebrow = c.get("eyebrow", c.get("event_category", "MACRO SIGNAL"))
    date_label = c.get("date_label", "")
    draw.text((60, 160), f"· {eyebrow} · {date_label}", font=fonts.get("mono_xs"), fill=GOLD_SOFT)
    draw.line([(60, 188), (W-60, 188)], fill=GOLD, width=1)

    # Hook title
    hook = c.get("hook_title", "")
    title_font = fonts.get("serif_lg")
    y = 218
    for line in _wrap(draw, hook, title_font, W-120)[:4]:
        draw.text((60, y), line, font=title_font, fill=PAPER)
        y += 62

    # Rule
    draw.line([(60, y+8), (W-60, y+8)], fill=GOLD_SOFT, width=1)
    y += 24

    # Hook subtitle
    sub = c.get("hook_subtitle", "")
    if sub:
        sub_font = fonts.get("sans_md")
        for line in _wrap(draw, sub, sub_font, W-120)[:3]:
            draw.text((60, y), line, font=sub_font, fill=INK_15)
            y += 34

    # Footer hint
    draw.text((60, H-80), "SCORRI PER CAPIRE L'IMPATTO  →", font=fonts.get("mono_xs"), fill=INK_50)
    draw.text((W-100, H-80), "1/5", font=fonts.get("mono_xs"), fill=INK_50)

    img.save(str(output_path), "PNG")


def render_slide2(c: dict, output_path: Path, fonts: dict):
    """Slide 2 — Contesto con stats."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 4], fill=GOLD)
    draw.rectangle([0, 0, W-1, H-1], outline=INK_15, width=1)

    _draw_kairos_mark(draw, 60, 52, size=38, color=INK)
    draw.text((108, 60), "KAIRÓS", font=fonts.get("sans_xs"), fill=INK_50)
    draw.text((W-80, 60), "2/5", font=fonts.get("mono_xs"), fill=INK_50)

    y = 136
    draw.text((60, y), "IL CONTESTO", font=fonts.get("mono_xs"), fill=GOLD)
    y += 30
    draw.line([(60, y), (W-60, y)], fill=INK, width=1)
    y += 28

    ctx_title = c.get("context_title", "Perché conta")
    for line in _wrap(draw, ctx_title, fonts.get("serif_md"), W-120)[:2]:
        draw.text((60, y), line, font=fonts.get("serif_md"), fill=INK)
        y += 48
    y += 12

    # Stats
    stats = c.get("context_stats", [])
    if isinstance(stats, list):
        for stat in stats[:3]:
            val   = stat.get("value", "—") if isinstance(stat, dict) else str(stat)
            label = stat.get("label", "")  if isinstance(stat, dict) else ""
            draw.text((60, y), str(val), font=fonts.get("serif_lg"), fill=GOLD)
            y += 58
            draw.text((60, y), str(label), font=fonts.get("sans_md"), fill=INK_50)
            y += 32
            draw.line([(60, y), (W-60, y)], fill=INK_15, width=1)
            y += 20
            if y > H - 100:
                break

    src = c.get("source_label", "")
    if src:
        draw.text((60, H-56), f"FONTE: {src}", font=fonts.get("mono_xs"), fill=INK_50)

    img.save(str(output_path), "PNG")


def render_slide3(c: dict, output_path: Path, fonts: dict):
    """Slide 3 — Storico su sfondo Ink."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 4], fill=GOLD)

    _draw_kairos_mark(draw, 60, 52, size=38, color=PAPER)
    draw.text((108, 60), "KAIRÓS", font=fonts.get("sans_xs"), fill=INK_50)
    draw.text((W-80, 60), "3/5", font=fonts.get("mono_xs"), fill=INK_50)

    y = 136
    draw.text((60, y), "STORICO", font=fonts.get("mono_xs"), fill=GOLD_SOFT)
    y += 30
    draw.line([(60, y), (W-60, y)], fill=INK_15, width=1)
    y += 28

    hist_title = c.get("historical_title", c.get("history_title", "In eventi simili"))
    for line in _wrap(draw, hist_title, fonts.get("serif_md"), W-120)[:2]:
        draw.text((60, y), line, font=fonts.get("serif_md"), fill=PAPER)
        y += 48
    y += 16

    rows = c.get("historical_rows", [])
    if isinstance(rows, list):
        for row in rows[:5]:
            if isinstance(row, dict):
                label = row.get("label", "")
                value = row.get("value", "")
                positive = row.get("positive", True)
            else:
                label, value, positive = str(row), "", True
            color = POS if positive else NEG
            draw.text((60, y), str(label), font=fonts.get("sans_md"), fill=PAPER)
            val_w = draw.textbbox((0,0), str(value), font=fonts.get("mono_sm"))[2]
            draw.text((W-60-val_w, y), str(value), font=fonts.get("mono_sm"), fill=color)
            y += 36
            draw.line([(60, y), (W-60, y)], fill=(245,242,230,30), width=1)
            y += 12
            if y > H - 100:
                break

    draw.text((W-80, H-56), "3/5", font=fonts.get("mono_xs"), fill=INK_50)

    img.save(str(output_path), "PNG")


def render_slide4(c: dict, output_path: Path, fonts: dict):
    """Slide 4 — Settori: bullish vs bearish."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 4], fill=GOLD)
    draw.rectangle([0, 0, W-1, H-1], outline=INK_15, width=1)

    _draw_kairos_mark(draw, 60, 52, size=38, color=INK)
    draw.text((108, 60), "KAIRÓS", font=fonts.get("sans_xs"), fill=INK_50)
    draw.text((W-80, 60), "4/5", font=fonts.get("mono_xs"), fill=INK_50)

    y = 136
    draw.text((60, y), "COSA TENERE D'OCCHIO", font=fonts.get("mono_xs"), fill=GOLD)
    y += 30
    draw.line([(60, y), (W-60, y)], fill=INK, width=1)
    y += 28

    sectors_title = c.get("sectors_title", "Settori coinvolti")
    for line in _wrap(draw, sectors_title, fonts.get("serif_md"), W-120)[:2]:
        draw.text((60, y), line, font=fonts.get("serif_md"), fill=INK)
        y += 48
    y += 16

    # Bullish block
    bullish = c.get("bullish_sectors", "")
    if bullish:
        draw.rectangle([60, y, W-60, y+8], fill=POS)
        y += 16
        draw.text((60, y), "POTENZIALE BENEFICIO", font=fonts.get("mono_xs"), fill=POS)
        y += 26
        for line in _wrap(draw, bullish, fonts.get("sans_md"), W-140)[:3]:
            draw.text((80, y), line, font=fonts.get("sans_md"), fill=INK)
            y += 32
        y += 20

    # Bearish block
    bearish = c.get("bearish_sectors", "")
    if bearish:
        draw.rectangle([60, y, W-60, y+8], fill=NEG)
        y += 16
        draw.text((60, y), "POTENZIALE PRESSIONE", font=fonts.get("mono_xs"), fill=NEG)
        y += 26
        for line in _wrap(draw, bearish, fonts.get("sans_md"), W-140)[:3]:
            draw.text((80, y), line, font=fonts.get("sans_md"), fill=INK)
            y += 32

    draw.text((60, H-56), "Non è consulenza finanziaria · Elaborato da IA su fonti pubbliche",
              font=fonts.get("mono_xs"), fill=INK_50)

    img.save(str(output_path), "PNG")


def render_slide5(c: dict, output_path: Path, fonts: dict):
    """Slide 5 — CTA su sfondo Gold."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), GOLD)
    draw = ImageDraw.Draw(img)

    # Struttura centrata
    _draw_kairos_mark(draw, 60, 52, size=48, color=INK)
    draw.text((118, 64), "KAIRÓS", font=fonts.get("sans_sm"), fill=INK)
    draw.text((W-80, 64), "5/5", font=fonts.get("mono_xs"), fill=INK_50)

    draw.line([(60, 130), (W-60, 130)], fill=INK, width=1)

    y = 180
    draw.text((60, y), "· KAIRÓS · IL MOMENTO OPPORTUNO", font=fonts.get("mono_xs"), fill=INK_50)
    y += 48

    cta_q = c.get("cta_question", c.get("cta_title", "Vuoi i segnali operativi?"))
    title_font = fonts.get("serif_lg")
    for line in _wrap(draw, cta_q, title_font, W-120)[:3]:
        draw.text((60, y), line, font=title_font, fill=INK)
        y += 64
    y += 16

    cta_body = c.get("cta_body", "")
    if cta_body:
        body_font = fonts.get("sans_md")
        for line in _wrap(draw, cta_body, body_font, W-120)[:4]:
            draw.text((60, y), line, font=body_font, fill=INK_50)
            y += 34
    y += 32

    # CTA button
    cta_channel = c.get("cta_channel", "@kairos.macro su Telegram")
    btn_y = H - 240
    draw.rectangle([60, btn_y, W-60, btn_y+90], outline=INK, width=2)
    ch_w = draw.textbbox((0,0), cta_channel, font=fonts.get("serif_md"))[2]
    draw.text(((W - ch_w) // 2, btn_y + 22), cta_channel, font=fonts.get("serif_md"), fill=INK)

    draw.text((60, H-56), "Generato da IA · Solo scopo informativo · Non è consulenza finanziaria",
              font=fonts.get("mono_xs"), fill=INK_50)

    img.save(str(output_path), "PNG")


def render_carousel_slides_pillow(content_dict: dict, output_dir) -> list:
    """
    Renderizza 5 slide del carosello Kairós con Pillow.
    Compatibile con i campi di IGCarouselContent.

    Returns: lista di Path ai file PNG
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("Pillow non installato")
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fonts = _get_fonts()
    signal_id = content_dict.get("signal_id", "unknown")
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(signal_id))[:40]

    renderers = [
        (f"{safe_id}_slide1_hook.png",    render_slide1),
        (f"{safe_id}_slide2_context.png", render_slide2),
        (f"{safe_id}_slide3_history.png", render_slide3),
        (f"{safe_id}_slide4_sectors.png", render_slide4),
        (f"{safe_id}_slide5_cta.png",     render_slide5),
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
    import tempfile
    logging.basicConfig(level=logging.INFO)

    mock = {
        "signal_id": "test_pillow_002",
        "eyebrow": "ENERGIA · GEOPOLITICA",
        "date_label": "03 Maggio 2026",
        "hook_title": "L'OPEC aumenta la produzione: cosa significa per i tuoi investimenti",
        "hook_subtitle": "Il cartello del petrolio sorprende i mercati con +188k barili/giorno",
        "context_title": "Perché conta",
        "context_stats": [
            {"value": "+188k", "label": "barili/giorno aggiunti da giugno"},
            {"value": "$70",   "label": "Brent sotto pressione"},
            {"value": "-8%",   "label": "calo atteso nei 30 giorni successivi"},
        ],
        "historical_title": "In eventi simili, i mercati si sono mossi così",
        "historical_rows": [
            {"label": "Petrolio (Brent)",   "value": "-8% / -15%", "positive": False},
            {"label": "Compagnie aeree",    "value": "+5% / +10%", "positive": True},
            {"label": "Petrolchimico",      "value": "-5% / -8%",  "positive": False},
            {"label": "Auto elettriche",    "value": "+3% / +7%",  "positive": True},
        ],
        "sectors_title": "Settori coinvolti",
        "bullish_sectors": "Compagnie aeree, auto elettriche, manifattura energy-intensive",
        "bearish_sectors": "Energia integrata, petrolchimico, produttori petrolio USA",
        "cta_question": "Vuoi i segnali operativi completi?",
        "cta_body": "Ticker, stop loss, target e sizing in tempo reale — solo su Telegram.",
        "cta_channel": "@kairos.macro su Telegram",
        "source_label": "Reuters · Bloomberg",
        "caption": "Test caption",
        "hashtags": ["macro", "finanza", "mercati"],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        slides = render_carousel_slides_pillow(mock, tmpdir)
        print("\n" + "=" * 50)
        print("Slide generate: " + str(len(slides)) + "/5")
        for s in slides:
            size = Path(s).stat().st_size
            print("  " + Path(s).name + ": " + str(size) + " bytes")
