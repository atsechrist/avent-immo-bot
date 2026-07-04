from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from fastapi import Request


@dataclass
class MensajeEntrante:
    telefono: str
    texto: str
    mensaje_id: str
    es_propio: bool
    tipo: str = "text"           # text | audio | image | video | document
    media_id: str | None = None  # ID média Meta pour télécharger
    mime_type: str | None = None # MIME type du média


class ProveedorWhatsApp(ABC):

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        ...

    async def validar_webhook(self, request: Request) -> dict | int | None:
        return None

    async def enviar_audio(self, telefono: str, audio_url: str) -> bool:
        """Envoie un message audio. À surcharger par les providers qui le supportent."""
        return False
