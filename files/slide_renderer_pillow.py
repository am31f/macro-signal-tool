"""
slide_renderer_pillow.py — Kairós · Phase 8.3
Renderer PNG 1080x1080 con Pillow. Nessuna dipendenza browser.
Font bundlati in files/fonts/ (Cormorant Garamond, Inter, JetBrains Mono).
"""

import logging
from pathlib import Path

logger = logging.getLogger("slide_renderer_pillow")

# ── Palette ───────────────────────────────────────────────────────────────────
INK       = (14, 14, 12)
INK_70    = (58, 58, 53)
INK_50    = (107, 107, 98)
INK_15    = (212, 211, 199)
PAPER     = (245, 242, 230)
PAPER_DEEP= (236, 232, 215)
GOLD      = (184, 137, 59)
GOLD_SOFT = (201, 160, 98)
GOLD_DEEP = (140, 102, 36)
POS       = (45, 95, 63)   # verde standard — usato SEMPRE per positivo
NEG       = (140, 45, 45)  # rosso standard — usato SEMPRE per negativo
POS_BG    = (28, 52, 36)   # bg riga positiva (slide 3)
NEG_BG    = (62, 24, 24)   # bg riga negativa (slide 3)

W, H = 1080, 1080
PAD = 72   # padding laterale


# ── Font loader ───────────────────────────────────────────────────────────────

def _load_fonts():
    from PIL import ImageFont
    d = Path(__file__).parent / "fonts"

    def ttf(name, size):
        p = d / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
        return ImageFont.load_default()

    serif   = "CormorantGaramond-Medium.ttf"
    serif_r = "CormorantGaramond-Regular.ttf"
    italic  = "CormorantGaramond-Italic.ttf"
    sans    = "Inter-Regular.ttf"
    mono    = "JetBrainsMono-Regular.ttf"

    return {
        # serif titoli
        "serif_80":  ttf(serif, 80),
        "serif_72":  ttf(serif, 72),
        "serif_60":  ttf(serif, 60),
        "serif_52":  ttf(serif, 52),
        "serif_44":  ttf(serif, 44),
        "serif_36":  ttf(serif, 36),
        "serif_28":  ttf(serif, 28),
        "italic_36": ttf(italic, 36),
        # sans body
        "sans_30":   ttf(sans, 30),
        "sans_26":   ttf(sans, 26),
        "sans_22":   ttf(sans, 22),
        "sans_18":   ttf(sans, 18),
        # mono etichette
        "mono_20":   ttf(mono, 20),
        "mono_16":   ttf(mono, 16),
        "mono_13":   ttf(mono, 13),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wrap(draw, text, font, max_w):
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        try:
            wide = draw.textbbox((0, 0), test, font=font)[2]
        except Exception:
            wide = len(test) * 14
        if wide <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _text_w(draw, text, font):
    try:
        return draw.textbbox((0, 0), str(text), font=font)[2]
    except Exception:
        return len(str(text)) * 14


def _line_h(draw, font):
    try:
        return draw.textbbox((0, 0), "Ag", font=font)[3]
    except Exception:
        return 20


def _hline(draw, y, color=INK_15, x0=PAD, x1=W-PAD, w=1):
    draw.line([(x0, y), (x1, y)], fill=color, width=w)


def _gold_bar(draw):
    draw.rectangle([0, 0, W, 5], fill=GOLD)


def _wordmark(draw, fonts, color_mark=INK, color_text=INK):
    """K mark + KAIRÓS a sinistra, numero slide a destra."""
    s = 44
    x, y = PAD, 48
    # Stelo K
    draw.rectangle([x+int(20*s/100), y+int(15*s/100),
                    x+int(30*s/100), y+int(85*s/100)], fill=color_mark)
    # Cuneo superiore (gold sempre)
    draw.polygon([(x+int(32*s/100), y+int(50*s/100)),
                  (x+int(80*s/100), y+int(15*s/100)),
                  (x+int(80*s/100), y+int(49*s/100))], fill=GOLD)
    # Cuneo inferiore
    draw.polygon([(x+int(32*s/100), y+int(50*s/100)),
                  (x+int(80*s/100), y+int(51*s/100)),
                  (x+int(80*s/100), y+int(85*s/100))], fill=color_mark)
    draw.text((x + s + 10, y + 8), "KAIRÓS", font=fonts["sans_22"], fill=color_text)


def _page_num(draw, fonts, n, color=INK_50):
    txt = f"{n} / 5"
    w = _text_w(draw, txt, fonts["mono_16"])
    draw.text((W - PAD - w, 56), txt, font=fonts["mono_16"], fill=color)


# ── Slide 1 — Hook ────────────────────────────────────────────────────────────

def render_slide1(c, out, fonts):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    _gold_bar(draw)
    _wordmark(draw, fonts, color_mark=PAPER, color_text=PAPER)
    _page_num(draw, fonts, 1, color=INK_50)

    # Eyebrow
    eyebrow = c.get("eyebrow", c.get("event_category", "MACRO · SIGNAL"))
    date    = c.get("date_label", "")
    y = 150
    draw.text((PAD, y), f"· {eyebrow}  ·  {date}",
              font=fonts["mono_16"], fill=GOLD_SOFT)
    y += 28
    _hline(draw, y, GOLD, w=1)
    y += 36

    # Titolo hook — grande, serif
    hook = c.get("hook_title", "")
    tf   = fonts["serif_72"]
    lh   = 82
    for line in _wrap(draw, hook, tf, W - PAD*2)[:3]:
        draw.text((PAD, y), line, font=tf, fill=PAPER)
        y += lh

    y += 12
    _hline(draw, y, GOLD_SOFT, w=1)
    y += 28

    # Sottotitolo
    sub = c.get("hook_subtitle", "")
    if sub:
        sf = fonts["sans_26"]
        for line in _wrap(draw, sub, sf, W - PAD*2)[:3]:
            draw.text((PAD, y), line, font=sf, fill=INK_15)
            y += 38
        y += 16

    # Tesi macro (causal_chain o context_body)
    causal = c.get("causal_chain", "")
    if causal and y < H - 220:
        y += 8
        for line in _wrap(draw, causal, fonts["sans_22"], W - PAD*2)[:4]:
            draw.text((PAD, y), line, font=fonts["sans_22"], fill=INK_50)
            y += 32

    # Footer
    _hline(draw, H-90, GOLD, x0=PAD, x1=W-PAD, w=1)
    draw.text((PAD, H-72), "SCORRI PER CAPIRE L'IMPATTO  →",
              font=fonts["mono_16"], fill=INK_50)

    img.save(str(out), "PNG")


# ── Slide 2 — Contesto ───────────────────────────────────────────────────────

def render_slide2(c, out, fonts):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    _gold_bar(draw)
    _wordmark(draw, fonts)
    _page_num(draw, fonts, 2)

    y = 138
    draw.text((PAD, y), "IL CONTESTO", font=fonts["mono_16"], fill=GOLD_DEEP)
    y += 26
    _hline(draw, y, INK, w=2)
    y += 32

    # Titolo contesto
    ctx_title = c.get("context_title", "Perché conta")
    tf = fonts["serif_52"]
    for line in _wrap(draw, ctx_title, tf, W - PAD*2)[:2]:
        draw.text((PAD, y), line, font=tf, fill=INK)
        y += 62
    y += 8

    # Stats
    stats = c.get("context_stats", [])
    if isinstance(stats, list):
        for stat in stats[:3]:
            val   = str(stat.get("value", "—") if isinstance(stat, dict) else stat)
            label = str(stat.get("label", "")  if isinstance(stat, dict) else "")

            # Valore grande serif gold
            draw.text((PAD, y), val, font=fonts["serif_60"], fill=GOLD)
            y += 68

            # Label sans
            for ln in _wrap(draw, label, fonts["sans_22"], W - PAD*2)[:2]:
                draw.text((PAD, ln_y := y), ln, font=fonts["sans_22"], fill=INK_70)
                y += 30
            y += 4
            _hline(draw, y, INK_15)
            y += 20

    # Fonte
    src = c.get("source_label", "")
    if src:
        draw.text((PAD, H-56), f"FONTE: {src}",
                  font=fonts["mono_13"], fill=INK_50)

    img.save(str(out), "PNG")


# ── Slide 3 — Storico ────────────────────────────────────────────────────────

def render_slide3(c, out, fonts):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    _gold_bar(draw)
    _wordmark(draw, fonts, color_mark=PAPER, color_text=PAPER)
    _page_num(draw, fonts, 3, color=INK_50)

    y = 138
    draw.text((PAD, y), "PRECEDENTI STORICI", font=fonts["mono_16"], fill=GOLD_SOFT)
    y += 26
    _hline(draw, y, INK_15, w=1)
    y += 32

    # Titolo
    hist_title = c.get("historical_title", c.get("history_title", "In eventi simili"))
    tf = fonts["serif_52"]
    for line in _wrap(draw, hist_title, tf, W - PAD*2)[:2]:
        draw.text((PAD, y), line, font=tf, fill=PAPER)
        y += 62
    y += 16

    # Righe storiche — riempiono tutto lo spazio disponibile fino a H-6
    rows = c.get("historical_rows", [])
    if isinstance(rows, list):
        rows = rows[:5]
        n = len(rows)
        if n > 0:
            # Calcola altezza riga in modo che le righe riempiano esattamente lo spazio
            bottom = H - 6
            available = bottom - y
            row_h = available // n  # ogni riga occupa esattamente 1/n dello spazio

            for i, row in enumerate(rows):
                if isinstance(row, dict):
                    label    = str(row.get("label", ""))
                    value    = str(row.get("value", ""))
                    positive = row.get("positive", True)
                else:
                    label, value, positive = str(row), "", True

                val_color = POS if positive else NEG
                bg_fill   = POS_BG if positive else NEG_BG

                ry = y + i * row_h
                ry_end = ry + row_h - 4  # 4px gap tra righe

                # Sfondo riga — pieno
                draw.rectangle([PAD, ry, W-PAD, ry_end], fill=bg_fill)
                # Striscia laterale
                draw.rectangle([PAD, ry, PAD+6, ry_end], fill=val_color)

                # Centra verticalmente testo nella riga
                mid = ry + row_h // 2

                # Label a sinistra — dimensione adattiva
                lbl_font = fonts["sans_30"] if row_h > 90 else fonts["sans_26"]
                lh = _line_h(draw, lbl_font)
                draw.text((PAD+20, mid - lh // 2), label, font=lbl_font, fill=PAPER)

                # Value a destra — serif grande, colore vivace per massimo contrasto
                val_font = fonts["serif_52"] if row_h > 90 else fonts["serif_44"]
                vw = _text_w(draw, value, val_font)
                vh = _line_h(draw, val_font)
                draw.text((W - PAD - vw - 16, mid - vh // 2), value, font=val_font, fill=val_color)

    img.save(str(out), "PNG")


# ── Slide 4 — Settori ────────────────────────────────────────────────────────

def render_slide4(c, out, fonts):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    _gold_bar(draw)
    _wordmark(draw, fonts)
    _page_num(draw, fonts, 4)

    y = 138
    draw.text((PAD, y), "COSA TENERE D'OCCHIO", font=fonts["mono_16"], fill=GOLD_DEEP)
    y += 26
    _hline(draw, y, INK, w=2)
    y += 32

    # Titolo
    sec_title = c.get("sectors_title", "Settori coinvolti")
    tf = fonts["serif_52"]
    for line in _wrap(draw, sec_title, tf, W - PAD*2)[:2]:
        draw.text((PAD, y), line, font=tf, fill=INK)
        y += 62
    y += 16

    # I due blocchi riempiono TUTTO lo spazio fino alla riga disclaimer (H-58)
    # Gap di 12px tra i due blocchi
    DISC_Y = H - 58        # y della riga disclaimer
    GAP = 12               # gap tra blocco verde e blocco rosso
    block_h = (DISC_Y - y - GAP) // 2  # ogni blocco prende metà dello spazio

    y_bull = y
    y_bear = y + block_h + GAP

    # Bullish block — box pieno con sfondo verde scuro
    bullish = c.get("bullish_sectors", "")
    if bullish:
        draw.rectangle([PAD, y_bull, W-PAD, y_bull+block_h], fill=POS_BG)
        draw.rectangle([PAD, y_bull, PAD+6, y_bull+block_h], fill=POS)
        draw.text((PAD+18, y_bull+16), "▲  POTENZIALE BENEFICIO", font=fonts["mono_16"], fill=POS)
        draw.line([(PAD+18, y_bull+44), (W-PAD-18, y_bull+44)], fill=POS, width=1)
        bl_font = fonts["sans_30"]
        by = y_bull + 56
        for line in _wrap(draw, bullish, bl_font, W - PAD*2 - 32)[:4]:
            draw.text((PAD+18, by), line, font=bl_font, fill=PAPER)
            by += 38

    # Bearish block — box pieno con sfondo rosso scuro
    bearish = c.get("bearish_sectors", "")
    if bearish:
        draw.rectangle([PAD, y_bear, W-PAD, y_bear+block_h], fill=NEG_BG)
        draw.rectangle([PAD, y_bear, PAD+6, y_bear+block_h], fill=NEG)
        draw.text((PAD+18, y_bear+16), "▼  POTENZIALE PRESSIONE", font=fonts["mono_16"], fill=NEG)
        draw.line([(PAD+18, y_bear+44), (W-PAD-18, y_bear+44)], fill=NEG, width=1)
        bl_font = fonts["sans_30"]
        by = y_bear + 56
        for line in _wrap(draw, bearish, bl_font, W - PAD*2 - 32)[:4]:
            draw.text((PAD+18, by), line, font=bl_font, fill=PAPER)
            by += 38

    # Disclaimer
    draw.text((PAD, H-46),
              "Non è consulenza finanziaria · Elaborato da IA su fonti pubbliche",
              font=fonts["mono_13"], fill=INK_50)

    img.save(str(out), "PNG")


# ── Slide 5 — CTA ────────────────────────────────────────────────────────────

def render_slide5(c, out, fonts):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), GOLD)
    draw = ImageDraw.Draw(img)

    # Barra Ink top
    draw.rectangle([0, 0, W, 5], fill=INK)

    _wordmark(draw, fonts, color_mark=INK, color_text=INK)
    _page_num(draw, fonts, 5, color=INK_70)

    # Riga orizzontale
    _hline(draw, 130, INK, w=1)

    y = 158
    draw.text((PAD, y), "· KAIRÓS · ANALISI MACRO OGNI MATTINA ·",
              font=fonts["mono_16"], fill=INK_70)
    y += 44

    # Titolo principale — informativo, non promozionale
    titolo = "Ogni mattina analizziamo l'evento che muoverà i mercati"
    tf = fonts["serif_60"]
    lh = 72
    for line in _wrap(draw, titolo, tf, W - PAD*2)[:3]:
        draw.text((PAD, y), line, font=tf, fill=INK)
        y += lh
    y += 16

    _hline(draw, y, INK_70, w=1)
    y += 28

    # Cosa trovi su Telegram
    telegram_desc = "Su Telegram andiamo oltre: oltre al contesto macro, analizziamo anche i titoli azionari e gli strumenti finanziari che potrebbero essere impattati dall'evento del giorno."
    bf = fonts["sans_22"]
    for line in _wrap(draw, telegram_desc, bf, W - PAD*2)[:5]:
        draw.text((PAD, y), line, font=bf, fill=INK_70)
        y += 34
    y += 16

    # Box canale Telegram
    box_y = H - 240
    draw.rectangle([PAD, box_y, W-PAD, box_y+148], fill=INK)

    label1 = "Analisi completa ogni mattina →"
    draw.text((PAD + 20, box_y + 16), label1, font=fonts["mono_13"], fill=GOLD_SOFT)

    cta_channel = c.get("cta_channel", "@Kairós")
    ch_font = fonts["serif_44"]
    ch_w = _text_w(draw, cta_channel, ch_font)
    draw.text(((W - ch_w)//2, box_y + 44), cta_channel, font=ch_font, fill=PAPER)

    # Verbo d'azione sotto il nome canale
    action = "Unisciti · Leggi · Anticipa il mercato"
    aw = _text_w(draw, action, fonts["mono_13"])
    draw.text(((W - aw)//2, box_y + 96), action, font=fonts["mono_13"], fill=INK_50)

    # Disclaimer
    draw.text((PAD, H-46),
              "Contenuto informativo generato da IA · Non è consulenza finanziaria",
              font=fonts["mono_13"], fill=INK_70)

    img.save(str(out), "PNG")


# ── Entry point ───────────────────────────────────────────────────────────────

def render_carousel_slides_pillow(content_dict: dict, output_dir) -> list:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("Pillow non installato")
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fonts = _load_fonts()
    sid   = content_dict.get("signal_id", "kairos")
    safe  = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(sid))[:40]

    tasks = [
        (f"{safe}_slide1_hook.png",    render_slide1),
        (f"{safe}_slide2_context.png", render_slide2),
        (f"{safe}_slide3_history.png", render_slide3),
        (f"{safe}_slide4_sectors.png", render_slide4),
        (f"{safe}_slide5_cta.png",     render_slide5),
    ]

    paths = []
    for fname, fn in tasks:
        p = output_dir / fname
        try:
            fn(content_dict, p, fonts)
            paths.append(p)
            logger.info(f"  OK: {fname}")
        except Exception as e:
            logger.error(f"  ERR {fname}: {e}", exc_info=True)

    logger.info(f"Pillow: {len(paths)}/5 slide in {output_dir}")
    return paths


# ── Test CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil, tempfile
    logging.basicConfig(level=logging.INFO)

    mock = {
        "signal_id": "preview_v2",
        "eyebrow": "ENERGIA · GEOPOLITICA",
        "date_label": "4 Maggio 2026",
        "hook_title": "MSC aggira Hormuz: costi di trasporto verso l'alto",
        "hook_subtitle": "Le rotte alternative alzano la pressione inflazionistica globale",
        "causal_chain": "MSC bypassa Hormuz → rerouting via Sud Africa → +40% costi shipping → inflazione beni finali con lag 4-6 settimane",
        "context_title": "Perché Hormuz è così cruciale",
        "context_stats": [
            {"value": "21%",  "label": "del petrolio mondiale transita dallo Stretto"},
            {"value": "17M",  "label": "barili al giorno verso Asia ed Europa"},
            {"value": "+40%", "label": "aumento costi su rotte alternative via Sud Africa"},
        ],
        "historical_title": "In crisi simili, i mercati si sono mossi così",
        "historical_rows": [
            {"label": "Petrolio (Brent)",  "value": "+18% / +35%", "positive": True},
            {"label": "Shipping (BDIY)",   "value": "+25% / +60%", "positive": True},
            {"label": "Oro (safe haven)",  "value": "+5% / +12%",  "positive": True},
            {"label": "Compagnie aeree",   "value": "-8% / -15%",  "positive": False},
            {"label": "Manifattura EU",    "value": "-4% / -9%",   "positive": False},
        ],
        "sectors_title": "Settori da monitorare",
        "bullish_sectors": "Energia integrata, shipping alternativo, oro, difesa",
        "bearish_sectors": "Compagnie aeree, manifattura energy-intensive, shipping Mar Rosso",
        "cta_question": "Ogni mattina analizziamo l'evento che muovera' i mercati",
        "cta_body": "Su Telegram approfondiamo anche i titoli azionari e gli strumenti potenzialmente impattati.",
        "cta_channel": "@Kairós su Telegram",
        "source_label": "Reuters · Bloomberg · FT",
    }

    with tempfile.TemporaryDirectory() as td:
        slides = render_carousel_slides_pillow(mock, td)
        out_dir = Path(__file__).parent.parent
        print(f"\nSlide generate: {len(slides)}/5")
        for s in slides:
            dest = out_dir / Path(s).name
            shutil.copy(s, dest)
            print(f"  {Path(s).name}: {Path(s).stat().st_size//1024}KB => {dest}")