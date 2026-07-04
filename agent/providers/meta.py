import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("avent-immo")


class ProveedorMeta(ProveedorWhatsApp):

    def __init__(self):
        self.access_token = os.getenv("META_ACCESS_TOKEN")
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "avent-immo-verify")
        self.api_version = "v21.0"

    async def validar_webhook(self, request: Request) -> dict | int | None:
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == self.verify_token:
            return int(challenge)
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        body = await request.json()
        mensajes = []
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    msg_type = msg.get("type", "text")
                    telefono = msg.get("from", "")
                    msg_id = msg.get("id", "")

                    if msg_type == "text":
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=msg.get("text", {}).get("body", ""),
                            mensaje_id=msg_id,
                            es_propio=False,
                            tipo="text",
                        ))
                    elif msg_type == "audio":
                        audio = msg.get("audio", {})
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto="",
                            mensaje_id=msg_id,
                            es_propio=False,
                            tipo="audio",
                            media_id=audio.get("id"),
                            mime_type=audio.get("mime_type", "audio/ogg"),
                        ))
                    elif msg_type == "image":
                        image = msg.get("image", {})
                        caption = image.get("caption", "")
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=caption,
                            mensaje_id=msg_id,
                            es_propio=False,
                            tipo="image",
                            media_id=image.get("id"),
                            mime_type=image.get("mime_type", "image/jpeg"),
                        ))
                    elif msg_type == "video":
                        video = msg.get("video", {})
                        caption = video.get("caption", "")
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=caption,
                            mensaje_id=msg_id,
                            es_propio=False,
                            tipo="video",
                            media_id=video.get("id"),
                            mime_type=video.get("mime_type", "video/mp4"),
                        ))
                    elif msg_type == "document":
                        doc = msg.get("document", {})
                        caption = doc.get("caption", "") or doc.get("filename", "")
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=caption,
                            mensaje_id=msg_id,
                            es_propio=False,
                            tipo="document",
                            media_id=doc.get("id"),
                            mime_type=doc.get("mime_type", "application/octet-stream"),
                        ))
        return mensajes

    async def telecharger_media(self, media_id: str) -> tuple[bytes, str]:
        """
        Télécharge un fichier média depuis Meta.
        Retourne (octets, mime_type) ou (b"", "") en cas d'erreur.
        """
        if not self.access_token:
            return b"", ""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Étape 1 : obtenir l'URL de téléchargement
                r = await client.get(
                    f"https://graph.facebook.com/{self.api_version}/{media_id}",
                    headers=headers,
                )
                if r.status_code != 200:
                    logger.error(f"Erreur récup URL média {r.status_code}: {r.text[:200]}")
                    return b"", ""
                data = r.json()
                media_url = data.get("url", "")
                mime_type = data.get("mime_type", "")

                # Étape 2 : télécharger le fichier
                r2 = await client.get(media_url, headers=headers)
                if r2.status_code != 200:
                    logger.error(f"Erreur téléchargement média {r2.status_code}")
                    return b"", ""
                logger.info(f"Média téléchargé ({len(r2.content)} octets, {mime_type})")
                return r2.content, mime_type
        except Exception as e:
            logger.error(f"Exception téléchargement média: {e}")
            return b"", ""

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN ou META_PHONE_NUMBER_ID non configurés")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Erreur Meta API: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_audio(self, telefono: str, audio_url: str) -> bool:
        """Envoie un message audio via une URL publique (Cloudinary)."""
        if not self.access_token or not self.phone_number_id:
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "audio",
            "audio": {"link": audio_url},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Erreur envoi audio Meta: {r.status_code} — {r.text}")
            return r.status_code == 200
