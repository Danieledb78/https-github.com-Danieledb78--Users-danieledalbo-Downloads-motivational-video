"""
bot_campagna.py — Handler Telegram per Campaign Agent e Market Intelligence
Da importare in bot.py con:

    from bot_campagna import registra_handlers, job_mercato, job_sequenze

Poi in main():
    registra_handlers(app)
    app.job_queue.run_daily(job_mercato,  time=dt_time(8, 0), days=(1,))  # martedì
    app.job_queue.run_daily(job_sequenze, time=dt_time(8, 30), days=tuple(range(7)))
"""

import os, asyncio, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import time as dt_time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

import campagna_manager as cm
import agent_mercato

log = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=2)

OWNER_ID   = int(os.environ.get("TELEGRAM_OWNER_ID", "0"))
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")

# Importa EMAIL_ACCOUNTS e invia_email da bot.py al runtime
# (si accede via closure al modulo bot importato)
_bot_module = None
def init(bot_mod):
    global _bot_module
    _bot_module = bot_mod

def get_email_accounts():
    return _bot_module.EMAIL_ACCOUNTS if _bot_module else []

def send_email(account, to, subj, body):
    return _bot_module.invia_email(account, to, subj, body)


# ---------------------------------------------------------------------------
# Sessioni campagna (separate da quelle appunti in bot.py)
# ---------------------------------------------------------------------------
camp_sessions: dict[int, dict] = {}

def csess(chat_id: int) -> dict:
    if chat_id not in camp_sessions:
        camp_sessions[chat_id] = {"step": "idle", "camp_id": None, "email_idx": 0}
    return camp_sessions[chat_id]

def creset(chat_id: int):
    camp_sessions.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Tastiere
# ---------------------------------------------------------------------------
AZIENDE = ["Renergy Project&Build", "ACM&Partners", "BevManager", "Combinata"]

def kb_aziende() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(a, callback_data=f"camp_az_{i}")]
        for i, a in enumerate(AZIENDE)
    ])

def kb_email_account() -> InlineKeyboardMarkup:
    accounts = get_email_accounts()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📤 {a['label']}", callback_data=f"camp_acc_{i}")]
        for i, a in enumerate(accounts)
    ])

def kb_approva_email(tipo: str, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva", callback_data=f"camp_email_ok_{tipo}_{idx}"),
        InlineKeyboardButton("✏️ Modifica", callback_data=f"camp_email_mod_{tipo}_{idx}"),
    ]])

def kb_conferma_lead(camp_id: str, n: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Usa questi {n} lead", callback_data=f"camp_lead_ok_{camp_id}"),
        InlineKeyboardButton("🔄 Cerca altri",          callback_data=f"camp_lead_retry_{camp_id}"),
        InlineKeyboardButton("❌ Annulla",              callback_data="camp_annulla"),
    ]])

def kb_campagne_lista(campagne: list) -> InlineKeyboardMarkup:
    bottoni = [
        [InlineKeyboardButton(
            f"{'✅' if c['stato']=='attiva' else '📋'} {c['azienda'][:15]} — {c['offerta'][:20]}",
            callback_data=f"camp_stats_{c['id']}"
        )]
        for c in campagne[:8]
    ]
    bottoni.append([InlineKeyboardButton("➕ Nuova campagna", callback_data="camp_nuova")])
    return InlineKeyboardMarkup(bottoni)


# ---------------------------------------------------------------------------
# /nuova_campagna — wizard di creazione
# ---------------------------------------------------------------------------
async def cmd_nuova_campagna(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    creset(update.effective_chat.id)
    s = csess(update.effective_chat.id)
    s["step"] = "scegli_azienda"
    await update.message.reply_text(
        "🚀 *Nuova Campagna*\n\nPer quale azienda?",
        reply_markup=kb_aziende(), parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# /campagne — lista campagne esistenti
# ---------------------------------------------------------------------------
async def cmd_campagne(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    tutte = cm.get_tutte_campagne()
    if not tutte:
        await update.message.reply_text(
            "📋 Nessuna campagna ancora.\nUsa /nuova\\_campagna per crearne una.",
            parse_mode="Markdown"
        )
        return
    await update.message.reply_text(
        f"📋 *Campagne ({len(tutte)}):*",
        reply_markup=kb_campagne_lista(tutte),
        parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# /mercato — lancia agente market intelligence on-demand
# ---------------------------------------------------------------------------
async def cmd_mercato(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    msg = await update.message.reply_text("📡 Monitoraggio mercato in corso...")
    loop = asyncio.get_event_loop()
    _, briefing = await loop.run_in_executor(executor, agent_mercato.run)
    await msg.edit_text(briefing, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Handler testo per wizard campagna
# ---------------------------------------------------------------------------
async def handler_testo_campagna(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Gestisce i messaggi di testo relativi alla creazione campagna.
    Ritorna True se il messaggio è stato gestito, False altrimenti.
    """
    if update.effective_user.id != OWNER_ID:
        return False
    chat_id = update.effective_chat.id
    s = csess(chat_id)
    testo = update.message.text.strip()

    if s["step"] == "attendi_target":
        s["target"] = testo
        s["step"]   = "attendi_offerta"
        await update.message.reply_text(
            "🎯 *Qual è l'offerta o l'obiettivo della campagna?*\n\n"
            "Es: _Valutazione ESG gratuita_, _Audit energetico senza impegno_, "
            "_Prima consulenza gratuita_...",
            parse_mode="Markdown"
        )
        return True

    if s["step"] == "attendi_offerta":
        s["offerta"] = testo
        await genera_campagna_e_mostra(update, chat_id)
        return True

    if s["step"] == "attendi_modifica_email":
        tipo = s.get("modifica_tipo", "")
        idx  = s.get("email_idx", 0)
        cm.approva_email(s["camp_id"], tipo, corpo=testo)
        s["step"] = "review_email"
        await mostra_prossima_email(update, chat_id, idx + 1)
        return True

    return False


# ---------------------------------------------------------------------------
# Logica wizard interna
# ---------------------------------------------------------------------------
async def genera_campagna_e_mostra(update: Update, chat_id: int):
    s = csess(chat_id)
    msg = await update.effective_message.reply_text("⏳ Genero la sequenza email con Claude...")

    camp = cm.nuova_campagna(
        azienda=s["azienda"],
        from_account_idx=s.get("account_idx", 0),
        target=s["target"],
        offerta=s["offerta"],
    )
    s["camp_id"] = camp["id"]

    loop = asyncio.get_event_loop()
    try:
        sequenza = await loop.run_in_executor(executor, cm.genera_sequenza_email, camp["id"])
    except Exception as e:
        await msg.edit_text(f"❌ Errore generazione email: {e}")
        return

    await msg.edit_text(
        f"✅ Sequenza di 3 email generata per *{s['azienda']}*\n"
        f"Offerta: _{s['offerta']}_\n\nRevisionamole una per una:",
        parse_mode="Markdown"
    )
    s["step"]      = "review_email"
    s["email_idx"] = 0
    await mostra_prossima_email(update, chat_id, 0)


async def mostra_prossima_email(update: Update, chat_id: int, idx: int):
    s    = csess(chat_id)
    camp = cm.get_campagna(s["camp_id"])
    if not camp:
        return

    seq = camp.get("email_sequence", [])
    if idx >= len(seq):
        # Tutte le email revisionate
        if cm.tutte_approvate(s["camp_id"]):
            await update.effective_message.reply_text(
                "✅ *Tutte le email approvate!*\n\nOra cerco i lead per questa campagna...",
                parse_mode="Markdown"
            )
            s["step"] = "cerca_lead"
            await cerca_e_mostra_lead(update, chat_id)
        else:
            await update.effective_message.reply_text(
                "⚠️ Alcune email non sono ancora approvate. Torna indietro con /campagne."
            )
        return

    email   = seq[idx]
    tipo    = email["tipo"]
    label   = {"cold_email": "📧 Email 1 — Primo contatto",
                "followup_1": "📧 Email 2 — Follow-up (giorno 3-4)",
                "followup_2": "📧 Email 3 — Ultimo contatto (giorno 8-10)"}

    testo = (f"{label.get(tipo, tipo)} *({idx+1}/3)*\n\n"
             f"*Oggetto:* {email['oggetto']}\n\n"
             f"─────────────────\n{email['corpo']}\n─────────────────")

    s["email_idx"] = idx
    await update.effective_message.reply_text(
        testo, reply_markup=kb_approva_email(tipo, idx), parse_mode="Markdown"
    )


async def cerca_e_mostra_lead(update: Update, chat_id: int):
    s   = csess(chat_id)
    msg = await update.effective_message.reply_text("🔍 Apollo cerca i lead per questo target...")
    loop = asyncio.get_event_loop()
    try:
        lead_list = await loop.run_in_executor(
            executor, cm.cerca_lead_per_campagna, s["camp_id"], 25
        )
    except Exception as e:
        await msg.edit_text(f"❌ Errore ricerca lead: {e}")
        return

    if not lead_list:
        await msg.edit_text("⚠️ Nessun lead trovato per questo target. Prova a modificare la campagna.")
        return

    s["lead_trovati"] = lead_list
    anteprima = "\n".join(
        f"• {l.get('name','?')} — {l.get('city','?')} ({l.get('industry','?')})"
        for l in lead_list[:10]
    )
    if len(lead_list) > 10:
        anteprima += f"\n...e altri {len(lead_list)-10}"

    await msg.edit_text(
        f"✅ *Trovati {len(lead_list)} lead*\n\n{anteprima}\n\nConfermi?",
        reply_markup=kb_conferma_lead(s["camp_id"], len(lead_list)),
        parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# Invio sequenze (chiamato dal job giornaliero e da /run_all)
# ---------------------------------------------------------------------------
async def esegui_sequenze(ctx_or_bot, chat_id: int):
    """Processa tutti i follow-up schedulati per le campagne attive."""
    from bot import invia_email, EMAIL_ACCOUNTS
    campagne = cm.get_campagne_attive()
    totale_inviate = 0

    for camp in campagne:
        camp_id  = camp["id"]
        seq_map  = {e["tipo"]: e for e in camp.get("email_sequence", []) if e.get("approvata")}
        account  = EMAIL_ACCOUNTS[camp.get("from_account_idx", 0)] if EMAIL_ACCOUNTS else None
        if not account:
            continue

        da_fare = cm.get_lead_da_processare(camp_id)

        for tipo, lead_list in da_fare.items():
            email_tmpl = seq_map.get(tipo)
            if not email_tmpl:
                continue
            for lead in lead_list:
                if not lead.get("email"):
                    continue
                try:
                    corpo = email_tmpl["corpo"].replace(
                        "{nome_referente}", lead.get("referente") or "Gentile"
                    ).replace(
                        "{nome_azienda}", lead.get("azienda", "")
                    )
                    invia_email(account, lead["email"], email_tmpl["oggetto"], corpo)
                    cm.segna_inviata(camp_id, lead["id"], tipo)
                    totale_inviate += 1
                    await asyncio.sleep(2)   # pausa anti-spam
                except Exception as e:
                    log.error(f"Errore invio {lead['email']}: {e}")

    return totale_inviate


# ---------------------------------------------------------------------------
# Callback handler per campagne
# ---------------------------------------------------------------------------
async def handler_callback_campagna(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Gestisce i callback relativi alle campagne.
    Ritorna True se gestito, False altrimenti.
    """
    if update.effective_user.id != OWNER_ID:
        return False
    q       = update.callback_query
    chat_id = update.effective_chat.id
    data    = q.data
    s       = csess(chat_id)

    if not data.startswith("camp_"):
        return False

    await q.answer()

    # Selezione azienda
    if data.startswith("camp_az_"):
        idx = int(data.split("_")[2])
        s["azienda"] = AZIENDE[idx]
        s["step"]    = "scegli_account"
        accounts = get_email_accounts()
        if not accounts:
            await q.edit_message_text("⚠️ Nessun account email configurato.")
            return True
        await q.edit_message_text(
            f"✅ Azienda: *{s['azienda']}*\n\nDa quale account invii?",
            reply_markup=kb_email_account(), parse_mode="Markdown"
        )
        return True

    # Selezione account
    if data.startswith("camp_acc_"):
        idx = int(data.split("_")[2])
        s["account_idx"] = idx
        s["step"] = "attendi_target"
        await q.edit_message_text(
            f"✅ Account: *{get_email_accounts()[idx]['label']}*\n\n"
            "📋 *Descrivi il target della campagna:*\n\n"
            "Es: _PMI manifatturiere Nord Italia con 20-100 dipendenti_\n"
            "_Aziende agricole Veneto con capannoni_\n"
            "_Startup italiane tech che cercano investitori_",
            parse_mode="Markdown"
        )
        return True

    # Approva email
    if data.startswith("camp_email_ok_"):
        parti = data.split("_")
        tipo  = parti[3]
        idx   = int(parti[4])
        cm.approva_email(s["camp_id"], tipo)
        await q.edit_message_text(f"✅ Email {idx+1}/3 approvata.")
        await mostra_prossima_email(update, chat_id, idx + 1)
        return True

    # Modifica email
    if data.startswith("camp_email_mod_"):
        parti = data.split("_")
        tipo  = parti[3]
        idx   = int(parti[4])
        s["modifica_tipo"] = tipo
        s["email_idx"]     = idx
        s["step"]          = "attendi_modifica_email"
        await q.edit_message_text(
            "✏️ Invia il nuovo testo del *corpo* dell'email (solo il corpo):",
            parse_mode="Markdown"
        )
        return True

    # Conferma lead
    if data.startswith("camp_lead_ok_"):
        camp_id   = data.split("_")[3]
        lead_list = s.get("lead_trovati", [])
        n = cm.aggiungi_lead_campagna(camp_id, lead_list)
        await q.edit_message_text(
            f"🚀 *Campagna attivata!*\n\n"
            f"✅ {n} lead aggiunti alla sequenza\n"
            f"Le email partiranno automaticamente ogni mattina.\n\n"
            f"Usa /campagne per monitorare lo stato.",
            parse_mode="Markdown"
        )
        creset(chat_id)
        return True

    # Riprova ricerca lead
    if data.startswith("camp_lead_retry_"):
        await q.edit_message_text("🔄 Riprovo la ricerca...")
        await cerca_e_mostra_lead(update, chat_id)
        return True

    # Stats campagna
    if data.startswith("camp_stats_"):
        camp_id = data.split("_")[2]
        testo   = cm.formatta_riepilogo_campagna(camp_id)
        await q.edit_message_text(testo, parse_mode="Markdown")
        return True

    # Nuova campagna da lista
    if data == "camp_nuova":
        await q.edit_message_text("Usa /nuova\\_campagna per creare una nuova campagna.", parse_mode="Markdown")
        return True

    # Annulla
    if data == "camp_annulla":
        creset(chat_id)
        await q.edit_message_text("🗑 Annullato.")
        return True

    return False


# ---------------------------------------------------------------------------
# Job schedulati
# ---------------------------------------------------------------------------
async def job_mercato(ctx: ContextTypes.DEFAULT_TYPE):
    """Martedì 08:00 — briefing market intelligence."""
    log.info("Job: agente mercato")
    loop = asyncio.get_event_loop()
    _, briefing = await loop.run_in_executor(executor, agent_mercato.run)
    await ctx.bot.send_message(OWNER_ID, briefing, parse_mode="Markdown")


async def job_sequenze(ctx: ContextTypes.DEFAULT_TYPE):
    """Ogni giorno 08:30 — invia follow-up schedulati."""
    log.info("Job: esecuzione sequenze campagne")
    n = await esegui_sequenze(ctx, OWNER_ID)
    if n > 0:
        await ctx.bot.send_message(
            OWNER_ID,
            f"📤 Sequenze: {n} email inviate automaticamente oggi.",
        )


# ---------------------------------------------------------------------------
# Registrazione handlers in bot.py
# ---------------------------------------------------------------------------
def registra_handlers(app: Application):
    app.add_handler(CommandHandler("nuova_campagna", cmd_nuova_campagna))
    app.add_handler(CommandHandler("campagne",       cmd_campagne))
    app.add_handler(CommandHandler("mercato",        cmd_mercato))
    # I callback e i messaggi testo vengono intercettati
    # dai handler esistenti in bot.py che chiamano
    # handler_callback_campagna() e handler_testo_campagna()
    log.info("Handler campagne registrati")
