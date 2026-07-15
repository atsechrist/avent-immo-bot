# agent/calendar_tool.py — Intégration Google Calendar pour NAYA
# AVENT IMMO Bot — Prise de RDV au siège

"""
Crée des rendez-vous dans Google Calendar via un Service Account.

Setup requis :
  1. Activer Google Calendar API dans Google Cloud Console
  2. Créer un Service Account → télécharger le JSON
  3. Partager le calendrier cible avec l'email du Service Account
  4. Uploader le JSON comme Secret File sur Render : /etc/secrets/google_credentials.json
  5. Ajouter GOOGLE_CALENDAR_ID dans les variables d'env Render (email du calendrier ou "primary")
"""

import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("avent-immo")

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "/etc/secrets/google_credentials.json")
CALENDAR_ID      = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TIMEZONE         = os.getenv("CALENDAR_TIMEZONE", "Africa/Abidjan")
HEURE_DEBUT      = int(os.getenv("CALENDAR_HEURE_DEBUT", "9"))
HEURE_FIN        = int(os.getenv("CALENDAR_HEURE_FIN", "18"))
DUREE_RDV_MIN    = int(os.getenv("CALENDAR_DUREE_RDV", "60"))


def _get_service():
    """Service Google Calendar authentifié via Service Account."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds)


async def creer_rdv(
    nom: str,
    telephone: str,
    date: str,
    heure: str,
    objet: str = "Visite agence / Closing",
) -> dict:
    """
    Crée un RDV dans Google Calendar.

    Args:
        nom       : Nom du client
        telephone : Numéro WhatsApp du client
        date      : "YYYY-MM-DD"
        heure     : "HH:MM"
        objet     : Motif du RDV

    Returns:
        {"success": True, "event_id": ..., "lien": ...}
        ou {"success": False, "erreur": ...}
    """
    try:
        tz = ZoneInfo(TIMEZONE)
        dt_debut = datetime.strptime(f"{date} {heure}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        dt_fin   = dt_debut + timedelta(minutes=DUREE_RDV_MIN)

        service = _get_service()
        event = {
            "summary": f"RDV {nom} — {objet}",
            "description": (
                f"👤 Client : {nom}\n"
                f"📱 Téléphone : {telephone}\n"
                f"📋 Objet : {objet}\n\n"
                f"✅ Prise de RDV automatique via NAYA (WhatsApp)"
            ),
            "start": {"dateTime": dt_debut.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": dt_fin.isoformat(),   "timeZone": TIMEZONE},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email",  "minutes": 60},
                    {"method": "popup",  "minutes": 30},
                ],
            },
        }

        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        logger.info(f"📅 RDV créé: {nom} le {date} à {heure} (id={result.get('id')})")
        return {"success": True, "event_id": result.get("id"), "lien": result.get("htmlLink", "")}

    except FileNotFoundError:
        logger.error(f"Credentials Google non trouvés : {CREDENTIALS_FILE}")
        return {"success": False, "erreur": "credentials_manquants"}
    except Exception as e:
        logger.error(f"Erreur création RDV Google Calendar: {e}")
        return {"success": False, "erreur": str(e)}


async def obtenir_creneaux_disponibles(date_debut: str, nb_jours: int = 4) -> list[dict]:
    """
    Retourne jusqu'à 6 créneaux libres sur nb_jours à partir de date_debut.

    Args:
        date_debut : "YYYY-MM-DD"
        nb_jours   : Nombre de jours à scanner (défaut: 4)

    Returns:
        [{"date": "2025-01-15", "heure": "10:00", "label": "Mercredi 15 jan à 10h"}, ...]
    """
    try:
        tz        = ZoneInfo(TIMEZONE)
        now       = datetime.now(tz)
        dt_debut  = datetime.strptime(date_debut, "%Y-%m-%d").replace(tzinfo=tz)
        dt_fin    = dt_debut + timedelta(days=nb_jours)

        service = _get_service()

        # Créneaux occupés via freebusy
        freebusy = service.freebusy().query(body={
            "timeMin":  dt_debut.isoformat(),
            "timeMax":  dt_fin.isoformat(),
            "timeZone": TIMEZONE,
            "items":    [{"id": CALENDAR_ID}],
        }).execute()
        busy_periods = freebusy.get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])

        JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        MOIS_FR  = ["jan", "fév", "mar", "avr", "mai", "jun", "jul", "aoû", "sep", "oct", "nov", "déc"]

        creneaux = []
        current = dt_debut
        while current < dt_fin and len(creneaux) < 6:
            # Pas le dimanche
            if current.weekday() < 6:
                for h in range(HEURE_DEBUT, HEURE_FIN):
                    slot_debut = current.replace(hour=h, minute=0, second=0, microsecond=0)
                    slot_fin   = slot_debut + timedelta(minutes=DUREE_RDV_MIN)

                    if slot_debut <= now:
                        continue

                    libre = all(
                        not (slot_debut < datetime.fromisoformat(b["end"]).astimezone(tz)
                             and slot_fin > datetime.fromisoformat(b["start"]).astimezone(tz))
                        for b in busy_periods
                    )

                    if libre:
                        label = f"{JOURS_FR[slot_debut.weekday()]} {slot_debut.day} {MOIS_FR[slot_debut.month - 1]} à {h}h"
                        creneaux.append({
                            "date":  slot_debut.strftime("%Y-%m-%d"),
                            "heure": slot_debut.strftime("%H:%M"),
                            "label": label,
                        })
                        if len(creneaux) >= 6:
                            break
            current += timedelta(days=1)

        return creneaux

    except Exception as e:
        logger.error(f"Erreur obtenir créneaux disponibles: {e}")
        return []
