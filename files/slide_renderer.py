"""
slide_renderer.py
Phase 8.3 — Kairós · MacroSignalTool

Renderizza le slide del carosello Instagram come immagini PNG 1080x1080
usando Playwright (headless Chromium) su template HTML brandizzati Kairós.

Palette:
  Ink     #0E0E0C  — foreground primario
  Paper   #F5F2E6  — background primario (crema FT)
  Gold    #B8893B  — accento (usato 5% max)
  Pos     #2D5F3F  — LONG / positivo
  Neg     #8C2D2D  — SHORT / negativo

Tipografia (via Google Fonts):
  Cormorant Garamond — serif (titoli)
  Inter               — sans (body, UI)
  JetBrains Mono      — mono (dati, ticker)

Output: lista di Path alle immagini PNG salvate in /tmp/kairos_slides/

Testabile: python slide_renderer.py --test
"""

import argparse
import json
import logging
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("slide_renderer")

SLIDES_DIR = Path(tempfile.gettempdir()) / "kairos_slides"
SLIDES_DIR.mkdir(exist_ok=True)

# Google Fonts import (usato nei template HTML)
FONTS_IMPORT = """
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Inter:wght@400;500&family=JetBrains+Mono:wght@400;500&display=swap');
"""

# ─── CSS base Kairós ──────────────────────────────────────────────────────────

BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --ink:       #0E0E0C;
  --ink-90:    #1B1B18;
  --ink-70:    #3A3A35;
  --ink-50:    #6B6B62;
  --ink-30:    #A6A69C;
  --ink-15:    #D4D3C7;
  --paper:     #F5F2E6;
  --paper-pure:#FAF8EE;
  --paper-deep:#ECE8D7;
  --gold:      #B8893B;
  --gold-deep: #8C6624;
  --gold-soft: #C9A062;
  --pos:       #2D5F3F;
  --neg:       #8C2D2D;
  --serif:     'Cormorant Garamond', Georgia, serif;
  --sans:      'Inter', 'Helvetica Neue', sans-serif;
  --mono:      'JetBrains Mono', 'Courier New', monospace;
}
body {
  width: 1080px; height: 1080px;
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
.frame {
  width: 1080px; height: 1080px;
  position: relative;
  display: flex; flex-direction: column;
  padding: 64px;
}
.eyebrow {
  font-family: var(--mono); font-size: 18px;
  letter-spacing: 0.18em; text-transform: uppercase;
}
.serif { font-family: var(--serif); }
.serif-italic { font-family: var(--serif); font-style: italic; }
.mono  { font-family: var(--mono); }
.top-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 48px;
}
.wordmark {
  font-family: var(--serif); font-size: 22px; font-weight: 500;
  letter-spacing: 0.04em;
}
.date-label {
  font-family: var(--mono); font-size: 14px;
  letter-spacing: 0.12em; color: var(--ink-50);
}
.rule { height: 1px; background: var(--ink); margin: 32px 0; }
.rule-thin { height: 1px; background: var(--ink-15); margin: 24px 0; }
.page-num {
  position: absolute; bottom: 48px; right: 64px;
  font-family: var(--mono); font-size: 14px;
  letter-spacing: 0.12em; color: var(--ink-30);
}
.footer-source {
  position: absolute; bottom: 48px; left: 64px;
  font-family: var(--mono); font-size: 12px;
  letter-spacing: 0.1em; color: var(--ink-30);
}
"""


# ─── Template slide 1 — Hook ──────────────────────────────────────────────────

def _slide1_html(c: dict) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{FONTS_IMPORT}{BASE_CSS}
.s1 {{ background: var(--paper); color: var(--ink); }}
.s1-eyebrow {{ color: var(--gold-deep); margin-bottom: 48px; }}
.s1-title {{ font-family: var(--serif); font-size: 72px; font-weight: 500;
  line-height: 1.0; letter-spacing: -0.02em; margin-bottom: 32px; }}
.s1-subtitle {{ font-family: var(--sans); font-size: 26px; color: var(--ink-70);
  line-height: 1.4; max-width: 800px; }}
.swipe-hint {{ font-family: var(--mono); font-size: 15px;
  letter-spacing: 0.14em; color: var(--ink-30); margin-top: 48px; }}
.gold-bar {{ position: absolute; top: 0; left: 0; right: 0;
  height: 4px; background: var(--gold); }}
</style></head><body>
<div class="frame s1">
  <div class="gold-bar"></div>
  <div class="top-bar">
    <div class="wordmark">Kairós</div>
    <div class="date-label">{c.get('date_label','')}</div>
  </div>
  <div class="eyebrow s1-eyebrow">· {c.get('eyebrow','')}</div>
  <div class="s1-title">{c.get('hook_title','')}</div>
  <div class="rule"></div>
  <div class="s1-subtitle">{c.get('hook_subtitle','')}</div>
  <div class="swipe-hint">Scorri per capire l'impatto →</div>
  <div class="page-num">1 / 5</div>
</div></body></html>"""


# ─── Template slide 2 — Contesto ─────────────────────────────────────────────

def _slide2_html(c: dict) -> str:
    stats_html = ""
    for s in c.get("context_stats", [])[:3]:
        stats_html += f"""
        <div class="stat-row">
          <div class="stat-value">{s.get('value','—')}</div>
          <div class="stat-label">{s.get('label','')}</div>
        </div>
        <div class="rule-thin"></div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{FONTS_IMPORT}{BASE_CSS}
.s2 {{ background: var(--paper-pure); color: var(--ink); }}
.s2-title {{ font-family: var(--serif); font-size: 52px; font-weight: 500;
  line-height: 1.05; letter-spacing: -0.02em; margin-bottom: 48px; }}
.stat-row {{ display: flex; align-items: baseline; gap: 28px; padding: 20px 0; }}
.stat-value {{ font-family: var(--serif); font-size: 56px; font-weight: 500;
  color: var(--gold-deep); line-height: 1; min-width: 160px; }}
.stat-label {{ font-family: var(--sans); font-size: 22px; color: var(--ink-70);
  line-height: 1.4; }}
.section-eyebrow {{ font-family: var(--mono); font-size: 13px;
  letter-spacing: 0.18em; color: var(--ink-50); margin-bottom: 16px; }}
</style></head><body>
<div class="frame s2">
  <div class="top-bar">
    <div class="wordmark">Kairós</div>
    <div class="date-label">{c.get('date_label','')}</div>
  </div>
  <div class="section-eyebrow">IL CONTESTO</div>
  <div class="s2-title">{c.get('context_title','Perché conta')}</div>
  <div class="rule"></div>
  {stats_html}
  <div class="footer-source">FONTE: {c.get('source_label','')}</div>
  <div class="page-num">2 / 5</div>
</div></body></html>"""


# ─── Template slide 3 — Storico ──────────────────────────────────────────────

def _slide3_html(c: dict) -> str:
    rows_html = ""
    for r in c.get("historical_rows", [])[:5]:
        color = "var(--pos)" if r.get("positive") else "var(--neg)"
        rows_html += f"""
        <div class="hist-row">
          <div class="hist-label">{r.get('label','')}</div>
          <div class="hist-value" style="color:{color};">{r.get('value','')}</div>
        </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{FONTS_IMPORT}{BASE_CSS}
.s3 {{ background: var(--ink); color: var(--paper); }}
.s3 .wordmark {{ color: var(--paper); }}
.s3 .date-label {{ color: var(--ink-30); }}
.s3-title {{ font-family: var(--serif); font-size: 44px; font-weight: 500;
  line-height: 1.1; letter-spacing: -0.015em; margin-bottom: 40px; }}
.section-eyebrow {{ font-family: var(--mono); font-size: 13px;
  letter-spacing: 0.18em; color: var(--gold-soft); margin-bottom: 16px; }}
.hist-row {{ display: flex; justify-content: space-between; align-items: center;
  padding: 22px 0; border-bottom: 1px solid rgba(245,242,230,0.12); }}
.hist-label {{ font-family: var(--sans); font-size: 22px; color: rgba(245,242,230,0.75); }}
.hist-value {{ font-family: var(--mono); font-size: 24px; font-weight: 500; }}
.s3-rule {{ height: 1px; background: rgba(245,242,230,0.25); margin-bottom: 8px; }}
</style></head><body>
<div class="frame s3">
  <div class="top-bar">
    <div class="wordmark">Kairós</div>
    <div class="date-label">{c.get('date_label','')}</div>
  </div>
  <div class="section-eyebrow">STORICO</div>
  <div class="s3-title">{c.get('historical_title','In eventi simili')}</div>
  <div class="s3-rule"></div>
  {rows_html}
  <div class="page-num" style="color:rgba(245,242,230,0.3);">3 / 5</div>
</div></body></html>"""


# ─── Template slide 4 — Settori ──────────────────────────────────────────────

def _slide4_html(c: dict) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{FONTS_IMPORT}{BASE_CSS}
.s4 {{ background: var(--paper); color: var(--ink); }}
.s4-title {{ font-family: var(--serif); font-size: 52px; font-weight: 500;
  line-height: 1.05; letter-spacing: -0.02em; margin-bottom: 40px; }}
.section-eyebrow {{ font-family: var(--mono); font-size: 13px;
  letter-spacing: 0.18em; color: var(--ink-50); margin-bottom: 16px; }}
.sector-block {{ padding: 28px 32px; margin-bottom: 20px; }}
.sector-label {{ font-family: var(--mono); font-size: 13px; font-weight: 500;
  letter-spacing: 0.18em; margin-bottom: 12px; }}
.sector-content {{ font-family: var(--sans); font-size: 22px;
  line-height: 1.45; color: var(--ink-70); }}
.bullish-block {{ background: rgba(45,95,63,0.07);
  border-left: 3px solid var(--pos); }}
.bearish-block {{ background: rgba(140,45,45,0.07);
  border-left: 3px solid var(--neg); }}
.bullish-label {{ color: var(--pos); }}
.bearish-label {{ color: var(--neg); }}
.disclaimer {{ position: absolute; bottom: 48px; left: 64px; right: 64px;
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em;
  color: var(--ink-30); line-height: 1.5; }}
</style></head><body>
<div class="frame s4">
  <div class="top-bar">
    <div class="wordmark">Kairós</div>
    <div class="date-label">{c.get('date_label','')}</div>
  </div>
  <div class="section-eyebrow">COSA TENERE D'OCCHIO</div>
  <div class="s4-title">{c.get('sectors_title','Settori coinvolti')}</div>
  <div class="rule"></div>
  <div class="sector-block bullish-block">
    <div class="sector-label bullish-label">POTENZIALE BENEFICIO</div>
    <div class="sector-content">{c.get('bullish_sectors','')}</div>
  </div>
  <div class="sector-block bearish-block">
    <div class="sector-label bearish-label">POTENZIALE PRESSIONE</div>
    <div class="sector-content">{c.get('bearish_sectors','')}</div>
  </div>
  <div class="disclaimer">Contenuto informativo · Non è consulenza finanziaria · Elaborato da IA su fonti pubbliche</div>
  <div class="page-num">4 / 5</div>
</div></body></html>"""


# ─── Template slide 5 — CTA Telegram ─────────────────────────────────────────

def _slide5_html(c: dict) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{FONTS_IMPORT}{BASE_CSS}
.s5 {{ background: var(--gold); color: var(--ink); justify-content: center;
  align-items: center; text-align: center; }}
.s5-eyebrow {{ font-family: var(--mono); font-size: 14px;
  letter-spacing: 0.2em; color: var(--ink-70); margin-bottom: 48px; }}
.s5-question {{ font-family: var(--serif); font-size: 64px; font-weight: 500;
  line-height: 1.05; letter-spacing: -0.02em; margin-bottom: 32px;
  max-width: 900px; }}
.s5-body {{ font-family: var(--sans); font-size: 26px; color: var(--ink-70);
  line-height: 1.5; margin-bottom: 48px; max-width: 700px; }}
.s5-cta {{ font-family: var(--mono); font-size: 18px; font-weight: 500;
  letter-spacing: 0.12em; border: 2px solid var(--ink); padding: 18px 40px;
  display: inline-block; }}
.s5-disclaimer {{ position: absolute; bottom: 48px; left: 64px; right: 64px;
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em;
  color: rgba(14,14,12,0.4); text-align: center; }}
</style></head><body>
<div class="frame s5">
  <div class="s5-eyebrow">· KAIRÓS · IL MOMENTO OPPORTUNO</div>
  <div class="s5-question">{c.get('cta_question','Vuoi i segnali operativi?')}</div>
  <div class="s5-body">{c.get('cta_body','')}</div>
  <div class="s5-cta">{c.get('cta_channel','@kairos.macro')}</div>
  <div class="s5-disclaimer">Generato da IA · Solo scopo informativo · Non è consulenza finanziaria</div>
  <div class="page-num" style="color:rgba(14,14,12,0.3);">5 / 5</div>
</div></body></html>"""


# ─── Renderer principale ──────────────────────────────────────────────────────

def render_carousel_slides(content_dict: dict, output_dir: Path = None) -> list:
    """
    Renderizza le 5 slide del carosello come PNG 1080x1080.

    Prova prima Playwright (qualità massima con CSS/font Google).
    Se Playwright o Chromium non sono disponibili (es. Railway),
    usa automaticamente il renderer Pillow come fallback.

    Args:
        content_dict: dict (da IGCarouselContent o asdict())
        output_dir: directory dove salvare i PNG (default: /tmp/kairos_slides/)

    Returns:
        lista di Path ai file PNG generati
    """
    # ── Fallback Pillow se Playwright non è disponibile ───────────────────────
    try:
        from playwright.sync_api import sync_playwright as _sync_playwright
        _playwright_ok = True
    except ImportError:
        _playwright_ok = False

    if not _playwright_ok:
        logger.info("Playwright non disponibile → uso renderer Pillow")
        try:
            from slide_renderer_pillow import render_carousel_slides_pillow
            out = output_dir if output_dir is not None else SLIDES_DIR
            return render_carousel_slides_pillow(content_dict, out)
        except Exception as e:
            logger.error(f"Anche Pillow renderer fallito: {e}")
            return []

    # Controlla se Chromium di sistema è disponibile
    _chromium_candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/run/current-system/sw/bin/chromium",
    ]
    _chromium_exe = next((p for p in _chromium_candidates if p and Path(p).exists()), None)
    _launch_kwargs: dict = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if _chromium_exe:
        _launch_kwargs["executable_path"] = _chromium_exe

    # Prova a lanciare Playwright — se fallisce usa Pillow
    try:
        import subprocess
        test = subprocess.run(
            ["playwright", "install", "--dry-run", "chromium"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass

    if output_dir is None:
        output_dir = SLIDES_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    # Pulisci slide precedenti
    signal_id = content_dict.get("signal_id", "unknown")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(signal_id))

    templates = [
        ("slide1_hook",     _slide1_html(content_dict)),
        ("slide2_context",  _slide2_html(content_dict)),
        ("slide3_history",  _slide3_html(content_dict)),
        ("slide4_sectors",  _slide4_html(content_dict)),
        ("slide5_cta",      _slide5_html(content_dict)),
    ]

    output_paths = []

    # Su Railway (nixpacks) Chromium è installato da sistema; usa il path di sistema
    _chromium_candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/run/current-system/sw/bin/chromium",
    ]
    _chromium_exe = next((p for p in _chromium_candidates if p and Path(p).exists()), None)
    _launch_kwargs = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if _chromium_exe:
        _launch_kwargs["executable_path"] = _chromium_exe
        logger.info(f"slide_renderer: uso Chromium di sistema → {_chromium_exe}")

    with sync_playwright() as p:
        browser = p.chromium.launch(**_launch_kwargs)
        page = browser.new_page(viewport={"width": 1080, "height": 1080})

        for i, (name, html) in enumerate(templates, 1):
            try:
                page.set_content(html, wait_until="networkidle")
                # Attendi font Google Fonts
                page.wait_for_timeout(1500)

                out_path = output_dir / f"{safe_id}_{name}.png"
                page.screenshot(path=str(out_path), full_page=False)
                output_paths.append(out_path)
                logger.info(f"  Slide {i}/5 renderizzata: {out_path.name}")

            except Exception as e:
                logger.error(f"  Errore slide {i}: {e}")

        browser.close()

    logger.info(f"Renderizzate {len(output_paths)}/5 slide in {output_dir}")
    return output_paths


def render_story_slide(content_dict: dict, output_dir: Path = None) -> Optional[Path]:
    """
    Renderizza una storia Instagram (1080x1920) con il flash della notizia.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright non installato")
        return None

    if output_dir is None:
        output_dir = SLIDES_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    story_html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{FONTS_IMPORT}{BASE_CSS}
body {{ width: 1080px; height: 1920px; }}
.story-frame {{
  width: 1080px; height: 1920px;
  background: var(--paper); color: var(--ink);
  display: flex; flex-direction: column;
  padding: 250px 96px;
  justify-content: space-between;
}}
.story-eyebrow {{ font-family: var(--mono); font-size: 18px;
  letter-spacing: 0.2em; color: var(--gold-deep); }}
.story-title {{ font-family: var(--serif); font-size: 72px; font-weight: 500;
  line-height: 1.05; letter-spacing: -0.02em; }}
.story-footer {{ font-family: var(--mono); font-size: 16px;
  letter-spacing: 0.14em; color: var(--ink-30); }}
.gold-bar {{ position: absolute; top: 0; left: 0; right: 0;
  height: 4px; background: var(--gold); }}
</style></head><body>
<div class="story-frame" style="position:relative;">
  <div class="gold-bar"></div>
  <div class="story-eyebrow">· FLASH · {content_dict.get('date_label','')}</div>
  <div class="story-title">{content_dict.get('hook_title','')}</div>
  <div class="story-footer">FONTE: {content_dict.get('source_label','')} · SEGUI @kairos.macro</div>
</div></body></html>"""

    signal_id = content_dict.get("signal_id", "unknown")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(signal_id))

    _chromium_candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/run/current-system/sw/bin/chromium",
    ]
    _chromium_exe = next((p for p in _chromium_candidates if p and Path(p).exists()), None)
    _launch_kwargs = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if _chromium_exe:
        _launch_kwargs["executable_path"] = _chromium_exe

    with sync_playwright() as p:
        browser = p.chromium.launch(**_launch_kwargs)
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        page.set_content(story_html, wait_until="networkidle")
        page.wait_for_timeout(1500)

        out_path = output_dir / f"{safe_id}_story.png"
        page.screenshot(path=str(out_path), full_page=False)
        browser.close()

    logger.info(f"Storia renderizzata: {out_path}")
    return out_path


# ─── CLI test ─────────────────────────────────────────────────────────────────

def _run_test():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    print(f"\n{'='*60}")
    print("TEST: slide_renderer.py")
    print("="*60)

    # Usa contenuto mock
    mock_content = {
        "eyebrow": "GEOPOLITICA · ENERGIA",
        "hook_title": "Iran chiude lo Stretto di Hormuz",
        "hook_subtitle": "Cosa significa per i mercati e i tuoi investimenti.",
        "date_label": "3 maggio 2026",
        "context_title": "Perché Hormuz è così importante?",
        "context_stats": [
            {"value": "20%", "label": "del petrolio mondiale transita da Hormuz"},
            {"value": "17M", "label": "barili al giorno riforniscono Europa e Asia"},
            {"value": "48h", "label": "di chiusura bastano per far salire il Brent"},
        ],
        "historical_title": "In eventi simili, i mercati si sono mossi così",
        "historical_rows": [
            {"label": "Petrolio (Brent)", "value": "+25% / +35%", "positive": True},
            {"label": "Titoli energia", "value": "+10% / +18%", "positive": True},
            {"label": "Oro (safe haven)", "value": "+5% / +12%", "positive": True},
            {"label": "Compagnie aeree", "value": "-8% / -15%", "positive": False},
        ],
        "sectors_title": "Settori coinvolti",
        "bullish_sectors": "Energia integrata, produttori petrolio USA, oro, difesa",
        "bearish_sectors": "Compagnie aeree, shipping, manifattura energy-intensive",
        "cta_question": "Vuoi i segnali operativi completi?",
        "cta_body": "Ticker, stop loss, target e sizing in tempo reale — solo su Telegram.",
        "cta_channel": "Cerca @kairos.macro su Telegram",
        "source_label": "Reuters · Bloomberg",
        "signal_id": "test_render_001",
        "disclaimer": "Contenuto informativo · Non è consulenza finanziaria",
    }

    print("\nRenderizzazione 5 slide carosello...")
    slides = render_carousel_slides(mock_content)

    if slides:
        print(f"\n✅ {len(slides)} slide renderizzate:")
        for s in slides:
            size_kb = Path(s).stat().st_size // 1024
            print(f"   {Path(s).name} ({size_kb} KB)")
    else:
        print("❌ Nessuna slide generata (Playwright installato?)")

    print("\nRenderizzazione storia...")
    story = render_story_slide(mock_content)
    if story:
        size_kb = Path(story).stat().st_size // 1024
        print(f"✅ Storia: {Path(story).name} ({size_kb} KB)")

    print(f"\n📁 Output in: {SLIDES_DIR}")
    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Slide Renderer — Kairós")
    parser.add_argument("--test", action="store_true", help="Test rendering slide mock")
    parser.add_argument("--content", type=str, help="Path a file JSON IGCarouselContent")
    args = parser.parse_args()

    if args.test:
        _run_test()
    elif args.content:
        with open(args.content, encoding="utf-8") as f:
            content = json.load(f)
        slides = render_carousel_slides(content)
        print(f"Renderizzate {len(slides)} slide")
    else:
        parser.print_help()
