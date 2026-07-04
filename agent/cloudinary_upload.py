# agent/cloudinary_upload.py — Upload de fichiers vers Cloudinary
# NAYA — Avent IMMO Bot

"""
Uploade les fichiers médias (audio, images) vers Cloudinary
pour obtenir une URL publique permanente compatible WhatsApp.

Free tier : 25 GB stockage / 25 GB bande passante par mois.
"""

import os
import hashlib
import time
import logging
import httpx

logger = logging.getLogger("avent-immo")

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")


def _signature(params: dict) -> str:
    """Génère la signature SHA1 pour l'API Cloudinary."""
    to_sign = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in ("file", "api_key"))
    to_sign += API_SECRET
    return hashlib.sha1(to_sign.encode()).hexdigest()


async def _uploader(file_bytes: bytes, nom_fichier: str, dossier: str, resource_type: str) -> str | None:
    if not all([CLOUD_NAME, API_KEY, API_SECRET]):
        logger.warning("Cloudinary non configuré — variables manquantes")
        return None

    timestamp = int(time.time())
    public_id = f"{dossier}/{nom_fichier.rsplit('.', 1)[0]}"
    params = {"timestamp": timestamp, "folder": dossier, "public_id": public_id}
    sig = _signature(params)
    url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/{resource_type}/upload"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                url,
                data={
                    "api_key": API_KEY,
                    "timestamp": str(timestamp),
                    "signature": sig,
                    "folder": dossier,
                    "public_id": public_id,
                },
                files={"file": (nom_fichier, file_bytes)},
            )
        if r.status_code == 200:
            url_pub = r.json().get("secure_url", "")
            logger.info(f"Cloudinary upload OK: {url_pub}")
            return url_pub
        logger.error(f"Erreur Cloudinary {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Exception Cloudinary: {e}")
        return None


async def uploader_audio(audio_bytes: bytes, nom_fichier: str) -> str | None:
    """Uploade un fichier audio. Retourne l'URL publique ou None."""
    return await _uploader(audio_bytes, nom_fichier, "avent-immo/audio", "video")


async def uploader_image(image_bytes: bytes, nom_fichier: str) -> str | None:
    """Uploade une image. Retourne l'URL publique ou None."""
    return await _uploader(image_bytes, nom_fichier, "avent-immo/images", "image")
