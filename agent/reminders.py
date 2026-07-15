import asyncio
import logging
from datetime import datetime, date

logger = logging.getLogger("avent-immo")


async def boucle_rappels():
    """Boucle fond — vérifie toutes les 30 min les RDVs à rappeler."""
    logger.info("Boucle rappels RDV démarrée")
    while True:
        try:
            await _verifier_rappels()
        except Exception as e:
            logger.error(f"Erreur boucle rappels: {e}")
        await asyncio.sleep(1800)  # 30 minutes


async def _verifier_rappels():
    from agent.memory import (
        obtenir_rdvs_proches, obtenir_rdvs_du_jour,
        marquer_rappel_48h, marquer_rappel_24h, marquer_rappel_dday,
    )
    from agent.email_notif import envoyer_email_dday
    from agent.providers import obtener_proveedor

    proveedor = obtener_proveedor()
    maintenant = datetime.utcnow()  # Africa/Abidjan = UTC+0

    # ── Rappels 48H ──────────────────────────────────────────────────────────
    for rdv in await obtenir_rdvs_proches(heures_min=47, heures_max=49):
        if not rdv["rappel_48h_envoye"]:
            nom_affiche = rdv["nom"] or "cher(e) client(e)"
            msg = (
                f"Bonjour {nom_affiche} ! Je suis NAYA, conseillère d'AVENT GROUPE. "
                f"Je vous rappelle votre rendez-vous en agence prévu le {rdv['date_rdv']} "
                f"à {rdv['heure_rdv']}. Confirmez-vous votre présence ?"
            )
            await proveedor.enviar_mensaje(rdv["telephone"], msg)
            await marquer_rappel_48h(rdv["id"])
            logger.info(f"Rappel 48H → {rdv['telephone']}")

    # ── Rappels 24H ──────────────────────────────────────────────────────────
    for rdv in await obtenir_rdvs_proches(heures_min=23, heures_max=25):
        if not rdv["rappel_24h_envoye"]:
            nom_affiche = rdv["nom"] or "cher(e) client(e)"
            msg = (
                f"Bonjour {nom_affiche} ! Votre rendez-vous avec l'équipe AVENT GROUPE "
                f"est demain {rdv['date_rdv']} à {rdv['heure_rdv']}. "
                f"Êtes-vous toujours disponible ?"
            )
            await proveedor.enviar_mensaje(rdv["telephone"], msg)
            await marquer_rappel_24h(rdv["id"])
            logger.info(f"Rappel 24H → {rdv['telephone']}")

    # ── Email J-Jour (fenêtre 7h-9h UTC) ────────────────────────────────────
    if 7 <= maintenant.hour <= 9:
        rdvs_jour = await obtenir_rdvs_du_jour()
        a_notifier = [r for r in rdvs_jour if not r["rappel_dday_envoye"]]
        if a_notifier:
            await envoyer_email_dday(a_notifier)
            for rdv in a_notifier:
                await marquer_rappel_dday(rdv["id"])
            logger.info(f"Email J-Jour envoyé : {len(a_notifier)} RDV(s)")
