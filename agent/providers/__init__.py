import os
from agent.providers.base import ProveedorWhatsApp


def obtener_proveedor() -> ProveedorWhatsApp:
    proveedor = os.getenv("WHATSAPP_PROVIDER", "").lower()
    if not proveedor:
        raise ValueError("WHATSAPP_PROVIDER non configuré dans .env")
    if proveedor == "meta":
        from agent.providers.meta import ProveedorMeta
        return ProveedorMeta()
    raise ValueError(f"Proveedor non supporté: {proveedor}")
