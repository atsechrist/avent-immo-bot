import os
import logging
import httpx

logger = logging.getLogger("avent-immo")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIF_EMAIL    = os.getenv("NOTIF_EMAIL", "atsechrist@gmail.com")
FROM_EMAIL     = "NAYA — AVENT GROUPE <onboarding@resend.dev>"


async def _envoyer(sujet: str, corps_html: str) -> bool:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY non configuré — email ignoré")
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": FROM_EMAIL,
                    "to": [NOTIF_EMAIL],
                    "subject": sujet,
                    "html": corps_html,
                },
            )
        if r.status_code in (200, 201):
            logger.info(f"Email envoyé via Resend : {sujet}")
            return True
        logger.error(f"Erreur Resend {r.status_code}: {r.text}")
        return False
    except Exception as e:
        logger.error(f"Exception envoi email Resend: {e}")
        return False


async def envoyer_email_nouveau_rdv(
    nom: str, telephone: str, date_rdv: str, heure_rdv: str, objet: str
) -> bool:
    sujet = f"📅 Nouveau RDV — {nom} ({telephone})"
    corps = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;color:#e0c97f;padding:20px;border-radius:8px 8px 0 0">
    <h2 style="margin:0">🏛️ NAYA — Nouveau Rendez-vous</h2>
  </div>
  <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px">
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr><td style="padding:8px;font-weight:bold;color:#555">Prospect</td><td style="padding:8px">{nom}</td></tr>
      <tr style="background:#fff"><td style="padding:8px;font-weight:bold;color:#555">Téléphone</td><td style="padding:8px">{telephone}</td></tr>
      <tr><td style="padding:8px;font-weight:bold;color:#555">Date</td><td style="padding:8px">{date_rdv}</td></tr>
      <tr style="background:#fff"><td style="padding:8px;font-weight:bold;color:#555">Heure</td><td style="padding:8px">{heure_rdv}</td></tr>
      <tr><td style="padding:8px;font-weight:bold;color:#555">Objet</td><td style="padding:8px">{objet}</td></tr>
    </table>
    <p style="margin-top:16px;font-size:13px;color:#777">
      Le RDV a été enregistré et ajouté à Google Calendar automatiquement.
      Le prospect recevra des rappels WhatsApp 48h et 24h avant.
    </p>
  </div>
</div>
"""
    return await _envoyer(sujet, corps)


async def envoyer_email_statut_rdv(
    nom: str, telephone: str, date_rdv: str, heure_rdv: str,
    statut: str, nouveau_rdv: str = ""
) -> bool:
    emoji   = {"reporte": "🔄", "annule": "❌", "confirme": "✅"}.get(statut, "ℹ️")
    libelle = {"reporte": "REPORTÉ", "annule": "ANNULÉ", "confirme": "CONFIRMÉ"}.get(statut, statut.upper())
    sujet   = f"{emoji} RDV {libelle} — {nom} ({telephone})"
    nouveau_row = (
        f'<tr><td style="padding:8px;font-weight:bold;color:#555">Nouveau créneau</td>'
        f'<td style="padding:8px;color:#27ae60;font-weight:bold">{nouveau_rdv}</td></tr>'
        if nouveau_rdv else ""
    )
    corps = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;color:#e0c97f;padding:20px;border-radius:8px 8px 0 0">
    <h2 style="margin:0">{emoji} NAYA — RDV {libelle}</h2>
  </div>
  <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px">
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr><td style="padding:8px;font-weight:bold;color:#555">Prospect</td><td style="padding:8px">{nom}</td></tr>
      <tr style="background:#fff"><td style="padding:8px;font-weight:bold;color:#555">Téléphone</td><td style="padding:8px">{telephone}</td></tr>
      <tr><td style="padding:8px;font-weight:bold;color:#555">Date prévue</td><td style="padding:8px">{date_rdv} à {heure_rdv}</td></tr>
      <tr style="background:#fff"><td style="padding:8px;font-weight:bold;color:#555">Statut</td>
          <td style="padding:8px;font-weight:bold;color:{'#e74c3c' if statut=='annule' else '#e67e22'}">{libelle}</td></tr>
      {nouveau_row}
    </table>
    <p style="margin-top:16px;font-size:13px;color:#777">
      {"NAYA renégocie un nouveau créneau avec le prospect." if statut in ("reporte", "annule") else ""}
    </p>
  </div>
</div>
"""
    return await _envoyer(sujet, corps)


async def envoyer_email_dday(rdvs: list[dict]) -> bool:
    if not rdvs:
        return True
    sujet = f"☀️ {len(rdvs)} RDV aujourd'hui — AVENT GROUPE"
    lignes = "".join(
        f"""<tr style="{'background:#fff' if i % 2 == 0 else ''}">
          <td style="padding:10px;font-weight:bold">{r['heure_rdv']}</td>
          <td style="padding:10px">{r['nom']}</td>
          <td style="padding:10px">{r['telephone']}</td>
          <td style="padding:10px;color:#7f8c8d">{r['objet']}</td>
        </tr>"""
        for i, r in enumerate(rdvs)
    )
    corps = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;color:#e0c97f;padding:20px;border-radius:8px 8px 0 0">
    <h2 style="margin:0">☀️ Rendez-vous du jour — AVENT GROUPE</h2>
  </div>
  <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px">
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead>
        <tr style="background:#16213e;color:#e0c97f">
          <th style="padding:10px;text-align:left">Heure</th>
          <th style="padding:10px;text-align:left">Prospect</th>
          <th style="padding:10px;text-align:left">Téléphone</th>
          <th style="padding:10px;text-align:left">Objet</th>
        </tr>
      </thead>
      <tbody>{lignes}</tbody>
    </table>
    <p style="margin-top:16px;font-size:13px;color:#777">
      Contactez chaque prospect ce matin pour confirmer sa présence avant le RDV.
    </p>
  </div>
</div>
"""
    return await _envoyer(sujet, corps)
