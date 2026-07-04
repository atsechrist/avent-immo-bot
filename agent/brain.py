import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("avent-immo")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_config() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    config = cargar_config()
    system_prompt = config.get("system_prompt", "Tu es un assistant immobilier professionnel. Réponds toujours en français.")
    fallback = config.get("fallback_message", "Je n'ai pas bien compris. Pouvez-vous reformuler ?")
    error_msg = config.get("error_message", "Problème technique, veuillez réessayer.")

    if not mensaje or len(mensaje.strip()) < 2:
        return fallback

    mensajes = [{"role": m["role"], "content": m["content"]} for m in historial]
    mensajes.append({"role": "user", "content": mensaje})

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes,
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Erreur Claude API: {e}")
        return error_msg
