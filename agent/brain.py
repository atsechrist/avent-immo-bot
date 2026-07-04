import os
import yaml
import logging
from mistralai import Mistral
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("avent-immo")

client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))


def _lire_yaml() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


async def cargar_system_prompt() -> str:
    """Lit le system prompt depuis la DB en priorité, sinon prompts.yaml."""
    from agent.memory import obtener_config
    valeur = await obtener_config("system_prompt")
    if valeur:
        return valeur
    return _lire_yaml().get("system_prompt", "Tu es NAYA, assistante immobilière d'AVENT GROUPE. Réponds en français.")


async def _obtener_fallback() -> str:
    from agent.memory import obtener_config
    valeur = await obtener_config("fallback_message")
    if valeur:
        return valeur
    return _lire_yaml().get("fallback_message", "Je n'ai pas bien compris. Pouvez-vous reformuler ?")


async def _obtener_error() -> str:
    from agent.memory import obtener_config
    valeur = await obtener_config("error_message")
    if valeur:
        return valeur
    return _lire_yaml().get("error_message", "Problème technique, veuillez réessayer dans quelques instants.")


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    if not mensaje or len(mensaje.strip()) < 2:
        return await _obtener_fallback()

    system_prompt = await cargar_system_prompt()

    messages = [{"role": "system", "content": system_prompt}]
    for m in historial:
        if m.get("content"):
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
        return await _obtener_error()
