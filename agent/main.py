import os
import re
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import PlainTextResponse, HTMLResponse, RedirectResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    limpiar_historial, obtener_historial_complet,
    enregistrer_prospect, mettre_a_jour_prospect, obtenir_tous_prospects,
    obtenir_stats_prospects, obtenir_prospect, obtenir_prospects_par_statut,
    guardar_souscription, obtenir_toutes_souscriptions, obtenir_souscription,
    mettre_a_jour_statut_paiement, obtenir_stats_souscriptions, supprimer_souscription,
    obtenir_conversations_recentes, est_bot_actif, pausar_bot, reanudar_bot,
)
from agent.providers import obtener_proveedor
from agent.providers.meta import ProveedorMeta

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("avent-immo")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "avent-admin-2024")

_messages_traites: set[str] = set()
_admin_sessions: set[str] = set()  # tokens de session admin


# ─── Helpers auth admin ───────────────────────────────────────────────────────

def admin_connecte(request: Request) -> bool:
    token = request.cookies.get("admin_token", "")
    return token in _admin_sessions


def require_admin(request: Request):
    if not admin_connecte(request):
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})


# ─── Détection auto souscription depuis réponse NAYA ─────────────────────────

SOUSCRIPTION_RE = re.compile(
    r"\[SOUSCRIT\|([^\]]+)\]", re.IGNORECASE
)

def extraire_souscription(texte: str) -> dict | None:
    """Extrait les champs d'un marqueur [SOUSCRIT|nom:X|lot:Y|...] dans la réponse de NAYA."""
    m = SOUSCRIPTION_RE.search(texte)
    if not m:
        return None
    data = {}
    for part in m.group(1).split("|"):
        if ":" in part:
            k, _, v = part.partition(":")
            data[k.strip()] = v.strip()
    return data if data else None


PROSPECT_RE = re.compile(r"\[PROSPECT\|([^\]]+)\]", re.IGNORECASE)

def extraire_mise_a_jour_prospect(texte: str) -> dict | None:
    m = PROSPECT_RE.search(texte)
    if not m:
        return None
    data = {}
    for part in m.group(1).split("|"):
        if ":" in part:
            k, _, v = part.partition(":")
            data[k.strip()] = v.strip()
    return data if data else None


def nettoyer_marqueurs(texte: str) -> str:
    """Supprime les marqueurs internes avant d'envoyer au client."""
    texte = SOUSCRIPTION_RE.sub("", texte)
    texte = PROSPECT_RE.sub("", texte)
    return texte.strip()


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info(f"NAYA — Avent IMMO Bot démarré sur le port {PORT}")
    yield


app = FastAPI(title="NAYA — Avent IMMO WhatsApp Agent", version="2.0.0", lifespan=lifespan)


# ─── Webhook WhatsApp ─────────────────────────────────────────────────────────

@app.get("/")
async def health_check():
    return {"status": "ok", "service": "avent-immo-bot", "agent": "NAYA"}


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
            if msg.es_propio:
                continue
            if msg.mensaje_id in _messages_traites:
                continue
            _messages_traites.add(msg.mensaje_id)
            if len(_messages_traites) > 2000:
                _messages_traites.clear()

            await enregistrer_prospect(msg.telefono)

            if not await est_bot_actif(msg.telefono):
                logger.info(f"Bot en pause pour {msg.telefono} — message ignoré")
                continue

            # ── Traitement selon le type de message ──────────────────────────
            texto_client = msg.texto  # texte visible dans l'historique
            c_est_vocal = False       # True si le client a envoyé un vocal

            if msg.tipo == "audio" and msg.media_id:
                # Télécharger et transcrire le message vocal
                logger.info(f"Message vocal de {msg.telefono} (media_id={msg.media_id})")
                if isinstance(proveedor, ProveedorMeta):
                    audio_bytes, mime = await proveedor.telecharger_media(msg.media_id)
                    if audio_bytes:
                        from agent.transcription import transcrire_audio
                        transcription = await transcrire_audio(audio_bytes, mime or msg.mime_type or "audio/ogg")
                        if transcription:
                            texto_client = f"[Message vocal] {transcription}"
                            c_est_vocal = True
                        else:
                            texto_client = "[Message vocal — transcription indisponible]"
                    else:
                        texto_client = "[Message vocal — téléchargement échoué]"

            elif msg.tipo == "image" and msg.media_id:
                logger.info(f"Image de {msg.telefono}")
                caption = msg.texto or ""
                if isinstance(proveedor, ProveedorMeta):
                    image_bytes, mime = await proveedor.telecharger_media(msg.media_id)
                    if image_bytes:
                        from agent.cloudinary_upload import uploader_image
                        img_url = await uploader_image(image_bytes, f"img_{uuid.uuid4().hex[:8]}.jpg")
                        if img_url and caption:
                            texto_client = f"[Image envoyée] {caption}\n(URL: {img_url})"
                        elif img_url:
                            texto_client = f"[Image envoyée] (URL: {img_url})"
                        else:
                            texto_client = f"[Image envoyée]{' — ' + caption if caption else ''}"
                    else:
                        texto_client = f"[Image envoyée]{' — ' + caption if caption else ''}"
                if not texto_client:
                    texto_client = "[Le client a envoyé une image]"

            elif msg.tipo == "video":
                caption = msg.texto or ""
                texto_client = f"[Vidéo envoyée]{' — ' + caption if caption else ''}"

            elif msg.tipo == "document":
                texto_client = f"[Document envoyé]{' — ' + msg.texto if msg.texto else ''}"

            # Ignorer si on n'a aucun texte à traiter
            if not texto_client:
                logger.info(f"Message sans texte ignoré ({msg.tipo}) de {msg.telefono}")
                continue

            logger.info(f"Message de {msg.telefono} ({msg.tipo}): {texto_client[:100]}")

            historial = await obtener_historial(msg.telefono)
            respuesta_brute = await generar_respuesta(texto_client, historial)

            souscription_data = extraire_souscription(respuesta_brute)
            if souscription_data:
                await guardar_souscription(msg.telefono, souscription_data)
                logger.info(f"Souscription enregistrée pour {msg.telefono}: {souscription_data}")

            prospect_data = extraire_mise_a_jour_prospect(respuesta_brute)
            if prospect_data:
                await mettre_a_jour_prospect(msg.telefono, **prospect_data)

            respuesta = nettoyer_marqueurs(respuesta_brute)

            await guardar_mensaje(msg.telefono, "user", texto_client)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # ── Envoi de la réponse ───────────────────────────────────────────
            if c_est_vocal:
                # Répondre en vocal si le client a envoyé un vocal
                envoi_vocal_ok = False
                try:
                    from agent.tts import generer_audio
                    from agent.cloudinary_upload import uploader_audio
                    audio_resp = await generer_audio(respuesta)
                    if audio_resp:
                        nom = f"naya_{uuid.uuid4().hex[:8]}.mp3"
                        audio_url = await uploader_audio(audio_resp, nom)
                        if audio_url:
                            envoi_vocal_ok = await proveedor.enviar_audio(msg.telefono, audio_url)
                except Exception as e_tts:
                    logger.error(f"Erreur TTS/upload: {e_tts}")
                if not envoi_vocal_ok:
                    # Fallback texte si la voix échoue
                    await proveedor.enviar_mensaje(msg.telefono, respuesta)
            else:
                await proveedor.enviar_mensaje(msg.telefono, respuesta)

            await mettre_a_jour_prospect(msg.telefono)
            logger.info(f"Réponse à {msg.telefono}: {respuesta[:80]}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Erreur webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Admin — Auth ─────────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if admin_connecte(request):
        return RedirectResponse("/admin")
    return HTMLResponse(LOGIN_HTML)


@app.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        token = secrets.token_hex(32)
        _admin_sessions.add(token)
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie("admin_token", token, httponly=True, max_age=86400 * 7)
        return response
    return HTMLResponse(LOGIN_HTML.replace("</form>", '<p style="color:red">Mot de passe incorrect</p></form>'))


@app.get("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("admin_token", "")
    _admin_sessions.discard(token)
    resp = RedirectResponse("/admin/login")
    resp.delete_cookie("admin_token")
    return resp


# ─── Admin — Dashboard ────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    require_admin(request)
    stats_p = await obtenir_stats_prospects()
    stats_s = await obtenir_stats_souscriptions()
    conversations = await obtenir_conversations_recentes(20)
    return HTMLResponse(render_dashboard(stats_p, stats_s, conversations))


# ─── Admin — Prospects ────────────────────────────────────────────────────────

@app.get("/admin/prospects", response_class=HTMLResponse)
async def admin_prospects(request: Request, statut: str = ""):
    require_admin(request)
    if statut:
        prospects = await obtenir_prospects_par_statut(statut)
        prospects_full = [await obtenir_prospect(p["telefono"]) for p in prospects]
        prospects_full = [p for p in prospects_full if p]
    else:
        prospects_full = await obtenir_tous_prospects()
    return HTMLResponse(render_prospects(prospects_full, statut))


@app.post("/admin/prospects/update/{telefono}")
async def admin_update_prospect(
    request: Request, telefono: str,
    nom: str = Form(""), statut: str = Form(""), interet: str = Form(""),
    budget: str = Form(""), localisation: str = Form(""), notes: str = Form("")
):
    require_admin(request)
    await mettre_a_jour_prospect(
        telefono, nom=nom or None, statut=statut or None,
        interet=interet or None, budget=budget or None,
        localisation=localisation or None, notes=notes or None,
    )
    return RedirectResponse(f"/admin/conversation/{telefono}", status_code=302)


# ─── Admin — Souscriptions ────────────────────────────────────────────────────

@app.get("/admin/souscriptions", response_class=HTMLResponse)
async def admin_souscriptions(request: Request):
    require_admin(request)
    souscriptions = await obtenir_toutes_souscriptions()
    return HTMLResponse(render_souscriptions(souscriptions))


@app.post("/admin/souscriptions/ajouter")
async def admin_ajouter_souscription(
    request: Request,
    telefono: str = Form(...), nom: str = Form(""), type_lot: str = Form(""),
    superficie: str = Form(""), prix_total: str = Form(""), mode_paiement: str = Form(""),
    acompte: str = Form(""), solde_restant: str = Form(""), notes: str = Form("")
):
    require_admin(request)
    data = {
        "nom": nom, "type_lot": type_lot, "superficie": superficie,
        "prix_total": prix_total, "mode_paiement": mode_paiement,
        "acompte": acompte, "solde_restant": solde_restant, "notes": notes,
        "statut_paiement": "en_cours" if mode_paiement == "echelonne" else "solde",
    }
    await guardar_souscription(telefono, data)
    return RedirectResponse("/admin/souscriptions", status_code=302)


@app.post("/admin/souscriptions/paiement/{sid}")
async def admin_paiement(
    request: Request, sid: int,
    statut: str = Form(...), notes: str = Form("")
):
    require_admin(request)
    await mettre_a_jour_statut_paiement(sid, statut, notes)
    return RedirectResponse("/admin/souscriptions", status_code=302)


@app.get("/admin/souscriptions/supprimer/{sid}")
async def admin_supprimer_souscription(request: Request, sid: int):
    require_admin(request)
    await supprimer_souscription(sid)
    return RedirectResponse("/admin/souscriptions", status_code=302)


# ─── Admin — Conversations ────────────────────────────────────────────────────

@app.get("/admin/conversation/{telefono}", response_class=HTMLResponse)
async def admin_conversation(request: Request, telefono: str):
    require_admin(request)
    try:
        messages = await obtenir_historial_complet(telefono)
        prospect = await obtenir_prospect(telefono)
        bot_actif = await est_bot_actif(telefono)
        return HTMLResponse(render_conversation(telefono, messages, prospect, bot_actif))
    except Exception as e:
        import traceback
        logger.error(f"Erreur conversation {telefono}: {traceback.format_exc()}")
        return HTMLResponse(f"<pre style='color:red;padding:20px'>ERREUR: {e}\n\n{traceback.format_exc()}</pre>", status_code=500)


@app.get("/admin/pause/{telefono}")
async def admin_pause(request: Request, telefono: str):
    require_admin(request)
    await pausar_bot(telefono, "Pause manuelle admin")
    return RedirectResponse(f"/admin/conversation/{telefono}", status_code=302)


@app.get("/admin/resume/{telefono}")
async def admin_resume(request: Request, telefono: str):
    require_admin(request)
    await reanudar_bot(telefono)
    return RedirectResponse(f"/admin/conversation/{telefono}", status_code=302)


@app.get("/admin/clear/{telefono}")
async def admin_clear(request: Request, telefono: str):
    require_admin(request)
    await limpiar_historial(telefono)
    return RedirectResponse(f"/admin/conversation/{telefono}", status_code=302)


@app.post("/admin/send/{telefono}")
async def admin_send(request: Request, telefono: str, message: str = Form(...)):
    require_admin(request)
    await proveedor.enviar_mensaje(telefono, message)
    await guardar_mensaje(telefono, "assistant", f"[Admin] {message}")
    return RedirectResponse(f"/admin/conversation/{telefono}", status_code=302)


# ─── Admin — Knowledge Base (system prompt en ligne) ─────────────────────────

@app.get("/admin/knowledge", response_class=HTMLResponse)
async def admin_knowledge(request: Request, msg: str = ""):
    require_admin(request)
    from agent.memory import obtener_config
    import yaml as _yaml
    # Charger valeurs depuis DB, sinon depuis prompts.yaml
    def _yaml_val(key: str) -> str:
        try:
            with open("config/prompts.yaml", "r", encoding="utf-8") as f:
                return (_yaml.safe_load(f) or {}).get(key, "")
        except Exception:
            return ""

    system_prompt = await obtener_config("system_prompt") or _yaml_val("system_prompt")
    fallback = await obtener_config("fallback_message") or _yaml_val("fallback_message")
    error_msg = await obtener_config("error_message") or _yaml_val("error_message")
    source = "🟢 Base en ligne (DB)" if await obtener_config("system_prompt") else "🟡 Base locale (prompts.yaml)"
    return HTMLResponse(render_knowledge(system_prompt, fallback, error_msg, source, msg))


@app.post("/admin/knowledge")
async def admin_knowledge_save(
    request: Request,
    system_prompt: str = Form(...),
    fallback_message: str = Form(""),
    error_message: str = Form(""),
):
    require_admin(request)
    from agent.memory import guardar_config
    await guardar_config("system_prompt", system_prompt.strip())
    if fallback_message.strip():
        await guardar_config("fallback_message", fallback_message.strip())
    if error_message.strip():
        await guardar_config("error_message", error_message.strip())
    logger.info("Knowledge base (system prompt) mis à jour en DB")
    return RedirectResponse("/admin/knowledge?msg=Base+de+connaissance+sauvegardée+en+ligne", status_code=302)


# ─── Campagne (relance prospects) ─────────────────────────────────────────────

@app.get("/campagne/contacts")
async def campagne_contacts(request: Request, token: str = "", statut: str = "chaud"):
    if token != os.getenv("CAMPAGNE_TOKEN", "avent-campagne-2025"):
        raise HTTPException(status_code=403, detail="Token invalide")
    prospects = await obtenir_prospects_par_statut(statut)
    return {"statut": statut, "total": len(prospects), "contacts": prospects}


# ═══════════════════════════════════════════════════════════════════════════════
# HTML — Templates
# ═══════════════════════════════════════════════════════════════════════════════

CSS_BASE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f0f2f5; color: #1a1a2e; }
  .navbar { background: linear-gradient(135deg, #1a1a2e, #16213e);
            color: white; padding: 14px 24px; display: flex;
            justify-content: space-between; align-items: center;
            box-shadow: 0 2px 8px rgba(0,0,0,.3); }
  .navbar a { color: #e0c97f; text-decoration: none; font-weight: 600; font-size: 14px; margin-left: 16px; }
  .navbar .brand { font-size: 18px; font-weight: 700; color: #e0c97f; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  .card { background: white; border-radius: 12px; padding: 20px;
          box-shadow: 0 2px 12px rgba(0,0,0,.08); margin-bottom: 20px; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; }
  .stat-card { background: linear-gradient(135deg, #1a1a2e, #16213e); color: white;
               border-radius: 12px; padding: 20px; text-align: center; }
  .stat-card .val { font-size: 36px; font-weight: 700; color: #e0c97f; }
  .stat-card .lbl { font-size: 12px; opacity: 0.8; margin-top: 4px; }
  .btn { display: inline-block; padding: 8px 16px; border-radius: 8px; border: none;
         cursor: pointer; font-size: 13px; font-weight: 600; text-decoration: none;
         transition: opacity .2s; }
  .btn:hover { opacity: 0.85; }
  .btn-gold { background: #e0c97f; color: #1a1a2e; }
  .btn-green { background: #25d366; color: white; }
  .btn-red { background: #e74c3c; color: white; }
  .btn-blue { background: #3498db; color: white; }
  .btn-gray { background: #95a5a6; color: white; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: #1a1a2e; color: #e0c97f; padding: 10px 12px; text-align: left; }
  td { padding: 10px 12px; border-bottom: 1px solid #f0f2f5; }
  tr:hover td { background: #fafafa; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 20px;
           font-size: 11px; font-weight: 600; }
  .badge-nouveau { background: #e8f4fd; color: #2980b9; }
  .badge-chaud { background: #fde8e8; color: #e74c3c; }
  .badge-tiede { background: #fef9e7; color: #e67e22; }
  .badge-froid { background: #eaecee; color: #7f8c8d; }
  .badge-converti { background: #e8f8f5; color: #27ae60; }
  .badge-solde { background: #e8f8f5; color: #27ae60; }
  .badge-en_cours { background: #fef9e7; color: #e67e22; }
  .badge-acompte_verse { background: #fde8e8; color: #e74c3c; }
  h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px; color: #1a1a2e; }
  input, select, textarea { width: 100%; padding: 8px 12px; border: 1px solid #ddd;
    border-radius: 8px; font-size: 13px; margin-top: 4px; }
  label { font-size: 12px; font-weight: 600; color: #555; display: block; margin-top: 10px; }
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
</style>
"""

LOGIN_HTML = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin — NAYA IMMO</title>{CSS_BASE}</head><body>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#1a1a2e,#16213e)">
<div style="background:white;border-radius:16px;padding:40px;width:360px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.4)">
  <div style="font-size:48px">🏛️</div>
  <h1 style="font-size:22px;margin:12px 0 4px;color:#1a1a2e">NAYA — Admin</h1>
  <p style="color:#7f8c8d;font-size:13px;margin-bottom:24px">AVENT GROUPE · Cité Présidentielle</p>
  <form method="POST" action="/admin/login">
    <input type="password" name="password" placeholder="Mot de passe admin" required
           style="margin-bottom:16px;padding:12px;font-size:14px">
    <button type="submit" class="btn btn-gold" style="width:100%;padding:12px;font-size:15px">
      Connexion
    </button>
  </form>
</div></div></body></html>"""


def nav(active: str = "") -> str:
    links = [
        ("/admin", "🏠 Dashboard"),
        ("/admin/prospects", "👥 Prospects"),
        ("/admin/souscriptions", "💰 Souscriptions"),
        ("/admin/knowledge", "📚 Knowledge"),
        ("/admin/logout", "🚪 Déconnexion"),
    ]
    items = "".join(f'<a href="{url}">{label}</a>' for url, label in links)
    return f"""<div class="navbar">
  <span class="brand">🏛️ NAYA IMMO Admin</span>
  <div>{items}</div>
</div>"""


def render_dashboard(stats_p: dict, stats_s: dict, conversations: list) -> str:
    rows = ""
    for c in conversations:
        badge = f'<span class="badge badge-{c["statut"]}">{c["statut"]}</span>'
        style = "style='color:#7f8c8d'" if c["role"] == "assistant" else ""
        rows += f"""<tr>
          <td><a href="/admin/conversation/{c['telefono']}" style="color:#e0c97f;font-weight:600">{c['telefono']}</a></td>
          <td>{c['nom']}</td>
          <td>{badge}</td>
          <td {style}>{c['dernier_message'][:70]}{'…' if len(c['dernier_message'])>70 else ''}</td>
          <td>{c['timestamp']}</td>
        </tr>"""
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard — NAYA Admin</title>{CSS_BASE}</head><body>
{nav()}
<div class="container">
  <div class="stats-grid" style="margin-bottom:24px">
    <div class="stat-card"><div class="val">{stats_p['total']}</div><div class="lbl">Prospects total</div></div>
    <div class="stat-card"><div class="val">{stats_p['chauds']}</div><div class="lbl">Prospects chauds</div></div>
    <div class="stat-card"><div class="val">{stats_p['convertis']}</div><div class="lbl">Convertis</div></div>
    <div class="stat-card"><div class="val">{stats_s['total']}</div><div class="lbl">Souscriptions</div></div>
    <div class="stat-card"><div class="val">{stats_s['soldes']}</div><div class="lbl">Lots soldés</div></div>
    <div class="stat-card"><div class="val">{stats_s['en_cours']}</div><div class="lbl">Paiements en cours</div></div>
  </div>
  <div class="card">
    <h2>💬 Conversations récentes</h2>
    <table>
      <tr><th>Téléphone</th><th>Nom</th><th>Statut</th><th>Dernier message</th><th>Date</th></tr>
      {rows or '<tr><td colspan="5" style="text-align:center;color:#7f8c8d;padding:20px">Aucune conversation</td></tr>'}
    </table>
  </div>
</div></body></html>"""


def render_prospects(prospects: list, filtre: str) -> str:
    statuts = ["nouveau", "chaud", "tiede", "froid", "converti"]
    filtres_html = '<a href="/admin/prospects" class="btn btn-gray" style="margin-right:8px">Tous</a>'
    for s in statuts:
        active = "btn-gold" if filtre == s else "btn-gray"
        filtres_html += f'<a href="/admin/prospects?statut={s}" class="btn {active}" style="margin-right:8px">{s.capitalize()}</a>'

    rows = ""
    for p in prospects:
        badge = f'<span class="badge badge-{p["statut"]}">{p["statut"]}</span>'
        rows += f"""<tr>
          <td><a href="/admin/conversation/{p['telefono']}" style="color:#1a1a2e;font-weight:600">{p['telefono']}</a></td>
          <td>{p['nom']}</td>
          <td>{badge}</td>
          <td>{p['interet']}</td>
          <td>{p['budget']}</td>
          <td>{p['localisation']}</td>
          <td>{p['derniere_activite']}</td>
          <td><a href="/admin/conversation/{p['telefono']}" class="btn btn-blue" style="font-size:11px">Voir</a></td>
        </tr>"""

    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prospects — NAYA Admin</title>{CSS_BASE}</head><body>
{nav()}
<div class="container">
  <div class="card">
    <h2>👥 Prospects ({len(prospects)})</h2>
    <div style="margin-bottom:16px">{filtres_html}</div>
    <table>
      <tr><th>Téléphone</th><th>Nom</th><th>Statut</th><th>Intérêt</th><th>Budget</th><th>Localisation</th><th>Dernière activité</th><th></th></tr>
      {rows or '<tr><td colspan="8" style="text-align:center;color:#7f8c8d;padding:20px">Aucun prospect</td></tr>'}
    </table>
  </div>
</div></body></html>"""


def render_souscriptions(souscriptions: list) -> str:
    rows = ""
    for s in souscriptions:
        badge = f'<span class="badge badge-{s["statut_paiement"]}">{s["statut_paiement"].replace("_"," ")}</span>'
        rows += f"""<tr>
          <td>{s['id']}</td>
          <td><a href="/admin/conversation/{s['telefono']}" style="color:#1a1a2e;font-weight:600">{s['telefono']}</a></td>
          <td>{s['nom']}</td>
          <td>{s['type_lot']}</td>
          <td>{s['superficie']}</td>
          <td>{s['prix_total']} FCFA</td>
          <td>{s['mode_paiement']}</td>
          <td>{s['acompte']} FCFA</td>
          <td>{badge}</td>
          <td>{s['date_souscription']}</td>
          <td>
            <form method="POST" action="/admin/souscriptions/paiement/{s['id']}" style="display:inline">
              <select name="statut" style="width:auto;padding:4px;font-size:11px">
                <option value="acompte_verse" {'selected' if s['statut_paiement']=='acompte_verse' else ''}>Acompte</option>
                <option value="en_cours" {'selected' if s['statut_paiement']=='en_cours' else ''}>En cours</option>
                <option value="solde" {'selected' if s['statut_paiement']=='solde' else ''}>Soldé</option>
              </select>
              <input type="hidden" name="notes" value="">
              <button type="submit" class="btn btn-gold" style="font-size:11px;padding:4px 8px">OK</button>
            </form>
            <a href="/admin/souscriptions/supprimer/{s['id']}" class="btn btn-red"
               style="font-size:11px;padding:4px 8px;margin-left:4px"
               onclick="return confirm('Supprimer ?')">✕</a>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Souscriptions — NAYA Admin</title>{CSS_BASE}</head><body>
{nav()}
<div class="container">
  <div class="card">
    <h2>➕ Ajouter une souscription manuellement</h2>
    <form method="POST" action="/admin/souscriptions/ajouter">
      <div class="form-grid">
        <div><label>Téléphone *</label><input name="telefono" required placeholder="+2250700000000"></div>
        <div><label>Nom client</label><input name="nom" placeholder="Kouamé Jean"></div>
        <div><label>Type de lot</label>
          <select name="type_lot">
            <option value="standard">Standard (500m²)</option>
            <option value="exception">Exception (400m²)</option>
          </select>
        </div>
        <div><label>Superficie</label><input name="superficie" placeholder="500m²"></div>
        <div><label>Prix total (FCFA)</label><input name="prix_total" placeholder="3500000"></div>
        <div><label>Mode paiement</label>
          <select name="mode_paiement">
            <option value="comptant">Comptant (-10%)</option>
            <option value="echelonne">Échelonné</option>
          </select>
        </div>
        <div><label>Acompte versé (FCFA)</label><input name="acompte" placeholder="1750000"></div>
        <div><label>Solde restant (FCFA)</label><input name="solde_restant" placeholder="1750000"></div>
      </div>
      <label>Notes</label>
      <textarea name="notes" rows="2" placeholder="Notes internes..."></textarea>
      <button type="submit" class="btn btn-gold" style="margin-top:12px">Enregistrer la souscription</button>
    </form>
  </div>
  <div class="card">
    <h2>💰 Souscriptions ({len(souscriptions)})</h2>
    <div style="overflow-x:auto">
    <table>
      <tr><th>#</th><th>Téléphone</th><th>Nom</th><th>Lot</th><th>Surface</th><th>Prix</th><th>Paiement</th><th>Acompte</th><th>Statut</th><th>Date</th><th>Actions</th></tr>
      {rows or '<tr><td colspan="11" style="text-align:center;color:#7f8c8d;padding:20px">Aucune souscription</td></tr>'}
    </table>
    </div>
  </div>
</div></body></html>"""


def render_conversation(telefono: str, messages: list, prospect: dict | None, bot_actif: bool) -> str:
    p = prospect or {}
    statuts = ["nouveau", "chaud", "tiede", "froid", "converti"]
    statut_sel = "".join(
        f'<option value="{s}" {"selected" if p.get("statut")==s else ""}>{s}</option>'
        for s in statuts
    )
    interets = ["standard", "exception", "les deux", "non précisé"]
    interet_sel = "".join(
        f'<option value="{i}" {"selected" if p.get("interet")==i else ""}>{i}</option>'
        for i in interets
    )
    budgets = ["comptant", "echelonne", "non précisé"]
    budget_sel = "".join(
        f'<option value="{b}" {"selected" if p.get("budget")==b else ""}>{b}</option>'
        for b in budgets
    )

    msgs_html = ""
    for m in messages:
        align = "flex-end" if m["role"] == "assistant" else "flex-start"
        bg = "#e0c97f" if m["role"] == "assistant" else "#f0f2f5"
        color = "#1a1a2e" if m["role"] == "assistant" else "#333"
        label = "NAYA" if m["role"] == "assistant" else "Client"
        msgs_html += f"""<div style="display:flex;justify-content:{align};margin-bottom:10px">
          <div style="max-width:70%;background:{bg};color:{color};padding:10px 14px;border-radius:12px;font-size:13px">
            <div style="font-size:10px;opacity:.7;margin-bottom:4px">{label} · {m['timestamp']}</div>
            {m['content'].replace(chr(10), '<br>')}
          </div>
        </div>"""

    pause_btn = (
        f'<a href="/admin/resume/{telefono}" class="btn btn-green">▶ Reprendre bot</a>'
        if not bot_actif else
        f'<a href="/admin/pause/{telefono}" class="btn btn-gray">⏸ Pause bot</a>'
    )
    bot_badge = (
        '<span class="badge" style="background:#e8f8f5;color:#27ae60">🤖 Bot actif</span>'
        if bot_actif else
        '<span class="badge" style="background:#fde8e8;color:#e74c3c">⏸ Bot en pause</span>'
    )

    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Conv {telefono} — NAYA Admin</title>{CSS_BASE}</head><body>
{nav()}
<div class="container">
  <div style="display:grid;grid-template-columns:340px 1fr;gap:20px">
    <!-- Panneau prospect -->
    <div>
      <div class="card">
        <h2>👤 Prospect</h2>
        <p style="font-size:14px;font-weight:600;margin-bottom:4px">{telefono}</p>
        <div style="margin-bottom:12px">{bot_badge}</div>
        <form method="POST" action="/admin/prospects/update/{telefono}">
          <label>Nom</label>
          <input name="nom" value="{p.get('nom','')}" placeholder="Nom du prospect">
          <label>Statut</label>
          <select name="statut">{statut_sel}</select>
          <label>Intérêt (type de lot)</label>
          <select name="interet">{interet_sel}</select>
          <label>Budget préféré</label>
          <select name="budget">{budget_sel}</select>
          <label>Localisation</label>
          <input name="localisation" value="{p.get('localisation','')}" placeholder="Abidjan / diaspora...">
          <label>Notes internes</label>
          <textarea name="notes" rows="3">{p.get('notes','')}</textarea>
          <button type="submit" class="btn btn-gold" style="width:100%;margin-top:12px">Enregistrer</button>
        </form>
        <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
          {pause_btn}
          <a href="/admin/clear/{telefono}" class="btn btn-red"
             onclick="return confirm('Effacer l historique ?')">🗑 Effacer</a>
        </div>
      </div>
      <div class="card">
        <h2>✉️ Envoyer un message</h2>
        <form method="POST" action="/admin/send/{telefono}">
          <textarea name="message" rows="3" placeholder="Message à envoyer au client..."></textarea>
          <button type="submit" class="btn btn-green" style="width:100%;margin-top:8px">Envoyer via WhatsApp</button>
        </form>
      </div>
    </div>
    <!-- Conversation -->
    <div class="card" style="display:flex;flex-direction:column">
      <h2>💬 Conversation ({len(messages)} messages)</h2>
      <div style="flex:1;overflow-y:auto;max-height:70vh;padding:8px;background:#f9f9f9;border-radius:8px">
        {msgs_html or '<p style="text-align:center;color:#7f8c8d;padding:20px">Aucun message</p>'}
      </div>
    </div>
  </div>
</div></body></html>"""


def render_knowledge(system_prompt: str, fallback: str, error_msg: str, source: str, msg: str) -> str:
    msg_html = f'<div style="background:#e8f8f5;color:#27ae60;padding:10px 14px;border-radius:8px;margin-bottom:16px">{msg}</div>' if msg else ""
    sp_safe = system_prompt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    fb_safe = fallback.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    em_safe = error_msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Base de connaissance — NAYA Admin</title>{CSS_BASE}</head><body>
{nav()}
<div class="container">
  {msg_html}
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2>📚 Base de connaissance de NAYA</h2>
      <span style="font-size:13px;font-weight:600">{source}</span>
    </div>
    <p style="font-size:13px;color:#7f8c8d;margin-bottom:20px">
      Le contenu sauvegardé ici est stocké en base de données et utilisé <strong>en priorité</strong>
      sur le fichier local <code>prompts.yaml</code>. Modifiez le texte ci-dessous et cliquez sur
      <em>Sauvegarder</em> — NAYA utilisera immédiatement la nouvelle version.
    </p>
    <form method="POST" action="/admin/knowledge">
      <label style="font-size:13px;font-weight:700;color:#1a1a2e;display:block;margin-bottom:6px">
        🧠 System prompt (instructions principales de NAYA)
      </label>
      <textarea name="system_prompt" rows="28"
        style="font-family:monospace;font-size:12px;line-height:1.6;border:2px solid #e0c97f;border-radius:8px;padding:12px"
      >{sp_safe}</textarea>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
        <div>
          <label style="font-size:12px;font-weight:700;color:#1a1a2e;display:block;margin-bottom:6px">
            💬 Message de fallback (si NAYA ne comprend pas)
          </label>
          <textarea name="fallback_message" rows="3"
            style="font-family:monospace;font-size:12px;border:1px solid #ddd;border-radius:8px;padding:10px"
          >{fb_safe}</textarea>
        </div>
        <div>
          <label style="font-size:12px;font-weight:700;color:#1a1a2e;display:block;margin-bottom:6px">
            ⚠️ Message d'erreur (si problème technique)
          </label>
          <textarea name="error_message" rows="3"
            style="font-family:monospace;font-size:12px;border:1px solid #ddd;border-radius:8px;padding:10px"
          >{em_safe}</textarea>
        </div>
      </div>

      <div style="margin-top:20px;display:flex;gap:12px;align-items:center">
        <button type="submit" class="btn btn-gold" style="padding:12px 28px;font-size:14px">
          💾 Sauvegarder en ligne
        </button>
        <span style="font-size:12px;color:#7f8c8d">
          La base en ligne est prioritaire sur le fichier local.
        </span>
      </div>
    </form>
  </div>
</div></body></html>"""
