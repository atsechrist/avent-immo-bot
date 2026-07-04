# agent/tts.py — Synthèse vocale (Text-To-Speech)
# NAYA — Avent IMMO Bot

"""
Convertit du texte en audio pour les réponses vocales WhatsApp.

Priorité :
  1. edge-tts (Microsoft Neural TTS) — gratuit, voix très naturelle, aucune clé requise
     Voix : fr-FR-DeniseNeural (féminine) ou fr-FR-HenriNeural (masculine)
  2. gTTS (Google) — fallback gratuit de dernier recours
"""

import io
import os
import re
import logging

logger = logging.getLogger("avent-immo")

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
            logger.info(f"Audio edge-tts généré ({len(audio_data)} octets)")
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
    Essaie edge-tts d'abord, puis gTTS en fallback.

    Args:
        texte : texte brut (markdown, emojis, etc. — nettoyé automatiquement)

    Returns:
        Octets MP3 ou None si tout échoue.
    """
    texte_propre = _nettoyer_texte_pour_tts(texte)
    if not texte_propre or len(texte_propre.strip()) < 2:
        return None

    audio = await _generer_audio_edge(texte_propre)
    if audio:
        return audio

    logger.warning("edge-tts échoué — essai gTTS")
    return await _generer_audio_gtts(texte_propre)
