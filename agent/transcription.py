# agent/transcription.py — Transcription vocale via Groq Whisper
# NAYA — Avent IMMO Bot

"""
Transcrit les messages vocaux WhatsApp en texte via Groq Whisper API.
Gratuit jusqu'à 7200 secondes/jour.
"""

import os
import logging
import httpx

logger = logging.getLogger("avent-immo")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Contexte vocabulaire immobilier pour améliorer la reconnaissance
WHISPER_PROMPT = (
    "AVENT GROUPE, NAYA, Cité Présidentielle, Yamoussoukro, "
    "lot, parcelle, souscription, acompte, FCFA, superficies, "
    "ACD, CMPF, titre foncier, mode de paiement, comptant, échelonné"
)

MIME_TO_EXT = {
    "audio/ogg": "ogg",
    "audio/ogg; codecs=opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
    "audio/mp4a-latm": "mp4",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/aac": "aac",
}


async def transcrire_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Transcrit un fichier audio en texte via Groq Whisper.

    Args:
        audio_bytes : octets du fichier audio (téléchargé depuis Meta)
        mime_type   : type MIME du fichier (ex: audio/ogg)

    Returns:
        Texte transcrit, ou chaîne vide en cas d'erreur.
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY non configurée — transcription impossible")
        return ""

    ext = MIME_TO_EXT.get(mime_type.lower().split(";")[0].strip(), "ogg")
    nom_fichier = f"voice.{ext}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                GROQ_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": (nom_fichier, audio_bytes, mime_type)},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "language": "fr",
                    "prompt": WHISPER_PROMPT,
                },
            )

        if r.status_code == 200:
            texte = r.json().get("text", "").strip()
            logger.info(f"Transcription OK ({len(audio_bytes)} octets) : {texte[:80]}")
            return texte
        else:
            logger.error(f"Erreur Groq Whisper {r.status_code}: {r.text[:200]}")
            return ""

    except Exception as e:
        logger.error(f"Exception transcription Groq: {e}")
        return ""
