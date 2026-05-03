"""
instagram_publisher.py
Phase 8.4 — Kairós · MacroSignalTool

Pubblica contenuto su Instagram Business via Meta Graph API ufficiale.
Supporta: caroselli (fino a 10 slide), post singoli, storie.

Configurazione necessaria in .env:
  IG_ACCESS_TOKEN         — token di accesso (long-lived, ~60 giorni)
  IG_BUSINESS_ACCOUNT_ID  — ID account Instagram Business
  IG_IMAGE_HOST_URL       — URL pubblico dove le immagini sono hostiate
                             (es. Railway public URL del tuo backend)

Come ottenere le credenziali:
  1. Vai su https://developers.facebook.com/tools/explorer/
  2. Seleziona la tua app (MonovibeS Etsy Bot / Kairós)
  3. Permessi richiesti: instagram_basic, instagram_content_publish,
     instagram_manage_comments, pages_read_engagement
  4. Clicca "Genera token" e copia il valore
  5. Per l'account ID: GET /me/accounts → trova page_id →
     GET /{page_id}?fields=instagram_business_account → prendi id

Rate limits (Meta Graph API):
  - 100 post pubblicati per 24h per account
  - 200 chiamate API per ora per account
  - Carosello: max 10 slide

Testabile: python instagram_publisher.py --test
"""

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import aiohttp
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

logger = logging.getLogger("ig_publisher")

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


# ─── Config ───────────────────────────────────────────────────────────────────

def _get_config() -> dict:
    return {
        "access_token": os.getenv("IG_ACCESS_TOKEN", ""),
        "account_id": os.getenv("IG_BUSINESS_ACCOUNT_ID", ""),
        "image_host_url": os.getenv("IG_IMAGE_HOST_URL", "").rstrip("/"),
    }


def is_configured() -> bool:
    cfg = _get_config()
    return bool(cfg["access_token"] and cfg["account_id"])


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class PublishResult:
    success: bool
    post_id: Optional[str]
    post_url: Optional[str]
    error: Optional[str]
    media_type: str  # "CAROUSEL", "IMAGE", "STORY"


# ─── Core API calls ───────────────────────────────────────────────────────────

async def _api_post(session: "aiohttp.ClientSession", endpoint: str, data: dict) -> dict:
    """POST su Graph API con gestione errori."""
    url = f"{GRAPH_BASE}/{endpoint}"
    try:
        async with session.post(url, data=data) as resp:
            result = await resp.json()
            if "error" in result:
                raise RuntimeError(f"Graph API error: {result['error'].get('message', result['error'])}")
            return result
    except Exception as e:
        logger.error(f"API POST {endpoint}: {e}")
        raise


async def _api_get(session: "aiohttp.ClientSession", endpoint: str, params: dict) -> dict:
    """GET su Graph API."""
    url = f"{GRAPH_BASE}/{endpoint}"
    try:
        async with session.get(url, params=params) as resp:
            result = await resp.json()
            if "error" in result:
                raise RuntimeError(f"Graph API error: {result['error'].get('message', result['error'])}")
            return result
    except Exception as e:
        logger.error(f"API GET {endpoint}: {e}")
        raise


# ─── Upload immagini ──────────────────────────────────────────────────────────

async def _serve_image_url(image_path: Path, cfg: dict) -> str:
    """
    Restituisce l'URL pubblico di un'immagine.

    La Graph API richiede URL pubblici raggiungibili da Meta.
    Su Railway il backend è già pubblico: basta copiare le immagini
    nella cartella statica e usare il public URL.

    Se IG_IMAGE_HOST_URL è configurato, usa quello.
    Altrimenti usa imgbb (upload gratuito, URL permanente).
    """
    host_url = cfg.get("image_host_url", "")

    if host_url:
        # Le immagini sono servite dal backend Railway
        # Copia il file in /data/ig_slides/ e costruisci l'URL
        slides_dir = Path(__file__).parent / "ig_slides"
        slides_dir.mkdir(exist_ok=True)
        dest = slides_dir / image_path.name
        dest.write_bytes(image_path.read_bytes())
        return f"{host_url}/static/ig_slides/{image_path.name}"

    # Fallback: upload su imgbb (gratuito, no account necessario)
    imgbb_key = os.getenv("IMGBB_API_KEY", "")
    if imgbb_key:
        return await _upload_imgbb(image_path, imgbb_key)

    raise RuntimeError(
        "Nessun host immagini configurato. "
        "Imposta IG_IMAGE_HOST_URL (URL pubblico Railway) "
        "o IMGBB_API_KEY nel file .env"
    )


async def _upload_imgbb(image_path: Path, api_key: str) -> str:
    """Upload su imgbb e restituisce URL pubblico."""
    import base64
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()

    async with aiohttp.ClientSession() as session:
        data = {"key": api_key, "image": img_b64, "name": image_path.stem}
        async with session.post("https://api.imgbb.com/1/upload", data=data) as resp:
            result = await resp.json()
            if result.get("success"):
                url = result["data"]["url"]
                logger.info(f"  Upload imgbb OK: {url}")
                return url
            raise RuntimeError(f"imgbb upload fallito: {result}")


# ─── Pubblica carosello ───────────────────────────────────────────────────────

async def publish_carousel(
    image_paths: list,
    caption: str,
    cfg: dict = None,
) -> PublishResult:
    """
    Pubblica un carosello Instagram (2-10 slide).

    Flusso:
    1. Crea container per ogni immagine (is_carousel_item=true)
    2. Crea container carosello con tutti gli item IDs
    3. Pubblica il container carosello
    """
    if cfg is None:
        cfg = _get_config()

    if not AIOHTTP_AVAILABLE:
        return PublishResult(False, None, None, "aiohttp non installato", "CAROUSEL")

    if not is_configured():
        return PublishResult(False, None, None, "IG_ACCESS_TOKEN / IG_BUSINESS_ACCOUNT_ID non configurati", "CAROUSEL")

    if not image_paths:
        return PublishResult(False, None, None, "Nessuna immagine fornita", "CAROUSEL")

    account_id = cfg["account_id"]
    token = cfg["access_token"]

    async with aiohttp.ClientSession() as session:
        # Step 1: upload URL di ogni immagine e crea item container
        item_ids = []
        for i, img_path in enumerate(image_paths[:10]):  # max 10
            try:
                img_url = await _serve_image_url(Path(img_path), cfg)
                logger.info(f"  Slide {i+1}: {img_url}")

                # Crea item container
                item_data = {
                    "image_url": img_url,
                    "is_carousel_item": "true",
                    "access_token": token,
                }
                result = await _api_post(
                    session,
                    f"{account_id}/media",
                    item_data
                )
                item_id = result["id"]
                item_ids.append(item_id)
                logger.info(f"  Item container {i+1}: {item_id}")

                # Piccola pausa per evitare rate limit
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"  Errore slide {i+1}: {e}")
                return PublishResult(False, None, None, str(e), "CAROUSEL")

        if not item_ids:
            return PublishResult(False, None, None, "Nessun item container creato", "CAROUSEL")

        # Step 2: crea container carosello
        carousel_data = {
            "media_type": "CAROUSEL",
            "children": ",".join(item_ids),
            "caption": caption[:2200],  # limite Instagram
            "access_token": token,
        }
        try:
            carousel_result = await _api_post(
                session,
                f"{account_id}/media",
                carousel_data
            )
            carousel_id = carousel_result["id"]
            logger.info(f"  Carosello container: {carousel_id}")
        except Exception as e:
            return PublishResult(False, None, None, f"Errore container carosello: {e}", "CAROUSEL")

        # Step 3: verifica che il container sia pronto (polling)
        await _wait_for_container(session, carousel_id, token)

        # Step 4: pubblica
        try:
            publish_data = {
                "creation_id": carousel_id,
                "access_token": token,
            }
            pub_result = await _api_post(
                session,
                f"{account_id}/media_publish",
                publish_data
            )
            post_id = pub_result["id"]
            post_url = f"https://www.instagram.com/p/{_id_to_shortcode(post_id)}/"
            logger.info(f"✅ Carosello pubblicato: {post_id}")
            return PublishResult(True, post_id, post_url, None, "CAROUSEL")

        except Exception as e:
            return PublishResult(False, None, None, f"Errore pubblicazione: {e}", "CAROUSEL")


async def publish_single_image(
    image_path: Path,
    caption: str,
    cfg: dict = None,
) -> PublishResult:
    """Pubblica un post singolo immagine."""
    if cfg is None:
        cfg = _get_config()

    if not AIOHTTP_AVAILABLE or not is_configured():
        return PublishResult(False, None, None, "Non configurato", "IMAGE")

    account_id = cfg["account_id"]
    token = cfg["access_token"]

    async with aiohttp.ClientSession() as session:
        try:
            img_url = await _serve_image_url(image_path, cfg)

            media_data = {
                "image_url": img_url,
                "caption": caption[:2200],
                "access_token": token,
            }
            result = await _api_post(session, f"{account_id}/media", media_data)
            media_id = result["id"]

            await _wait_for_container(session, media_id, token)

            pub_result = await _api_post(
                session,
                f"{account_id}/media_publish",
                {"creation_id": media_id, "access_token": token}
            )
            post_id = pub_result["id"]
            return PublishResult(True, post_id, None, None, "IMAGE")

        except Exception as e:
            return PublishResult(False, None, None, str(e), "IMAGE")


async def publish_story(
    image_path: Path,
    cfg: dict = None,
) -> PublishResult:
    """Pubblica una storia Instagram."""
    if cfg is None:
        cfg = _get_config()

    if not AIOHTTP_AVAILABLE or not is_configured():
        return PublishResult(False, None, None, "Non configurato", "STORY")

    account_id = cfg["account_id"]
    token = cfg["access_token"]

    async with aiohttp.ClientSession() as session:
        try:
            img_url = await _serve_image_url(image_path, cfg)

            media_data = {
                "image_url": img_url,
                "media_type": "STORIES",
                "access_token": token,
            }
            result = await _api_post(session, f"{account_id}/media", media_data)
            media_id = result["id"]

            await _wait_for_container(session, media_id, token)

            pub_result = await _api_post(
                session,
                f"{account_id}/media_publish",
                {"creation_id": media_id, "access_token": token}
            )
            post_id = pub_result["id"]
            return PublishResult(True, post_id, None, None, "STORY")

        except Exception as e:
            return PublishResult(False, None, None, str(e), "STORY")


# ─── Gestione commenti ────────────────────────────────────────────────────────

async def get_recent_comments(post_id: str, cfg: dict = None) -> list:
    """
    Recupera i commenti recenti di un post.
    Utile per il workaround poll/quiz via commenti.
    """
    if cfg is None:
        cfg = _get_config()

    if not AIOHTTP_AVAILABLE or not is_configured():
        return []

    async with aiohttp.ClientSession() as session:
        try:
            result = await _api_get(
                session,
                f"{post_id}/comments",
                {
                    "fields": "id,text,username,timestamp",
                    "access_token": cfg["access_token"],
                }
            )
            return result.get("data", [])
        except Exception as e:
            logger.error(f"Errore lettura commenti: {e}")
            return []


async def reply_to_comment(
    comment_id: str,
    reply_text: str,
    cfg: dict = None,
) -> bool:
    """Risponde a un commento."""
    if cfg is None:
        cfg = _get_config()

    if not AIOHTTP_AVAILABLE or not is_configured():
        return False

    async with aiohttp.ClientSession() as session:
        try:
            await _api_post(
                session,
                f"{comment_id}/replies",
                {
                    "message": reply_text[:1000],
                    "access_token": cfg["access_token"],
                }
            )
            return True
        except Exception as e:
            logger.error(f"Errore risposta commento: {e}")
            return False


# ─── Utility ─────────────────────────────────────────────────────────────────

async def _wait_for_container(
    session: "aiohttp.ClientSession",
    container_id: str,
    token: str,
    max_wait: int = 30,
) -> bool:
    """Polling finché il container è FINISHED (pronto per la pubblicazione)."""
    for attempt in range(max_wait):
        result = await _api_get(
            session,
            container_id,
            {"fields": "status_code", "access_token": token}
        )
        status = result.get("status_code", "")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            raise RuntimeError(f"Container {container_id} in errore")
        await asyncio.sleep(1)

    logger.warning(f"Container {container_id} non pronto dopo {max_wait}s")
    return False


def _id_to_shortcode(media_id: str) -> str:
    """Converte media_id numerico in shortcode Instagram (base64)."""
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        n = int(media_id.split("_")[0])
        shortcode = ""
        while n > 0:
            shortcode = alphabet[n % 64] + shortcode
            n //= 64
        return shortcode or media_id
    except Exception:
        return media_id


async def get_account_info(cfg: dict = None) -> dict:
    """Verifica che le credenziali siano valide e restituisce info account."""
    if cfg is None:
        cfg = _get_config()

    if not AIOHTTP_AVAILABLE:
        return {"error": "aiohttp non installato"}

    async with aiohttp.ClientSession() as session:
        try:
            result = await _api_get(
                session,
                cfg["account_id"],
                {
                    "fields": "id,name,username,followers_count,media_count",
                    "access_token": cfg["access_token"],
                }
            )
            return result
        except Exception as e:
            return {"error": str(e)}


# ─── CLI test ─────────────────────────────────────────────────────────────────

async def _run_test_async():
    print(f"\n{'='*60}")
    print("TEST: instagram_publisher.py")
    print("="*60)

    cfg = _get_config()
    print(f"\nConfigurazione:")
    print(f"  Token:      {'✅ configurato' if cfg['access_token'] else '❌ mancante (IG_ACCESS_TOKEN)'}")
    print(f"  Account ID: {'✅ ' + cfg['account_id'] if cfg['account_id'] else '❌ mancante (IG_BUSINESS_ACCOUNT_ID)'}")
    print(f"  Image host: {cfg['image_host_url'] or '⚠️  non configurato (IG_IMAGE_HOST_URL)'}")

    if not is_configured():
        print("\n⚠️  Credenziali non configurate. Imposta IG_ACCESS_TOKEN e IG_BUSINESS_ACCOUNT_ID nel file .env")
        print("\nPer ottenere le credenziali:")
        print("  1. Vai su https://developers.facebook.com/tools/explorer/")
        print("  2. Seleziona la tua app")
        print("  3. Permessi: instagram_basic, instagram_content_publish, instagram_manage_comments")
        print("  4. Clicca 'Genera token' e copia il valore")
        print("  5. GET /me/accounts → prendi page_id")
        print("  6. GET /{page_id}?fields=instagram_business_account → prendi id")
        return

    # Verifica account
    print("\nVerifica account...")
    info = await get_account_info(cfg)
    if "error" in info:
        print(f"❌ Errore: {info['error']}")
    else:
        print(f"✅ Account verificato:")
        print(f"   Username: @{info.get('username', '?')}")
        print(f"   Followers: {info.get('followers_count', '?')}")
        print(f"   Post totali: {info.get('media_count', '?')}")

    print("\n✅ Test completato. Per testare la pubblicazione usa --publish-test")


def _run_test():
    asyncio.run(_run_test_async())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    parser = argparse.ArgumentParser(description="Instagram Publisher — Kairós")
    parser.add_argument("--test", action="store_true", help="Verifica configurazione e account")
    parser.add_argument("--account-info", action="store_true", help="Mostra info account Instagram")
    args = parser.parse_args()

    if args.test or args.account_info:
        _run_test()
    else:
        parser.print_help()
