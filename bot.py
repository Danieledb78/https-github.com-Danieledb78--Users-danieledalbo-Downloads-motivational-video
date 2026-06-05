"""
bot.py — Bot Telegram Unificato
Combina: agenti lead generation + appunti vocali → email + briefing automatico

Comandi disponibili:
  /run_all    → lancia Renergy + ACM, manda briefing al termine
  /renergy    → solo agente Renergy
  /acm        → solo agente ACM
  /lead       → ultimi lead creati in Hub
  /stato      → stato sistema e account configurati
  /mittente   → cambia account email di invio
  /annulla    → annulla sessione corrente

Messaggio vocale o testo libero → estrae azioni → bozze email → invio approvato

Secrets Replit:
  TELEGRAM_BOT_TOKEN     TELEGRAM_OWNER_ID
  OPENAI_API_KEY         CLAUDE_API_KEY
  APOLLO_API_KEY         GOOGLE_MAPS_KEY
  HUB_EMAIL              HUB_PASSWORD
  EMAIL_1_LABEL          EMAIL_1_ADDRESS      EMAIL_1_PASSWORD
  EMAIL_1_SMTP_HOST      EMAIL_1_SMTP_PORT    EMAIL_1_SSL
  EMAIL_2_LABEL ...  (aggiungi quanti account vuoi)
"""

import os, sys, json, smtplib, tempfile, logging, asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dt_time

import anthropic
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

import agent_renergy
import agent_acm
import bot_campagna

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config globale
# ---------------------------------------------------------------------------
BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID   = int(os.environ.get("TELEGRAM_OWNER_ID", "0"))
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
HUB_BASE   = "https://hub.renergygroup.it/api"
HUB_EMAIL  = os.environ.get("HUB_EMAIL")
HUB_PASS   = os.environ.get("HUB_PASSWORD")

claude  = anthropic.Anthropic(api_key=CLAUDE_KEY)
whisper = OpenAI(api_key=OPENAI_KEY)
executor = ThreadPoolExecutor(max_workers=2)


# ---------------------------------------------------------------------------
# Account email
# ---------------------------------------------------------------------------
def carica_account_email() -> list[dict]:
    accounts = []
    for i in range(1, 10):
        label = os.environ.get(f"EMAIL_{i}_LABEL")
        if not label:
            break
        accounts.append({
            "label":    label,
            "address":  os.environ.get(f"EMAIL_{i}_ADDRESS", ""),
            "password": os.environ.get(f"EMAIL_{i}_PASSWORD", ""),
            "host":     os.environ.get(f"EMAIL_{i}_SMTP_HOST", ""),
            "port":     int(os.environ.get(f"EMAIL_{i}_SMTP_PORT", "587")),
            "ssl":      os.environ.get(f"EMAIL_{i}_SSL", "false").lower() == "true",
        })
    return accounts

EMAIL_ACCOUNTS = carica_account_email()


# ---------------------------------------------------------------------------
# Sessioni in memoria
# ---------------------------------------------------------------------------
sessions: dict[int, dict] = {}

def sessione(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = {
            "stato": "idle",
            "note_raw": "", "azioni": [],
            "email_drafts": [], "email_idx": 0,
            "email_modifica_idx": 0, "email_dest_idx": 0,
            "from_account": EMAIL_ACCOUNTS[0] if EMAIL_ACCOUNTS else None,
        }
    return sessions[chat_id]

def reset_sessione(chat_id: int):
    sessions.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Sicurezza
# ---------------------------------------------------------------------------
def solo_owner(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            await update.effective_message.reply_text("❌ Accesso non autorizzato.")
            return
        return await func(update, ctx)
    return wrapper


# ---------------------------------------------------------------------------
# Hub CRM helpers
# ---------------------------------------------------------------------------
def hub_token() -> str:
    import requests as req
    r = req.post(f"{HUB_BASE}/auth/login", json={"email": HUB_EMAIL, "password": HUB_PASS}, timeout=10)
    r.raise_for_status()
    return r.json().get("token")

def hub_ultimi_lead(n: int = 10) -> list[dict]:
    import requests as req
    token = hub_token()
    # Prova endpoint più comuni per lista lead
    for endpoint in ["/crm/lead", "/crm/leads", "/crm/lead/list"]:
        try:
            r = req.get(f"{HUB_BASE}{endpoint}",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"limit": n, "orderBy": "created_at", "order": "desc"},
                        timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("leads", data.get("data", []))
        except Exception:
            continue
    return []


# ---------------------------------------------------------------------------
# Trascrizione vocale
# ---------------------------------------------------------------------------
async def trascrivi_vocale(file_telegram) -> str:
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file_telegram.download_to_drive(tmp.name)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as audio:
            result = whisper.audio.transcriptions.create(model="whisper-1", file=audio, language="it")
        return result.text
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Claude — estrazione azioni
# ---------------------------------------------------------------------------
PROMPT_AZIONI = """Sei l'assistente personale di un imprenditore italiano con più società (Renergy, ACM&Partners, BevManager).
Ha registrato appunti dopo una serie di appuntamenti.

APPUNTI:
{note}

Estrai tutte le azioni e le email da inviare. Rispondi SOLO con JSON valido:
{{
  "riepilogo": "2-3 righe di riepilogo compatto",
  "azioni": [
    {{
      "società": "Renergy | ACM&Partners | BevManager | altra",
      "contatto_incontrato": "nome/azienda",
      "descrizione": "cosa va fatto",
      "destinatario_nome": "a chi va assegnato",
      "destinatario_email": "email se menzionata, altrimenti null",
      "scadenza": "quando",
      "priorità": "alta | media | bassa",
      "tipo": "email_collaboratore | email_cliente | reminder | altro"
    }}
  ]
}}"""

def estrai_azioni(note: str) -> dict:
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT_AZIONI.format(note=note)}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Claude — bozza email
# ---------------------------------------------------------------------------
PROMPT_EMAIL = """Scrivi un'email professionale in italiano per conto di un imprenditore.

Società: {società}
Destinatario: {nome}
Compito: {descrizione}
Scadenza: {scadenza}
Priorità: {priorità}
Contesto: {note}

Rispondi SOLO con JSON: {{"oggetto": "...", "corpo": "testo completo email"}}
Stile: professionale ma diretto, chiaro sulle aspettative."""

def bozza_email(azione: dict, note_raw: str) -> dict:
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=800,
        messages=[{"role": "user", "content": PROMPT_EMAIL.format(
            società=azione.get("società",""), nome=azione.get("destinatario_nome",""),
            descrizione=azione.get("descrizione",""), scadenza=azione.get("scadenza",""),
            priorità=azione.get("priorità",""), note=note_raw[:400]
        )}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Invio email SMTP
# ---------------------------------------------------------------------------
def invia_email(account: dict, to: str, subject: str, body: str):
    msg = MIMEMultipart()
    msg["From"] = account["address"]
    msg["To"]   = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if account["ssl"]:
        srv = smtplib.SMTP_SSL(account["host"], account["port"], timeout=15)
    else:
        srv = smtplib.SMTP(account["host"], account["port"], timeout=15)
        srv.starttls()
    srv.login(account["address"], account["password"])
    srv.send_message(msg)
    srv.quit()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def kb_azioni() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Prepara le email", callback_data="azioni_ok"),
        InlineKeyboardButton("✏️ Modifica", callback_data="azioni_modifica"),
    ]])

def kb_email(idx: int, totale: int, ha_email: bool) -> InlineKeyboardMarkup:
    righe = []
    if ha_email:
        righe.append([
            InlineKeyboardButton("✅ Approva e invia", callback_data=f"email_approva_{idx}"),
            InlineKeyboardButton("✏️ Modifica testo",  callback_data=f"email_modifica_{idx}"),
        ])
        righe.append([
            InlineKeyboardButton("📧 Cambia destinatario", callback_data=f"email_dest_{idx}"),
            InlineKeyboardButton("⏭ Salta", callback_data=f"email_salta_{idx}"),
        ])
    else:
        righe.append([
            InlineKeyboardButton("📧 Inserisci email", callback_data=f"email_dest_{idx}"),
            InlineKeyboardButton("⏭ Salta", callback_data=f"email_salta_{idx}"),
        ])
    righe.append([InlineKeyboardButton("🗑 Annulla tutto", callback_data="annulla")])
    return InlineKeyboardMarkup(righe)

def kb_mittente() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📤 {a['label']}", callback_data=f"mittente_{i}")]
        for i, a in enumerate(EMAIL_ACCOUNTS)
    ])

def formatta_azione(a: dict, i: int) -> str:
    emoji = {"alta":"🔴","media":"🟡","bassa":"🟢"}.get(a.get("priorità",""),"⚪")
    dest  = a.get("destinatario_email") or "⚠️ email mancante"
    return (f"{i+1}. {emoji} *{a.get('società','')}*\n"
            f"   👤 {a.get('destinatario_nome','')} ({dest})\n"
            f"   📋 {a.get('descrizione','')}\n"
            f"   ⏰ {a.get('scadenza','N/D')}")

def formatta_email(draft: dict, azione: dict, account: dict, idx: int, totale: int) -> str:
    return (f"📧 *BOZZA EMAIL {idx+1}/{totale}*\n\n"
            f"*A:* {azione.get('destinatario_nome','')} "
            f"<{azione.get('destinatario_email') or '⚠️ da inserire'}>\n"
            f"*Da:* {account['label']}\n"
            f"*Oggetto:* {draft.get('oggetto','')}\n\n"
            f"─────────────────\n{draft.get('corpo','')}\n─────────────────")

def formatta_briefing(ora: str, sr: dict, sa: dict) -> str:
    return (f"🤖 *Briefing Agenti AI — {ora}*\n\n"
            f"⚡ *Renergy Project\\&Build*\n"
            f"• Lead creati: {sr.get('create_hub',0)}\n"
            f"• Pipeline: €{sr.get('valore_totale',0):,}\n"
            f"• Scartati \\(hanno solare\\): {sr.get('scartate_solare',0)}\n\n"
            f"🏢 *ACM\\&Partners*\n"
            f"• Lead creati: {sa.get('create_hub',0)}\n"
            f"• Scartati: {sa.get('scartate',0)}\n\n"
            f"📊 *Totale lead: {sr.get('create_hub',0)+sa.get('create_hub',0)}*\n"
            f"Apri Hub per revisione e approvazione\\.")


# ---------------------------------------------------------------------------
# Runner agenti in background
# ---------------------------------------------------------------------------
async def run_agente_bg(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, agente, nome: str):
    """Lancia un agente in thread separato e manda il risultato su Telegram."""
    loop = asyncio.get_event_loop()
    await ctx.bot.send_message(chat_id, f"⏳ Agente *{nome}* avviato...", parse_mode="Markdown")
    try:
        stats = await loop.run_in_executor(executor, agente.run)
        if nome == "Renergy":
            testo = (f"✅ *Renergy* completato\n"
                     f"• Lead: {stats.get('create_hub',0)}\n"
                     f"• Pipeline: €{stats.get('valore_totale',0):,}\n"
                     f"• Scartati: {stats.get('scartate_solare',0)}")
        else:
            testo = (f"✅ *ACM\\&Partners* completato\n"
                     f"• Lead: {stats.get('create_hub',0)}\n"
                     f"• Scartati: {stats.get('scartate',0)}")
        await ctx.bot.send_message(chat_id, testo, parse_mode="MarkdownV2")
        return stats
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"❌ Errore agente {nome}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Comando /run_all
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_run_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("🚀 Avvio entrambi gli agenti...")
    loop = asyncio.get_event_loop()

    # Lancia in parallelo
    sr, sa = await asyncio.gather(
        loop.run_in_executor(executor, agent_renergy.run),
        loop.run_in_executor(executor, agent_acm.run),
        return_exceptions=True,
    )
    if isinstance(sr, Exception): sr = {"create_hub":0,"valore_totale":0,"scartate_solare":0}
    if isinstance(sa, Exception): sa = {"create_hub":0,"scartate":0}

    ora = datetime.now().strftime("%d/%m/%Y %H:%M")
    await update.message.reply_text(formatta_briefing(ora, sr, sa), parse_mode="MarkdownV2")


@solo_owner
async def cmd_renergy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await run_agente_bg(ctx, update.effective_chat.id, agent_renergy, "Renergy")

@solo_owner
async def cmd_acm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await run_agente_bg(ctx, update.effective_chat.id, agent_acm, "ACM")


# ---------------------------------------------------------------------------
# Comando /lead
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_lead(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Recupero ultimi lead da Hub...")
    try:
        loop = asyncio.get_event_loop()
        lead_list = await loop.run_in_executor(executor, hub_ultimi_lead, 8)
        if not lead_list:
            await update.message.reply_text("📭 Nessun lead trovato in Hub.")
            return
        stati_emoji = {"nuovo":"🆕","contattato":"📞","interesse":"👀",
                       "preventivo":"📄","chiuso_vinto":"🏆","chiuso_perso":"❌","sospeso":"⏸"}
        righe = ["📊 *Ultimi lead in Hub:*\n"]
        for l in lead_list:
            stato   = l.get("stato","")
            emoji   = stati_emoji.get(stato,"•")
            azienda = l.get("ragione_sociale","N/D")
            citta   = l.get("citta","")
            fonte   = l.get("fonte","")
            kwp     = f" | {l.get('kwp_stimati')}kWp" if l.get("kwp_stimati") else ""
            righe.append(f"{emoji} *{azienda}* — {stato}\n   📍 {citta} | {fonte}{kwp}")
        await update.message.reply_text("\n".join(righe), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore Hub: {e}")


# ---------------------------------------------------------------------------
# Comando /stato
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_stato(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apollo  = "✅" if os.environ.get("APOLLO_API_KEY")  else "❌"
    claude_ = "✅" if os.environ.get("CLAUDE_API_KEY")  else "❌"
    openai_ = "✅" if os.environ.get("OPENAI_API_KEY")  else "❌"
    maps    = "✅" if os.environ.get("GOOGLE_MAPS_KEY") else "❌ (Vision disabilitata)"
    hub     = "✅" if os.environ.get("HUB_EMAIL")       else "❌"
    email_n = len(EMAIL_ACCOUNTS)
    s = sessione(update.effective_chat.id)
    mittente = s["from_account"]["label"] if s["from_account"] else "non configurato"
    testo = (f"🔧 *Stato Sistema*\n\n"
             f"Apollo API: {apollo}\nClaude API: {claude_}\n"
             f"OpenAI Whisper: {openai_}\nGoogle Maps: {maps}\n"
             f"Hub CRM: {hub}\n\n"
             f"📤 Account email: {email_n} configurati\n"
             f"Mittente attivo: *{mittente}*")
    await update.message.reply_text(testo, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Comando /mittente, /annulla, /start
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_sessione(update.effective_chat.id)
    await update.message.reply_text(
        "👋 *Bot Agenti AI* — tutto in un posto\\!\n\n"
        "📋 *Comandi agenti:*\n"
        "/run\\_all — lancia Renergy \\+ ACM\n"
        "/renergy — solo agente Renergy\n"
        "/acm — solo agente ACM\n"
        "/lead — ultimi lead in Hub\n"
        "/stato — stato sistema\n\n"
        "🎙️ *Appunti → Email:*\n"
        "Manda un vocale o testo con i tuoi appunti\\. "
        "Estraggo le azioni e preparo le email\\.\n\n"
        "/mittente — cambia account invio\n"
        "/annulla — annulla sessione corrente",
        parse_mode="MarkdownV2"
    )

@solo_owner
async def cmd_annulla(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_sessione(update.effective_chat.id)
    await update.message.reply_text("🗑 Sessione annullata.")

@solo_owner
async def cmd_mittente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not EMAIL_ACCOUNTS:
        await update.message.reply_text("⚠️ Nessun account email configurato.")
        return
    s = sessione(update.effective_chat.id)
    await update.message.reply_text(
        f"Account attuale: *{s['from_account']['label']}*\n\nSeleziona mittente:",
        reply_markup=kb_mittente(), parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# Processa input (vocale o testo)
# ---------------------------------------------------------------------------
async def processa_input(update: Update, testo: str, chat_id: int):
    s = sessione(chat_id)
    s["stato"] = "processing"
    s["note_raw"] = testo
    msg = await update.effective_message.reply_text("⏳ Analizzo gli appunti...")
    try:
        risultato = estrai_azioni(testo)
    except Exception as e:
        await msg.edit_text(f"❌ Errore analisi: {e}")
        s["stato"] = "idle"
        return
    s["azioni"] = risultato.get("azioni", [])
    if not s["azioni"]:
        await msg.edit_text("🤔 Nessuna azione trovata. Prova a riformulare.")
        s["stato"] = "idle"
        return
    azioni_testo = "\n\n".join(formatta_azione(a, i) for i, a in enumerate(s["azioni"]))
    testo_out = (f"📋 *RIEPILOGO*\n\n{risultato.get('riepilogo','')}\n\n"
                 f"*Azioni estratte ({len(s['azioni'])}):*\n\n{azioni_testo}\n\n"
                 f"Preparo le bozze email?")
    await msg.edit_text(testo_out, reply_markup=kb_azioni(), parse_mode="Markdown")
    s["stato"] = "review_azioni"


@solo_owner
async def handler_vocale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎙️ Trascrizione in corso...")
    try:
        f = await update.message.voice.get_file()
        testo = await trascrivi_vocale(f)
    except Exception as e:
        await msg.edit_text(f"❌ Errore trascrizione: {e}")
        return
    await msg.edit_text(f"📝 *Trascrizione:*\n\n_{testo}_", parse_mode="Markdown")
    await processa_input(update, testo, update.effective_chat.id)


@solo_owner
async def handler_testo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = sessione(chat_id)
    testo = update.message.text.strip()

    # Delega al campaign handler se è in corso un wizard campagna
    if await bot_campagna.handler_testo_campagna(update, ctx):
        return

    if s["stato"] == "attesa_modifica_email":
        idx = s["email_modifica_idx"]
        s["email_drafts"][idx]["corpo"] = testo
        s["stato"] = "review_email"
        await mostra_email(update, chat_id, idx)
        return

    if s["stato"] == "attesa_email_dest":
        idx = s["email_dest_idx"]
        s["azioni"][idx]["destinatario_email"] = testo
        await update.message.reply_text("✏️ Rigenero la bozza...")
        try:
            draft = bozza_email(s["azioni"][idx], s["note_raw"])
            if idx < len(s["email_drafts"]):
                s["email_drafts"][idx] = draft
            else:
                s["email_drafts"].append(draft)
        except Exception:
            pass
        s["stato"] = "review_email"
        await mostra_email(update, chat_id, idx)
        return

    await processa_input(update, testo, chat_id)


# ---------------------------------------------------------------------------
# Review email
# ---------------------------------------------------------------------------
async def mostra_email(update: Update, chat_id: int, idx: int):
    s = sessione(chat_id)
    if idx >= len(s["azioni"]):
        await mostra_fine(update, chat_id)
        return
    azione = s["azioni"][idx]
    s["email_idx"] = idx
    if idx >= len(s["email_drafts"]):
        msg = await update.effective_message.reply_text("✏️ Genero bozza...")
        try:
            s["email_drafts"].append(bozza_email(azione, s["note_raw"]))
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Errore bozza: {e}")
            return
    draft    = s["email_drafts"][idx]
    ha_email = bool(azione.get("destinatario_email"))
    preview  = formatta_email(draft, azione, s["from_account"], idx, len(s["azioni"]))
    await update.effective_message.reply_text(
        preview, reply_markup=kb_email(idx, len(s["azioni"]), ha_email), parse_mode="Markdown"
    )
    s["stato"] = "review_email"


async def mostra_fine(update: Update, chat_id: int):
    s = sessione(chat_id)
    inviate  = sum(1 for a in s["azioni"] if a.get("_inviata"))
    saltate  = sum(1 for a in s["azioni"] if a.get("_saltata"))
    errori   = sum(1 for a in s["azioni"] if a.get("_errore"))
    testo = f"✅ *Sessione completata!*\n\n📤 Inviate: {inviate}\n⏭ Saltate: {saltate}"
    if errori: testo += f"\n❌ Errori: {errori}"
    if inviate:
        testo += "\n\n*Inviate a:*\n"
        testo += "\n".join(f"• {a.get('destinatario_nome')} ({a.get('destinatario_email')})"
                           for a in s["azioni"] if a.get("_inviata"))
    await update.effective_message.reply_text(testo, parse_mode="Markdown")
    reset_sessione(chat_id)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------
@solo_owner
async def handler_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q       = update.callback_query
    chat_id = update.effective_chat.id
    data    = q.data
    s       = sessione(chat_id)

    # Delega al campaign handler se il callback riguarda le campagne
    if data.startswith("camp_"):
        await bot_campagna.handler_callback_campagna(update, ctx)
        return

    await q.answer()

    if data.startswith("mittente_"):
        idx = int(data.split("_")[1])
        s["from_account"] = EMAIL_ACCOUNTS[idx]
        await q.edit_message_text(f"✅ Mittente: *{EMAIL_ACCOUNTS[idx]['label']}*", parse_mode="Markdown")

    elif data == "annulla":
        reset_sessione(chat_id)
        await q.edit_message_text("🗑 Sessione annullata.")

    elif data == "azioni_ok":
        await q.edit_message_text("✅ Preparo le email...")
        await mostra_email(update, chat_id, 0)

    elif data == "azioni_modifica":
        await q.edit_message_text("✏️ Scrivi le correzioni o riformula gli appunti:")
        s["stato"] = "idle"

    elif data.startswith("email_approva_"):
        idx    = int(data.split("_")[2])
        azione = s["azioni"][idx]
        draft  = s["email_drafts"][idx]
        to     = azione.get("destinatario_email","")
        await q.edit_message_text(f"📤 Invio a {to}...")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor, invia_email,
                s["from_account"], to, draft["oggetto"], draft["corpo"]
            )
            azione["_inviata"] = True
            await q.edit_message_text(f"✅ Email inviata a *{azione.get('destinatario_nome')}* ({to})", parse_mode="Markdown")
        except Exception as e:
            azione["_errore"] = True
            await q.edit_message_text(f"❌ Errore invio: {e}")
        await mostra_email(update, chat_id, idx + 1)

    elif data.startswith("email_modifica_"):
        idx = int(data.split("_")[2])
        s["email_modifica_idx"] = idx
        s["stato"] = "attesa_modifica_email"
        await q.edit_message_text("✏️ Invia il nuovo testo del corpo email:")

    elif data.startswith("email_dest_"):
        idx = int(data.split("_")[2])
        s["email_dest_idx"] = idx
        s["stato"] = "attesa_email_dest"
        nome = s["azioni"][idx].get("destinatario_nome","destinatario")
        await q.edit_message_text(f"📧 Inserisci l'email di *{nome}*:", parse_mode="Markdown")

    elif data.startswith("email_salta_"):
        idx = int(data.split("_")[2])
        s["azioni"][idx]["_saltata"] = True
        await q.edit_message_text(f"⏭ Email {idx+1} saltata.")
        await mostra_email(update, chat_id, idx + 1)


# ---------------------------------------------------------------------------
# Job schedulato — briefing mattutino
# ---------------------------------------------------------------------------
async def briefing_mattutino(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Briefing mattutino schedulato — avvio agenti...")
    loop = asyncio.get_event_loop()
    sr, sa = await asyncio.gather(
        loop.run_in_executor(executor, agent_renergy.run),
        loop.run_in_executor(executor, agent_acm.run),
        return_exceptions=True,
    )
    if isinstance(sr, Exception): sr = {"create_hub":0,"valore_totale":0,"scartate_solare":0}
    if isinstance(sa, Exception): sa = {"create_hub":0,"scartate":0}
    ora = datetime.now().strftime("%d/%m/%Y %H:%M")
    await ctx.bot.send_message(OWNER_ID, formatta_briefing(ora, sr, sa), parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN mancante")
    if not OWNER_ID:
        raise ValueError("TELEGRAM_OWNER_ID mancante")

    log.info(f"Bot avviato | Email account: {len(EMAIL_ACCOUNTS)}")

    # Inizializza il modulo campagne passando questo modulo come riferimento
    bot_campagna.init(sys.modules[__name__])

    app = Application.builder().token(BOT_TOKEN).build()

    # Comandi base
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("annulla",  cmd_annulla))
    app.add_handler(CommandHandler("mittente", cmd_mittente))
    app.add_handler(CommandHandler("run_all",  cmd_run_all))
    app.add_handler(CommandHandler("renergy",  cmd_renergy))
    app.add_handler(CommandHandler("acm",      cmd_acm))
    app.add_handler(CommandHandler("lead",     cmd_lead))
    app.add_handler(CommandHandler("stato",    cmd_stato))

    # Comandi campagne e mercato
    bot_campagna.registra_handlers(app)

    # Messaggi
    app.add_handler(MessageHandler(filters.VOICE,                   handler_vocale))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler_testo))
    app.add_handler(CallbackQueryHandler(handler_callback))

    # Job schedulati campagne
    app.job_queue.run_daily(bot_campagna.job_mercato,   time=dt_time(8,  0), days=(1,))          # martedì 08:00
    app.job_queue.run_daily(bot_campagna.job_sequenze,  time=dt_time(8, 30), days=tuple(range(7))) # ogni giorno 08:30

    # Briefing agenti — ogni lunedì alle 07:00
    app.job_queue.run_daily(
        briefing_mattutino,
        time=dt_time(7, 0, 0),
        days=(0,),   # 0=lunedì, cambia in tuple(range(7)) per ogni giorno
    )

    log.info("In ascolto... (briefing automatico: lunedì 07:00)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
