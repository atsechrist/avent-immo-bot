import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("avent-immo")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Déduplication des messages déjà traités
_messages_traites: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info(f"Avent IMMO Bot démarré sur le port {PORT}")
    yield


app = FastAPI(title="Avent IMMO — WhatsApp AI Agent", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "avent-immo-bot"}


@app.get("/webhook")
async def webhook_verification(request: Request):
    result = await proveedor.validar_webhook(request)
    if result is not None:
        return PlainTextResponse(str(result))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        mensajes = await proveedor.parsear_webhook(request)
        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue
            if msg.mensaje_id in _messages_traites:
                continue
            _messages_traites.add(msg.mensaje_id)
            if len(_messages_traites) > 2000:
                _messages_traites.clear()

            logger.info(f"Message de {msg.telefono}: {msg.texto}")
            historial = await obtener_historial(msg.telefono)
            respuesta = await generar_respuesta(msg.texto, historial)
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)
            await proveedor.enviar_mensaje(msg.telefono, respuesta)
            logger.info(f"Réponse à {msg.telefono}: {respuesta}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Erreur webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
