import os
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Boolean, func, desc, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./avent_immo.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversacionEstado(Base):
    """État du bot par numéro (actif ou en pause manuelle)."""
    __tablename__ = "conversacion_estado"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    bot_activo: Mapped[bool] = mapped_column(Boolean, default=True)
    pausado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    motivo_pausa: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Prospect(Base):
    """Chaque contact WhatsApp qui interagit avec NAYA."""
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nom: Mapped[str | None] = mapped_column(String(100), nullable=True)
    localisation: Mapped[str | None] = mapped_column(String(100), nullable=True)  # pays/ville
    interet: Mapped[str | None] = mapped_column(String(50), nullable=True)  # standard / exception
    budget: Mapped[str | None] = mapped_column(String(50), nullable=True)   # comptant / echelonne
    statut: Mapped[str] = mapped_column(String(30), default="nouveau")  # nouveau, chaud, tiede, froid, converti
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    derniere_activite: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Souscription(Base):
    """Client ayant confirmé l'achat d'un lot (a versé un acompte ou payé comptant)."""
    __tablename__ = "souscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    nom: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    type_lot: Mapped[str | None] = mapped_column(String(30), nullable=True)   # standard / exception
    superficie: Mapped[str | None] = mapped_column(String(20), nullable=True) # 500m2 / 400m2
    prix_total: Mapped[str | None] = mapped_column(String(30), nullable=True)  # ex: 3500000
    mode_paiement: Mapped[str | None] = mapped_column(String(30), nullable=True)  # comptant / echelonne
    acompte: Mapped[str | None] = mapped_column(String(30), nullable=True)     # montant versé
    solde_restant: Mapped[str | None] = mapped_column(String(30), nullable=True)
    statut_paiement: Mapped[str] = mapped_column(String(30), default="acompte_verse")  # acompte_verse, en_cours, solde
    date_souscription: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BotConfig(Base):
    """Clé-valeur pour stocker le system prompt et autres configs en ligne."""
    __tablename__ = "bot_config"

    cle: Mapped[str] = mapped_column(String(100), primary_key=True)
    valeur: Mapped[str] = mapped_column(Text, nullable=True)


# ─── Initialisation DB ────────────────────────────────────────────────────────

async def inicializar_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for col_sql in [
        "ALTER TABLE mensajes ADD COLUMN wamid VARCHAR(200)",
        "ALTER TABLE prospects ADD COLUMN localisation VARCHAR(100)",
        "ALTER TABLE prospects ADD COLUMN budget VARCHAR(50)",
    ]:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(col_sql))
        except Exception:
            pass  # colonne déjà présente


# ─── Config (system prompt en ligne) ─────────────────────────────────────────

async def obtener_config(cle: str) -> str | None:
    async with async_session() as session:
        result = await session.execute(select(BotConfig).where(BotConfig.cle == cle))
        cfg = result.scalar_one_or_none()
        return cfg.valeur if cfg else None


async def guardar_config(cle: str, valeur: str):
    async with async_session() as session:
        result = await session.execute(select(BotConfig).where(BotConfig.cle == cle))
        cfg = result.scalar_one_or_none()
        if cfg:
            cfg.valeur = valeur
        else:
            session.add(BotConfig(cle=cle, valeur=valeur))
        await session.commit()


# ─── Messages ─────────────────────────────────────────────────────────────────

async def guardar_mensaje(telefono: str, role: str, content: str):
    async with async_session() as session:
        session.add(Mensaje(telefono=telefono, role=role, content=content, timestamp=datetime.utcnow()))
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        return [{"role": m.role, "content": m.content} for m in reversed(result.scalars().all())]


async def limpiar_historial(telefono: str):
    async with async_session() as session:
        result = await session.execute(select(Mensaje).where(Mensaje.telefono == telefono))
        for m in result.scalars().all():
            await session.delete(m)
        await session.commit()


async def obtener_historial_complet(telefono: str) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Mensaje).where(Mensaje.telefono == telefono).order_by(Mensaje.timestamp.asc())
        )
        return [{"role": m.role, "content": m.content, "timestamp": m.timestamp.strftime("%d/%m %H:%M")}
                for m in result.scalars().all()]


# ─── Bot état (pause / resume) ────────────────────────────────────────────────

async def est_bot_actif(telefono: str) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(ConversacionEstado).where(ConversacionEstado.telefono == telefono)
        )
        estado = result.scalar_one_or_none()
        return estado.bot_activo if estado else True


async def pausar_bot(telefono: str, motivo: str = ""):
    async with async_session() as session:
        result = await session.execute(
            select(ConversacionEstado).where(ConversacionEstado.telefono == telefono)
        )
        estado = result.scalar_one_or_none()
        if estado:
            estado.bot_activo = False
            estado.pausado_en = datetime.utcnow()
            estado.motivo_pausa = motivo
        else:
            session.add(ConversacionEstado(
                telefono=telefono, bot_activo=False,
                pausado_en=datetime.utcnow(), motivo_pausa=motivo
            ))
        await session.commit()


async def reanudar_bot(telefono: str):
    async with async_session() as session:
        result = await session.execute(
            select(ConversacionEstado).where(ConversacionEstado.telefono == telefono)
        )
        estado = result.scalar_one_or_none()
        if estado:
            estado.bot_activo = True
            estado.motivo_pausa = None
        else:
            session.add(ConversacionEstado(telefono=telefono, bot_activo=True))
        await session.commit()


# ─── Prospects ────────────────────────────────────────────────────────────────

async def enregistrer_prospect(telefono: str):
    """Crée un prospect s'il n'existe pas encore."""
    async with async_session() as session:
        result = await session.execute(select(Prospect).where(Prospect.telefono == telefono))
        if not result.scalar_one_or_none():
            session.add(Prospect(telefono=telefono))
            await session.commit()


async def mettre_a_jour_prospect(telefono: str, **kwargs):
    """Met à jour les champs d'un prospect (nom, statut, interet, budget, notes, localisation)."""
    async with async_session() as session:
        result = await session.execute(select(Prospect).where(Prospect.telefono == telefono))
        p = result.scalar_one_or_none()
        if not p:
            p = Prospect(telefono=telefono)
            session.add(p)
        for k, v in kwargs.items():
            if hasattr(p, k) and v is not None:
                setattr(p, k, v)
        p.derniere_activite = datetime.utcnow()
        await session.commit()


async def obtenir_tous_prospects() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(select(Prospect).order_by(desc(Prospect.derniere_activite)))
        return [
            {
                "id": p.id, "telefono": p.telefono, "nom": p.nom or "—",
                "statut": p.statut, "interet": p.interet or "—",
                "budget": p.budget or "—", "localisation": p.localisation or "—",
                "notes": p.notes or "",
                "created_at": p.created_at.strftime("%d/%m/%Y"),
                "derniere_activite": p.derniere_activite.strftime("%d/%m/%Y %H:%M"),
            }
            for p in result.scalars().all()
        ]


async def obtenir_stats_prospects() -> dict:
    async with async_session() as session:
        # Ne compter que les prospects ayant au moins 1 message
        sous_requete = select(Mensaje.telefono).distinct()
        avec_messages = select(func.count(Prospect.id)).where(
            Prospect.telefono.in_(sous_requete)
        )
        total = (await session.execute(avec_messages)).scalar() or 0
        chauds = (await session.execute(
            select(func.count(Prospect.id))
            .where(Prospect.statut == "chaud")
            .where(Prospect.telefono.in_(sous_requete))
        )).scalar() or 0
        convertis = (await session.execute(
            select(func.count(Prospect.id))
            .where(Prospect.statut == "converti")
            .where(Prospect.telefono.in_(sous_requete))
        )).scalar() or 0
        return {"total": total, "chauds": chauds, "convertis": convertis}


async def obtenir_prospect(telefono: str) -> dict | None:
    async with async_session() as session:
        result = await session.execute(select(Prospect).where(Prospect.telefono == telefono))
        p = result.scalar_one_or_none()
        if not p:
            return None
        return {
            "id": p.id, "telefono": p.telefono, "nom": p.nom or "",
            "statut": p.statut, "interet": p.interet or "",
            "budget": p.budget or "", "localisation": p.localisation or "",
            "notes": p.notes or "",
            "created_at": p.created_at.strftime("%d/%m/%Y"),
            "derniere_activite": p.derniere_activite.strftime("%d/%m/%Y %H:%M"),
        }


async def obtenir_prospects_par_statut(statut: str) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Prospect).where(Prospect.statut == statut).order_by(desc(Prospect.derniere_activite))
        )
        return [{"telefono": p.telefono, "nom": p.nom or "—"} for p in result.scalars().all()]


# ─── Souscriptions ────────────────────────────────────────────────────────────

async def guardar_souscription(telefono: str, data: dict):
    """Enregistre ou met à jour une souscription pour un client."""
    async with async_session() as session:
        result = await session.execute(select(Souscription).where(Souscription.telefono == telefono))
        s = result.scalar_one_or_none()
        if s:
            for k, v in data.items():
                if hasattr(s, k) and v:
                    setattr(s, k, v)
        else:
            s = Souscription(telefono=telefono, **{k: v for k, v in data.items() if hasattr(Souscription, k)})
            session.add(s)
        await session.commit()
    # Marquer le prospect comme converti
    await mettre_a_jour_prospect(telefono, statut="converti")


async def obtenir_toutes_souscriptions() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(select(Souscription).order_by(desc(Souscription.date_souscription)))
        return [
            {
                "id": s.id, "telefono": s.telefono, "nom": s.nom or "—",
                "type_lot": s.type_lot or "—", "superficie": s.superficie or "—",
                "prix_total": s.prix_total or "—", "mode_paiement": s.mode_paiement or "—",
                "acompte": s.acompte or "—", "solde_restant": s.solde_restant or "—",
                "statut_paiement": s.statut_paiement,
                "date_souscription": s.date_souscription.strftime("%d/%m/%Y"),
                "notes": s.notes or "",
            }
            for s in result.scalars().all()
        ]


async def obtenir_souscription(souscription_id: int) -> dict | None:
    async with async_session() as session:
        result = await session.execute(select(Souscription).where(Souscription.id == souscription_id))
        s = result.scalar_one_or_none()
        if not s:
            return None
        return {
            "id": s.id, "telefono": s.telefono, "nom": s.nom or "",
            "type_lot": s.type_lot or "", "superficie": s.superficie or "",
            "prix_total": s.prix_total or "", "mode_paiement": s.mode_paiement or "",
            "acompte": s.acompte or "", "solde_restant": s.solde_restant or "",
            "statut_paiement": s.statut_paiement, "notes": s.notes or "",
            "date_souscription": s.date_souscription.strftime("%d/%m/%Y"),
        }


async def mettre_a_jour_statut_paiement(souscription_id: int, statut: str, notes: str = ""):
    async with async_session() as session:
        result = await session.execute(select(Souscription).where(Souscription.id == souscription_id))
        s = result.scalar_one_or_none()
        if s:
            s.statut_paiement = statut
            if notes:
                s.notes = (s.notes or "") + f"\n[{datetime.utcnow().strftime('%d/%m/%Y')}] {notes}"
            await session.commit()


async def obtenir_stats_souscriptions() -> dict:
    async with async_session() as session:
        total = (await session.execute(func.count(Souscription.id))).scalar() or 0
        soldes = (await session.execute(
            select(func.count(Souscription.id)).where(Souscription.statut_paiement == "solde")
        )).scalar() or 0
        en_cours = (await session.execute(
            select(func.count(Souscription.id)).where(Souscription.statut_paiement == "en_cours")
        )).scalar() or 0
        return {"total": total, "soldes": soldes, "en_cours": en_cours,
                "acompte_seul": total - soldes - en_cours}


async def supprimer_souscription(souscription_id: int):
    async with async_session() as session:
        result = await session.execute(select(Souscription).where(Souscription.id == souscription_id))
        s = result.scalar_one_or_none()
        if s:
            await session.delete(s)
            await session.commit()


# ─── Stats globales dashboard ─────────────────────────────────────────────────

async def obtenir_conversations_recentes(limite: int = 30) -> list[dict]:
    """Retourne les numéros ayant eu une activité récente, avec dernier message."""
    async with async_session() as session:
        subq = (
            select(Mensaje.telefono, func.max(Mensaje.timestamp).label("last_ts"))
            .group_by(Mensaje.telefono)
            .order_by(desc("last_ts"))
            .limit(limite)
            .subquery()
        )
        result = await session.execute(select(subq))
        rows = result.fetchall()
        out = []
        for r in rows:
            tel = r[0]
            ts = r[1]
            # Dernier message
            last_msg = await session.execute(
                select(Mensaje)
                .where(Mensaje.telefono == tel)
                .order_by(desc(Mensaje.timestamp))
                .limit(1)
            )
            m = last_msg.scalar_one_or_none()
            # Statut prospect
            p_res = await session.execute(select(Prospect).where(Prospect.telefono == tel))
            p = p_res.scalar_one_or_none()
            out.append({
                "telefono": tel,
                "nom": p.nom if p and p.nom else "—",
                "statut": p.statut if p else "nouveau",
                "dernier_message": m.content[:80] if m else "",
                "role": m.role if m else "",
                "timestamp": ts.strftime("%d/%m %H:%M") if isinstance(ts, datetime) else str(ts)[:16],
            })
        return out
