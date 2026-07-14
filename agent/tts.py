# agent/tts.py — Synthèse vocale (Text-To-Speech)
# NAYA — Avent IMMO Bot

"""
Convertit du texte en audio pour les réponses vocales WhatsApp.

Priorité :
  1. ElevenLabs (Starter) — voix humaine française "Marie", très naturelle
  2. edge-tts (Microsoft Neural) — fallback gratuit, voix fr-FR-DeniseNeural
  3. gTTS (Google) — fallback de dernier recours
"""

import io
import os
import re
import logging
import httpx

logger = logging.getLogger("avent-immo")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "sANWqF1bCMzR6eyZbCGw")  # Marie — Warm, Expressive
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "fr-FR-DeniseNeural")


def _nettoyer_texte_pour_tts(texte: str) -> str:
    """Prépare le texte pour la synthèse vocale — retire tout ce qui n'a pas de sens à l'oral."""
    # Tags internes
    texte = re.sub(r'\[[A-Z_]+\|[^\]]+\]', '', texte)
    # Markdown
    texte = re.sub(r'\*\*(.+?)\*\*', r'\1', texte)
    texte = re.sub(r'\*(.+?)\*', r'\1', texte)
    texte = re.sub(r'^#{1,6}\s+', '', texte, flags=re.MULTILINE)
    # Emojis
    texte = re.sub(r'[\U0001F000-\U0001FFFF\U00002500-\U00002BEF\U00002702-\U000027B0]+', '', texte, flags=re.UNICODE)
    # Séparateurs de milliers
    for _ in range(3):
        texte = re.sub(r'(\d)\s(\d{3})\b', r'\1\2', texte)
    # Monnaie
    texte = re.sub(r'\bFCFA\b', 'francs CFA', texte)
    # Tirets de liste, URLs
    texte = re.sub(r'^\s*[-•]\s+', '', texte, flags=re.MULTILINE)
    texte = re.sub(r'https?://\S+', '', texte)
    texte = re.sub(r'[|#@~`^]', '', texte)
    # Nettoyage final
    texte = re.sub(r'[ \t]{2,}', ' ', texte)
    texte = re.sub(r'\n{2,}', '\n', texte)
    return texte.strip()


async def _generer_audio_elevenlabs(texte: str) -> bytes | None:
    """ElevenLabs TTS — voix française humaine de haute qualité."""
    if not ELEVENLABS_API_KEY:
        return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": texte,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                logger.info(f"Audio ElevenLabs généré ({len(r.content)} octets)")
                return r.content
            logger.error(f"ElevenLabs erreur {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Exception ElevenLabs: {e}")
        return None


async def _generer_audio_edge(texte: str) -> bytes | None:
    """Microsoft Neural TTS via edge-tts — gratuit, aucune clé requise."""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(texte, voice=EDGE_TTS_VOICE)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        if audio_data:
            logger.info(f"Audio edge-tts généré ({len(audio_data)} octets) [fallback]")
            return audio_data
        return None
    except ImportError:
        logger.error("edge-tts non installé — pip install edge-tts")
        return None
    except Exception as e:
        logger.error(f"Exception edge-tts: {e}")
        return None


async def _generer_audio_gtts(texte: str) -> bytes | None:
    """Google TTS — fallback de dernier recours."""
    try:
        from gtts import gTTS
        tts = gTTS(text=texte, lang="fr", slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        audio = buf.read()
        logger.info(f"Audio gTTS généré ({len(audio)} octets) [fallback]")
        return audio
    except ImportError:
        logger.error("gTTS non installé")
        return None
    except Exception as e:
        logger.error(f"Exception gTTS: {e}")
        return None


async def generer_audio(texte: str) -> bytes | None:
    """
    Convertit du texte en audio MP3.
    Essaie ElevenLabs d'abord, puis edge-tts, puis gTTS en fallback.

    Args:
        texte : texte brut (markdown, emojis, etc. — nettoyé automatiquement)

    Returns:
        Octets MP3 ou None si tout échoue.
    """
    texte_propre = _nettoyer_texte_pour_tts(texte)
    if not texte_propre or len(texte_propre.strip()) < 2:
        return None

    audio = await _generer_audio_elevenlabs(texte_propre)
    if audio:
        return audio

    logger.warning("ElevenLabs échoué — essai edge-tts")
    audio = await _generer_audio_edge(texte_propre)
    if audio:
        return audio

    logger.warning("edge-tts échoué — essai gTTS")
    return await _generer_audio_gtts(texte_propre)
