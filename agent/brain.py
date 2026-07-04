import os
import yaml
import logging
from mistralai import Mistral
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("avent-immo")

client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))


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

    messages = [{"role": "system", "content": system_prompt}]
    for m in historial:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": mensaje})

    try:
        response = await client.chat.complete_async(
            model="mistral-large-latest",
            messages=messages,
            max_tokens=1024,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Erreur Mistral API: {e}")
        return error_msg
