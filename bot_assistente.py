"""
bot_assistente.py — Assistente Appunti → Azioni → Email
Telegram bot che riceve vocali/testo, estrae azioni, bozza email, le mostra per approvazione e le invia.

Secrets Replit necessari:
  TELEGRAM_BOT_TOKEN     → da @BotFather
  TELEGRAM_OWNER_ID      → il tuo chat ID (solo tu puoi usarlo)
  OPENAI_API_KEY         → per trascrizione vocale Whisper
  CLAUDE_API_KEY         → per analisi e stesura email

  # Account email (aggiungi quanti ne vuoi con numerazione progressiva)
  EMAIL_1_LABEL          → es. "Renergy (Office365)"
  EMAIL_1_ADDRESS        → daniele@renergygroup.it
  EMAIL_1_PASSWORD       → ...
  EMAIL_1_SMTP_HOST      → smtp.office365.com
  EMAIL_1_SMTP_PORT      → 587
  EMAIL_1_SSL            → false  (usa STARTTLS)

  EMAIL_2_LABEL          → es. "ACM (SiteGround)"
  EMAIL_2_ADDRESS        → daniele@acmpartners.it
  EMAIL_2_PASSWORD       → ...
  EMAIL_2_SMTP_HOST      → smtp.siteground.com (o mail.acmpartners.it)
  EMAIL_2_SMTP_PORT      → 587
  EMAIL_2_SSL            → false

  EMAIL_3_LABEL          → es. "Personale (Aruba)"
  EMAIL_3_ADDRESS        → daniele@...
  EMAIL_3_PASSWORD       → ...
  EMAIL_3_SMTP_HOST      → smtps.aruba.it
  EMAIL_3_SMTP_PORT      → 465
  EMAIL_3_SSL            → true
"""

import os
import json
import smtplib
import tempfile
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import anthropic
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID    = int(os.environ.get("TELEGRAM_OWNER_ID", "0"))
CLAUDE_KEY  = os.environ.get("CLAUDE_API_KEY")
OPENAI_KEY  = os.environ.get("OPENAI_API_KEY")

claude = anthropic.Anthropic(api_key=CLAUDE_KEY)
whisper = OpenAI(api_key=OPENAI_KEY)


def carica_account_email() -> list[dict]:
    """Carica tutti gli account email configurati via env vars numerati."""
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
# Sessioni in memoria  {chat_id: {...}}
# ---------------------------------------------------------------------------
sessions: dict[int, dict] = {}

def sessione(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = {
            "stato":       "idle",
            "note_raw":    "",
            "azioni":      [],
            "email_drafts": [],
            "email_idx":   0,
            "from_account": EMAIL_ACCOUNTS[0] if EMAIL_ACCOUNTS else None,
        }
    return sessions[chat_id]

def reset_sessione(chat_id: int):
    sessions.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Sicurezza — solo il proprietario
# ---------------------------------------------------------------------------
def solo_owner(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("❌ Accesso non autorizzato.")
            return
        return await func(update, ctx)
    return wrapper


# ---------------------------------------------------------------------------
# Trascrizione vocale (Whisper)
# ---------------------------------------------------------------------------
async def trascrivi_vocale(file_telegram) -> str:
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file_telegram.download_to_drive(tmp.name)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as audio:
            result = whisper.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="it",
            )
        return result.text
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Claude — estrazione azioni strutturate
# ---------------------------------------------------------------------------
PROMPT_ESTRAZIONE = """Sei l'assistente personale di un imprenditore italiano con più società.
Ha appena registrato dei appunti dopo una serie di appuntamenti.

APPUNTI:
{note}

Estrai tutte le azioni da compiere e le email da inviare.
Rispondi SOLO con JSON valido, nient'altro:
{{
  "riepilogo": "2-3 righe di riepilogo compatto degli appuntamenti",
  "azioni": [
    {{
      "società": "nome della società coinvolta (Renergy/ACM/BevManager/ecc.)",
      "contatto_incontrato": "nome o azienda con cui si è parlato",
      "descrizione": "cosa va fatto",
      "destinatario_nome": "nome del collaboratore a cui va assegnato",
      "destinatario_email": "email del destinatario (se menzionata, altrimenti null)",
      "scadenza": "quando (es. venerdì, domani, entro una settimana, urgente)",
      "priorità": "alta | media | bassa",
      "tipo": "email_collaboratore | email_cliente | reminder | altro"
    }}
  ]
}}

Se l'email del destinatario non viene menzionata, metti null — la chiederò all'utente.
Estrai TUTTE le azioni, anche quelle implicite."""

def estrai_azioni(note: str) -> dict:
    msg = claude.messages.create(
        model="claude-opus-4-8",
        max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT_ESTRAZIONE.format(note=note)}],
    )
    testo = msg.content[0].text.strip()
    start = testo.find("{")
    end   = testo.rfind("}") + 1
    return json.loads(testo[start:end])


# ---------------------------------------------------------------------------
# Claude — stesura email
# ---------------------------------------------------------------------------
PROMPT_EMAIL = """Sei l'assistente personale di un imprenditore.
Devi scrivere un'email professionale e concisa in italiano.

CONTESTO DELL'AZIONE:
Società: {società}
Destinatario: {destinatario_nome}
Cosa fare: {descrizione}
Scadenza: {scadenza}
Priorità: {priorità}
Note originali: {note_raw}

Scrivi l'email. Rispondi SOLO con JSON:
{{
  "oggetto": "oggetto dell'email",
  "corpo": "testo completo dell'email (professionale ma diretto, senza formalità eccessive)"
}}

Stile: italiano, professionale ma non formale, chiaro sulle aspettative e scadenze."""

def bozza_email(azione: dict, note_raw: str) -> dict:
    msg = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": PROMPT_EMAIL.format(
            società=azione.get("società", ""),
            destinatario_nome=azione.get("destinatario_nome", ""),
            descrizione=azione.get("descrizione", ""),
            scadenza=azione.get("scadenza", ""),
            priorità=azione.get("priorità", ""),
            note_raw=note_raw[:500],
        )}],
    )
    testo = msg.content[0].text.strip()
    start = testo.find("{")
    end   = testo.rfind("}") + 1
    return json.loads(testo[start:end])


# ---------------------------------------------------------------------------
# Invio email SMTP
# ---------------------------------------------------------------------------
def invia_email(account: dict, to: str, subject: str, body: str) -> bool:
    try:
        msg = MIMEMultipart()
        msg["From"]    = account["address"]
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if account["ssl"]:
            server = smtplib.SMTP_SSL(account["host"], account["port"], timeout=15)
        else:
            server = smtplib.SMTP(account["host"], account["port"], timeout=15)
            server.starttls()

        server.login(account["address"], account["password"])
        server.send_message(msg)
        server.quit()
        log.info(f"Email inviata a {to} tramite {account['label']}")
        return True
    except Exception as e:
        log.error(f"Errore invio email a {to}: {e}")
        raise


# ---------------------------------------------------------------------------
# Helpers UI Telegram
# ---------------------------------------------------------------------------
def kb_azioni(azioni: list) -> InlineKeyboardMarkup:
    """Tastiera per conferma azioni estratte."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Sì, procedi con le email", callback_data="azioni_ok"),
        InlineKeyboardButton("✏️ Modifica", callback_data="azioni_modifica"),
    ]])

def kb_email(idx: int, totale: int, ha_email: bool) -> InlineKeyboardMarkup:
    """Tastiera per review singola email."""
    righe = []
    if ha_email:
        righe.append([
            InlineKeyboardButton("✅ Approva e invia", callback_data=f"email_approva_{idx}"),
            InlineKeyboardButton("✏️ Modifica testo", callback_data=f"email_modifica_{idx}"),
        ])
        righe.append([
            InlineKeyboardButton("📧 Cambia destinatario", callback_data=f"email_cambio_dest_{idx}"),
            InlineKeyboardButton("⏭ Salta", callback_data=f"email_salta_{idx}"),
        ])
    else:
        righe.append([
            InlineKeyboardButton("📧 Inserisci email destinatario", callback_data=f"email_inserisci_dest_{idx}"),
            InlineKeyboardButton("⏭ Salta", callback_data=f"email_salta_{idx}"),
        ])
    if totale > 1:
        righe.append([InlineKeyboardButton("🗑 Annulla tutto", callback_data="annulla")])
    return InlineKeyboardMarkup(righe)

def kb_mittente() -> InlineKeyboardMarkup:
    """Selezione account mittente."""
    bottoni = [
        [InlineKeyboardButton(f"📤 {acc['label']}", callback_data=f"mittente_{i}")]
        for i, acc in enumerate(EMAIL_ACCOUNTS)
    ]
    return InlineKeyboardMarkup(bottoni)

def formatta_azione(a: dict, idx: int) -> str:
    priorità_emoji = {"alta": "🔴", "media": "🟡", "bassa": "🟢"}.get(a.get("priorità", ""), "⚪")
    dest_email = a.get("destinatario_email") or "⚠️ email mancante"
    return (
        f"{idx+1}. {priorità_emoji} **{a.get('società', '')}**\n"
        f"   👤 A: {a.get('destinatario_nome', '')} ({dest_email})\n"
        f"   📋 {a.get('descrizione', '')}\n"
        f"   ⏰ Scadenza: {a.get('scadenza', 'N/D')}"
    )

def formatta_email_preview(draft: dict, azione: dict) -> str:
    return (
        f"📧 **BOZZA EMAIL**\n\n"
        f"**A:** {azione.get('destinatario_nome', '')} "
        f"<{azione.get('destinatario_email') or 'email da inserire'}>\n"
        f"**Oggetto:** {draft.get('oggetto', '')}\n\n"
        f"─────────────────\n"
        f"{draft.get('corpo', '')}\n"
        f"─────────────────"
    )


# ---------------------------------------------------------------------------
# Handlers principali
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_sessione(update.effective_chat.id)
    await update.message.reply_text(
        "👋 Ciao! Sono il tuo assistente appunti.\n\n"
        "Mandami un **vocale** o un **messaggio** con i tuoi appunti dopo gli appuntamenti "
        "e creo le email per i tuoi collaboratori.\n\n"
        "Comandi:\n"
        "/attività — vedi le azioni pendenti\n"
        "/annulla — annulla la sessione corrente\n"
        "/mittente — cambia account email di invio",
        parse_mode="Markdown",
    )

@solo_owner
async def cmd_annulla(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_sessione(update.effective_chat.id)
    await update.message.reply_text("🗑 Sessione annullata. Manda nuovi appunti quando vuoi.")

@solo_owner
async def cmd_mittente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not EMAIL_ACCOUNTS:
        await update.message.reply_text("⚠️ Nessun account email configurato nei Secrets.")
        return
    s = sessione(update.effective_chat.id)
    testo = f"Account attuale: **{s['from_account']['label']}**\n\nSeleziona account mittente:"
    await update.message.reply_text(testo, reply_markup=kb_mittente(), parse_mode="Markdown")


async def processa_input(update: Update, testo: str, chat_id: int):
    """Elabora testo (o trascrizione vocale) ed estrae le azioni."""
    s = sessione(chat_id)
    s["stato"]    = "processing"
    s["note_raw"] = testo

    msg_attesa = await update.effective_message.reply_text(
        "⏳ Sto analizzando gli appunti..."
    )

    try:
        risultato = estrai_azioni(testo)
    except Exception as e:
        await msg_attesa.edit_text(f"❌ Errore nell'analisi: {e}")
        s["stato"] = "idle"
        return

    s["azioni"] = risultato.get("azioni", [])
    riepilogo   = risultato.get("riepilogo", "")

    if not s["azioni"]:
        await msg_attesa.edit_text("🤔 Non ho trovato azioni da compiere. Prova a riformulare.")
        s["stato"] = "idle"
        return

    # Mostra riepilogo azioni
    testo_azioni = f"📋 **RIEPILOGO APPUNTAMENTI**\n\n{riepilogo}\n\n**Azioni estratte ({len(s['azioni'])}):**\n\n"
    testo_azioni += "\n\n".join(formatta_azione(a, i) for i, a in enumerate(s["azioni"]))
    testo_azioni += "\n\n✅ Vuoi che prepari le bozze email per tutte queste azioni?"

    await msg_attesa.edit_text(testo_azioni, reply_markup=kb_azioni(s["azioni"]), parse_mode="Markdown")
    s["stato"] = "review_azioni"


@solo_owner
async def handler_testo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = sessione(chat_id)

    # Se siamo in attesa di modifica testo email
    if s["stato"] == "attesa_modifica_email":
        idx   = s.get("email_modifica_idx", 0)
        nuovo = update.message.text.strip()
        s["email_drafts"][idx]["corpo"] = nuovo
        s["stato"] = "review_email"
        await mostra_email(update, chat_id, idx)
        return

    # Se siamo in attesa di email destinatario
    if s["stato"] == "attesa_email_dest":
        idx   = s.get("email_dest_idx", 0)
        email = update.message.text.strip()
        s["azioni"][idx]["destinatario_email"] = email
        # Rigenera bozza con email ora disponibile
        await update.message.reply_text("✏️ Rigenerando la bozza con il nuovo destinatario...")
        try:
            s["email_drafts"][idx] = bozza_email(s["azioni"][idx], s["note_raw"])
        except Exception:
            pass
        s["stato"] = "review_email"
        await mostra_email(update, chat_id, idx)
        return

    # Input normale — processa appunti
    await processa_input(update, update.message.text, chat_id)


@solo_owner
async def handler_vocale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg_attesa = await update.message.reply_text("🎙️ Trascrizione vocale in corso...")

    try:
        voice_file = await update.message.voice.get_file()
        testo = await trascrivi_vocale(voice_file)
    except Exception as e:
        await msg_attesa.edit_text(f"❌ Errore trascrizione: {e}")
        return

    await msg_attesa.edit_text(f"📝 **Trascrizione:**\n\n_{testo}_", parse_mode="Markdown")
    await processa_input(update, testo, chat_id)


# ---------------------------------------------------------------------------
# Mostra singola email per review
# ---------------------------------------------------------------------------
async def mostra_email(update: Update, chat_id: int, idx: int):
    s = sessione(chat_id)

    if idx >= len(s["azioni"]):
        await mostra_riepilogo_finale(update, chat_id)
        return

    azione = s["azioni"][idx]
    s["email_idx"] = idx

    # Genera bozza se non esiste ancora
    if idx >= len(s["email_drafts"]):
        msg = await update.effective_message.reply_text("✏️ Genero la bozza...")
        try:
            draft = bozza_email(azione, s["note_raw"])
            s["email_drafts"].append(draft)
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Errore bozza: {e}")
            return

    draft    = s["email_drafts"][idx]
    ha_email = bool(azione.get("destinatario_email"))

    preview = formatta_email_preview(draft, azione)
    preview += f"\n\n📤 Da: **{s['from_account']['label']}**"
    preview += f"\n\n_Email {idx+1} di {len(s['azioni'])}_"

    await update.effective_message.reply_text(
        preview,
        reply_markup=kb_email(idx, len(s["azioni"]), ha_email),
        parse_mode="Markdown",
    )
    s["stato"] = "review_email"


async def mostra_riepilogo_finale(update: Update, chat_id: int):
    s = sessione(chat_id)
    inviate  = [a for a in s["azioni"] if a.get("_inviata")]
    saltate  = [a for a in s["azioni"] if a.get("_saltata")]
    in_errore = [a for a in s["azioni"] if a.get("_errore")]

    testo = f"✅ **Sessione completata!**\n\n"
    testo += f"📤 Email inviate: {len(inviate)}\n"
    testo += f"⏭ Saltate: {len(saltate)}\n"
    if in_errore:
        testo += f"❌ Errori: {len(in_errore)}\n"

    if inviate:
        testo += "\n**Inviate a:**\n"
        for a in inviate:
            testo += f"• {a.get('destinatario_nome')} ({a.get('destinatario_email')})\n"

    await update.effective_message.reply_text(testo, parse_mode="Markdown")
    reset_sessione(chat_id)


# ---------------------------------------------------------------------------
# Callback handler (bottoni inline)
# ---------------------------------------------------------------------------
@solo_owner
async def handler_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = update.effective_chat.id
    data    = query.data
    s       = sessione(chat_id)
    await query.answer()

    # --- Selezione mittente ---
    if data.startswith("mittente_"):
        idx = int(data.split("_")[1])
        s["from_account"] = EMAIL_ACCOUNTS[idx]
        await query.edit_message_text(f"✅ Account selezionato: **{EMAIL_ACCOUNTS[idx]['label']}**", parse_mode="Markdown")
        return

    # --- Annulla ---
    if data == "annulla":
        reset_sessione(chat_id)
        await query.edit_message_text("🗑 Sessione annullata.")
        return

    # --- Azioni ok → genera prima email ---
    if data == "azioni_ok":
        await query.edit_message_text("✅ Perfetto! Preparo le bozze email una alla volta...")
        await mostra_email(update, chat_id, 0)
        return

    # --- Azioni modifica ---
    if data == "azioni_modifica":
        await query.edit_message_text(
            "✏️ Manda un messaggio con le correzioni da fare "
            "(es. 'cambia email di Marco in marco@newmail.it') "
            "oppure riscrivimi gli appunti corretti."
        )
        s["stato"] = "idle"
        return

    # --- Email: approva e invia ---
    if data.startswith("email_approva_"):
        idx    = int(data.split("_")[2])
        azione = s["azioni"][idx]
        draft  = s["email_drafts"][idx]
        to     = azione.get("destinatario_email", "")

        await query.edit_message_text(f"📤 Invio email a {to}...")
        try:
            invia_email(s["from_account"], to, draft["oggetto"], draft["corpo"])
            azione["_inviata"] = True
            await query.edit_message_text(f"✅ Email inviata a **{azione.get('destinatario_nome')}** ({to})", parse_mode="Markdown")
        except Exception as e:
            azione["_errore"] = True
            await query.edit_message_text(f"❌ Errore invio: {e}\n\nSkippo e continuo.")

        await mostra_email(update, chat_id, idx + 1)
        return

    # --- Email: modifica testo ---
    if data.startswith("email_modifica_"):
        idx = int(data.split("_")[2])
        s["email_modifica_idx"] = idx
        s["stato"] = "attesa_modifica_email"
        await query.edit_message_text(
            "✏️ Invia il nuovo testo del corpo email (solo il corpo, non l'oggetto).\n"
            "Scrivi /annulla per tornare indietro."
        )
        return

    # --- Email: cambia destinatario ---
    if data.startswith("email_cambio_dest_"):
        idx = int(data.split("_")[3])
        s["email_dest_idx"] = idx
        s["stato"] = "attesa_email_dest"
        nome = s["azioni"][idx].get("destinatario_nome", "destinatario")
        await query.edit_message_text(
            f"📧 Inserisci l'indirizzo email di **{nome}**:"
        )
        return

    # --- Email: inserisci destinatario (mancante) ---
    if data.startswith("email_inserisci_dest_"):
        idx = int(data.split("_")[3])
        s["email_dest_idx"] = idx
        s["stato"] = "attesa_email_dest"
        nome = s["azioni"][idx].get("destinatario_nome", "destinatario")
        await query.edit_message_text(
            f"📧 Inserisci l'indirizzo email di **{nome}**:",
            parse_mode="Markdown"
        )
        return

    # --- Email: salta ---
    if data.startswith("email_salta_"):
        idx = int(data.split("_")[2])
        s["azioni"][idx]["_saltata"] = True
        await query.edit_message_text(f"⏭ Email {idx+1} saltata.")
        await mostra_email(update, chat_id, idx + 1)
        return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN non configurato nei Secrets")
    if not OWNER_ID:
        raise ValueError("TELEGRAM_OWNER_ID non configurato nei Secrets")
    if not EMAIL_ACCOUNTS:
        log.warning("Nessun account email configurato — le email non potranno essere inviate")

    log.info(f"Bot avviato — account email configurati: {len(EMAIL_ACCOUNTS)}")
    for acc in EMAIL_ACCOUNTS:
        log.info(f"  • {acc['label']} ({acc['address']})")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("annulla",  cmd_annulla))
    app.add_handler(CommandHandler("mittente", cmd_mittente))
    app.add_handler(MessageHandler(filters.VOICE, handler_vocale))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler_testo))
    app.add_handler(CallbackQueryHandler(handler_callback))

    log.info("Bot in ascolto...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
