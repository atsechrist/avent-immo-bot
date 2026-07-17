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


_INSTRUCTIONS_RDV = """

=== OBJECTIF PRIORITAIRE — PRISE DE RDV ===

Ton objectif ultime est de convertir chaque prospect en rendez-vous en agence.
Dès que tu as répondu à toutes ses questions, propose systématiquement un RDV avec cette phrase EXACTE :
"Souhaitez-vous aussi que je vous réserve un rendez-vous en agence avec notre équipe commerciale afin de répondre à toutes les questions subsidiaires que vous aurez et vous donner l'occasion de faire l'acquisition de votre lot ?"

Règles pour la prise de RDV :
- Propose ce RDV UNE SEULE FOIS par conversation, après avoir répondu aux questions du prospect.
- Si le prospect accepte, demande-lui : son prénom (si inconnu), la date souhaitée, l'heure souhaitée.
- Une fois les informations confirmées, génère CE MARQUEUR INVISIBLE dans ta réponse :
  [RDV|nom:PRENOM|date:YYYY-MM-DD|heure:HH:MM|objet:Visite agence et acquisition lot]
- Puis confirme chaleureusement : "Parfait [PRENOM] ! Votre rendez-vous est bien réservé pour le [date] à [heure]. Notre équipe vous attend avec impatience !"

Gestion des changements de RDV (suite à tes rappels ou spontanément) :
- Si le prospect veut REPORTER : montre de la compréhension, propose 2-3 nouveaux créneaux, et une fois le nouveau créneau choisi, génère : [RDV_UPDATE|statut:reporte|date:YYYY-MM-DD|heure:HH:MM]
- Si le prospect veut ANNULER : reste positif et flexible, propose de rescheduler plus tard, génère : [RDV_UPDATE|statut:annule]
- Ces marqueurs sont automatiquement retirés du message visible par le client.
"""


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    if not mensaje or len(mensaje.strip()) < 2:
        return await _obtener_fallback()

    from datetime import datetime
    import locale
    try:
        locale.setlocale(locale.LC_TIME, 'fr_FR.UTF-8')
    except Exception:
        pass
    maintenant = datetime.now()
    jours_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois_fr = ["janvier", "février", "mars", "avril", "mai", "juin",
               "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    date_str = f"{jours_fr[maintenant.weekday()]} {maintenant.day} {mois_fr[maintenant.month - 1]} {maintenant.year}"

    _contexte_date = f"""

CONTEXTE TEMPOREL (injecté automatiquement)
Aujourd'hui nous sommes le {date_str}.

PRISE DE RDV — RÈGLES ABSOLUES
Ton objectif est de valider un RDV avec chaque prospect. Voici comment procéder :

1. Demande au prospect ses disponibilités : "Quel jour et quelle heure vous conviendraient ?"
2. Avant de confirmer, vérifie que la date proposée est valide selon les règles ci-dessous.
3. Si la date est invalide, explique poliment pourquoi et propose le prochain créneau disponible.
4. Une fois la date validée, confirme chaleureusement et enregistre le marqueur [RDV|...].

JOURS ET HORAIRES D'OUVERTURE D'AVENT GROUPE :
- Lundi au vendredi : 8h00 à 18h00
- Samedi : 9h00 à 13h00 UNIQUEMENT (pas de RDV samedi après-midi)
- Dimanche : FERMÉ

DATES INVALIDES — refuser poliment et proposer une alternative :
- Dimanche (jour = dimanche)
- Samedi après 13h00
- Jours fériés en Côte d'Ivoire 2026 :
  * 1er janvier (Jour de l'An)
  * 20 mars (Aïd el-Fitr approximatif)
  * 6 avril (Lundi de Pâques)
  * 1er mai (Fête du Travail)
  * 14 mai (Ascension)
  * 25 mai (Lundi de Pentecôte)
  * 27 mai (Aïd el-Adha approximatif)
  * 7 août (Fête Nationale)
  * 15 août (Assomption)
  * 9 septembre (Mawlid approximatif)
  * 1er novembre (Toussaint)
  * 25 décembre (Noël)

Exemple de refus : "Ce jour est férié / nous sommes fermés le dimanche / nous fermons à 13h le samedi. Je vous propose plutôt [date alternative valide]."
"""

    system_prompt = await cargar_system_prompt()
    system_prompt = system_prompt + _INSTRUCTIONS_RDV + _contexte_date

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
