"""
bot.py — Bot Telegram Unificato — Sistema Agenti AI Completo
Aziende: Renergy Project&Build | ACM&Partners | RS Gas&Power

Comandi agenti core:
  /run_all    → lancia Renergy + ACM, manda briefing al termine
  /renergy    → solo agente Renergy
  /acm        → solo agente ACM
  /lead       → ultimi lead creati in Hub

Campagne & Mercato:
  /nuova_campagna  → wizard nuova campagna outbound
  /campagne        → lista campagne esistenti
  /mercato         → market intelligence on-demand
  /risposte        → legge e classifica risposte email campagne

Task Tracker:
  /task       → crea task da testo o vocale
  /tasks      → lista task aperti
  /tasks_az   → task per azienda specifica

Content & SEO:
  /content    → genera contenuti LinkedIn/blog
  /contenuti  → lista contenuti in coda
  /seo        → analisi SEO settimanale

Competitor & ESG:
  /competitor → monitoraggio competitor
  /esg        → monitor normativa ESG/CSRD
  /esg_cal    → calendario scadenze ESG

Report:
  /report     → report KPI settimanale
  /rsgas      → aggiorna KPI RS Gas&Power

Newsletter:
  /newsletter → genera bozze newsletter
  /nl_stats   → statistiche newsletter
  /nl_iscrivi → aggiungi iscritto
  /nl_invia   → invia issue approvata

Sistema:
  /stato      → stato sistema e account configurati
  /mittente   → cambia account email di invio
  /annulla    → annulla sessione corrente

Secrets Replit:
  TELEGRAM_BOT_TOKEN     TELEGRAM_OWNER_ID
  OPENAI_API_KEY         CLAUDE_API_KEY
  APOLLO_API_KEY         GOOGLE_MAPS_KEY
  HUB_EMAIL              HUB_PASSWORD
  EMAIL_1_LABEL          EMAIL_1_ADDRESS      EMAIL_1_PASSWORD
  EMAIL_1_SMTP_HOST      EMAIL_1_SMTP_PORT    EMAIL_1_SSL
  EMAIL_1_IMAP_HOST      EMAIL_1_IMAP_PORT    (opzionale per IMAP)
  EMAIL_2_LABEL ...  (aggiungi quanti account vuoi)
  NEWSLETTER_RENERGY_EMAIL  NEWSLETTER_ACM_EMAIL  (opzionale)
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
import agent_task_tracker   as att
import agent_risposte_email as are
import agent_content        as acnt
import agent_seo            as aseo
import agent_competitor     as acomp
import agent_report         as arep
import agent_esg_monitor    as aesg
import newsletter_manager   as nm

# AIOS v2.0 — orchestratore commerciale e nuovi agenti
import shared_intelligence    as si
import agent_orchestratore    as aorch
import agent_tariffe_rs       as atar
import agent_incentivi_renergy as ainc
import agent_crosssell        as acrs
import agent_prospect_rsgas   as apros
import agent_landing          as alanding
import agent_leadmagnet_acm   as alm
import agent_clienti          as aclienti

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
executor = ThreadPoolExecutor(max_workers=4)


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
            # Task tracker state
            "task_step": "idle", "task_drafts": [],
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
PROMPT_AZIONI = """Sei l'assistente personale di un imprenditore italiano con più società
(Renergy Project&Build, ACM&Partners, RS Gas&Power).
Ha registrato appunti dopo una serie di appuntamenti.

APPUNTI:
{note}

Estrai tutte le azioni e le email da inviare. Rispondi SOLO con JSON valido:
{{
  "riepilogo": "2-3 righe di riepilogo compatto",
  "azioni": [
    {{
      "società": "Renergy Project&Build | ACM&Partners | RS Gas&Power | altra",
      "contatto_incontrato": "nome/azienda",
      "descrizione": "cosa va fatto",
      "destinatario_nome": "a chi va assegnato",
      "destinatario_email": "email se menzionata, altrimenti null",
      "scadenza": "quando",
      "priorità": "alta | media | bassa",
      "tipo": "email_collaboratore | email_cliente | reminder | task | altro"
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

def kb_task_conferma(task_list: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Salva tutti", callback_data="task_salva_ok"),
        InlineKeyboardButton("🗑 Annulla",    callback_data="annulla"),
    ]])

def kb_content_item(cnt_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva",  callback_data=f"cnt_approva_{cnt_id}"),
        InlineKeyboardButton("📋 Vedi",    callback_data=f"cnt_vedi_{cnt_id}"),
        InlineKeyboardButton("🗑 Scarta",  callback_data=f"cnt_scarta_{cnt_id}"),
    ]])

def kb_newsletter(issue_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva",  callback_data=f"nl_approva_{issue_id}"),
        InlineKeyboardButton("📋 Anteprima",callback_data=f"nl_vedi_{issue_id}"),
        InlineKeyboardButton("📤 Invia",   callback_data=f"nl_invia_{issue_id}"),
    ]])


# ---------------------------------------------------------------------------
# Tastiere AIOS v2.0 — orchestratore, landing, lead magnet
# ---------------------------------------------------------------------------
ORCH_AZIONE_CALLBACK = {
    "campagna_email":   ("📧 Crea campagna",          "campagna"),
    "newsletter":       ("📨 Vai a newsletter",        "newsletter"),
    "landing_page":     ("🖥 Crea landing",            "landing"),
    "contatto_diretto": ("📞 Segna contatto diretto",  "contatto"),
}

def kb_orchestra(prop_id: str, proposta: dict) -> InlineKeyboardMarkup:
    azioni = []
    for chiave in ("azione_primaria", "azione_secondaria"):
        a = proposta.get(chiave)
        if a in ORCH_AZIONE_CALLBACK and a not in azioni:
            azioni.append(a)
    righe = [[InlineKeyboardButton(ORCH_AZIONE_CALLBACK[a][0],
                                   callback_data=f"orch_{ORCH_AZIONE_CALLBACK[a][1]}_{prop_id}")]
             for a in azioni]
    righe.append([InlineKeyboardButton("🗑 Ignora", callback_data=f"orch_ignora_{prop_id}")])
    return InlineKeyboardMarkup(righe)

def kb_landing_anteprima(land_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Deploy su Netlify", callback_data=f"land_deploy_{land_id}"),
    ]])

def kb_leadmagnet_anteprima(lm_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva e archivia", callback_data=f"lm_approva_{lm_id}"),
        InlineKeyboardButton("🗑 Scarta",             callback_data=f"lm_scarta_{lm_id}"),
    ]])

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
# Comandi lead generation
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_run_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("🚀 Avvio entrambi gli agenti...")
    loop = asyncio.get_event_loop()
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
# Comandi risposte email campagne
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_risposte(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📬 Controllo risposte email in arrivo...")
    loop = asyncio.get_event_loop()
    try:
        nuove = await loop.run_in_executor(executor, are.run)
        if not nuove:
            # Mostra quelle in attesa di gestione
            in_attesa = are.get_risposte_da_gestire()
            if not in_attesa:
                await msg.edit_text("✅ Nessuna risposta email da gestire.")
                return
            await msg.edit_text(f"📬 *{len(in_attesa)} risposte da gestire:*", parse_mode="Markdown")
            for r in in_attesa[:5]:
                await update.effective_message.reply_text(
                    are.formatta_risposta(r), parse_mode="Markdown"
                )
            return
        await msg.edit_text(f"✅ *{len(nuove)} nuove risposte classificate:*", parse_mode="Markdown")
        for r in nuove[:5]:
            await update.effective_message.reply_text(are.formatta_risposta(r), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi Task Tracker
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Crea un task da testo libero."""
    s = sessione(update.effective_chat.id)
    s["task_step"] = "attendi_task_testo"
    await update.message.reply_text(
        "📋 *Nuovo Task*\n\nDescrivimi il task da creare "
        "(anche in modo informale, lo elaboro io):\n\n"
        "Es: _Chiamare Marco di Rossi SpA entro venerdì per il preventivo Renergy_\n"
        "_Prepara report ESG per cliente ACM entro 15 giugno_",
        parse_mode="Markdown"
    )

@solo_owner
async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tasks = att.get_tasks_aperti()
    if not tasks:
        await update.message.reply_text("✅ Nessun task aperto al momento.")
        return
    testo = att.formatta_lista_tasks(tasks, "Task Aperti")
    await update.message.reply_text(testo, parse_mode="Markdown")

@solo_owner
async def cmd_tasks_az(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    aziende_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Renergy",    callback_data="tasks_az_Renergy Project&Build")],
        [InlineKeyboardButton("🏢 ACM",       callback_data="tasks_az_ACM&Partners")],
        [InlineKeyboardButton("⚡🔵 RS Gas",  callback_data="tasks_az_RS Gas&Power")],
        [InlineKeyboardButton("📋 Tutti",     callback_data="tasks_az_tutti")],
    ])
    await update.message.reply_text("Seleziona azienda:", reply_markup=aziende_kb)


# ---------------------------------------------------------------------------
# Comandi Content
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("✍️ Genero contenuti LinkedIn e blog...")
    loop = asyncio.get_event_loop()
    try:
        creati = await loop.run_in_executor(executor, acnt.run)
        if not creati:
            await msg.edit_text("⚠️ Nessun contenuto generato (verifica i feed RSS).")
            return
        await msg.edit_text(f"✅ *{len(creati)} contenuti generati e in coda:*", parse_mode="Markdown")
        for c in creati:
            await update.effective_message.reply_text(
                acnt.formatta_preview_contenuto(c),
                reply_markup=kb_content_item(c["id"]),
                parse_mode="Markdown"
            )
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_contenuti(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    in_attesa = acnt.get_contenuti_in_attesa()
    if not in_attesa:
        await update.message.reply_text("✅ Nessun contenuto in coda.")
        return
    await update.message.reply_text(f"📝 *{len(in_attesa)} contenuti in attesa:*", parse_mode="Markdown")
    for c in in_attesa[:5]:
        await update.effective_message.reply_text(
            acnt.formatta_preview_contenuto(c),
            reply_markup=kb_content_item(c["id"]),
            parse_mode="Markdown"
        )


# ---------------------------------------------------------------------------
# Comandi SEO
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_seo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Analisi SEO in corso...")
    loop = asyncio.get_event_loop()
    try:
        reports = await loop.run_in_executor(executor, aseo.run)
        if not reports:
            await msg.edit_text("⚠️ Nessun report SEO generato.")
            return
        await msg.edit_text(f"✅ Report SEO generati per {len(reports)} aziende:")
        for r in reports:
            await update.effective_message.reply_text(aseo.formatta_report_seo(r), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi Competitor
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_competitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🕵️ Monitoraggio competitor in corso...")
    loop = asyncio.get_event_loop()
    try:
        alerts = await loop.run_in_executor(executor, acomp.run)
        if not alerts:
            await msg.edit_text("✅ Nessun alert significativo dai competitor.")
            return
        await msg.edit_text(f"⚠️ *{len(alerts)} alert competitor:*", parse_mode="Markdown")
        for a in alerts[:4]:
            await update.effective_message.reply_text(acomp.formatta_alert(a), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi Report
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📊 Genero report KPI settimanale...")
    loop = asyncio.get_event_loop()
    try:
        report = await loop.run_in_executor(executor, arep.run)
        testo  = arep.formatta_report(report)
        await msg.edit_text(testo, parse_mode="MarkdownV2")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_rsgas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = sessione(update.effective_chat.id)
    s["stato"] = "attesa_rsgas"
    kpi_attuali = arep.get_kpi_rsgas()
    await update.message.reply_text(
        f"⚡🔵 *Aggiorna KPI RS Gas&Power*\n\n"
        f"KPI attuali: {json.dumps(kpi_attuali, ensure_ascii=False)}\n\n"
        "Invia i nuovi dati come JSON:\n"
        '`{"contratti_attivi": 150, "nuovi_clienti_mese": 12, "churn_mese": 3, "note": "..."}`',
        parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# Comandi AIOS v2.0 — orchestratore commerciale
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_orchestra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🧠 Valuto gli insight e genero proposte commerciali...")
    loop = asyncio.get_event_loop()
    try:
        proposte = await loop.run_in_executor(executor, aorch.run)
        if not proposte:
            await msg.edit_text("ℹ️ Nessun nuovo insight da valutare al momento.")
            return
        await msg.edit_text(f"🧠 *{len(proposte)} proposte commerciali generate:*", parse_mode="Markdown")
        for p in proposte:
            await update.effective_message.reply_text(
                aorch.formatta_proposta(p["insight"], p["proposta"]),
                reply_markup=kb_orchestra(p["prop_id"], p["proposta"]),
                parse_mode="Markdown"
            )
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi AIOS v2.0 — RS Gas&Power: tariffe competitor & prospect
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_tariffe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⚡🔵 Monitoraggio tariffe competitor in corso...")
    loop = asyncio.get_event_loop()
    try:
        _, messaggio, _ = await loop.run_in_executor(executor, atar.run)
        if not messaggio:
            await msg.edit_text("ℹ️ Tariffe invariate rispetto all'ultima rilevazione.")
            return
        await msg.edit_text(messaggio, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_prospect_rsgas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⚡🔵 Ricerca nuovi prospect RS Gas&Power in corso...")
    loop = asyncio.get_event_loop()
    try:
        stats = await loop.run_in_executor(executor, apros.run)
        await msg.edit_text(apros.formatta_riepilogo(stats), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi AIOS v2.0 — Renergy: incentivi e bandi (nazionali + Veneto/Trentino-AA/FVG)
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_incentivi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🇮🇹 Ricerca incentivi e bandi (fonti nazionali + Veneto, Trentino-Alto Adige, "
        "Friuli Venezia Giulia e relative province) in corso..."
    )
    loop = asyncio.get_event_loop()
    try:
        _, messaggio, bozza = await loop.run_in_executor(executor, ainc.run)
        if not messaggio:
            await msg.edit_text("ℹ️ Nessuna novità sugli incentivi rispetto all'ultima rilevazione.")
            return
        await msg.edit_text(messaggio, parse_mode="Markdown")
        if bozza:
            await update.effective_message.reply_text(
                f"✉️ *Bozza email cliente — incentivo rilevato*\n\n"
                f"*Oggetto:* {bozza.get('oggetto','')}\n\n"
                f"─────────────────\n{bozza.get('corpo','')}\n─────────────────",
                parse_mode="Markdown"
            )
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi AIOS v2.0 — landing page & lead magnet PDF
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_landing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🖥 Genero landing page dall'ultimo insight disponibile...")
    loop = asyncio.get_event_loop()
    try:
        insights = si.get_insight_da_sfruttare(giorni_recenti=14)
        if not insights:
            await msg.edit_text("ℹ️ Nessun insight disponibile su cui basare una landing. "
                                "Lancia prima un agente di analisi (es. /mercato, /esg, /competitor).")
            return
        entry = await loop.run_in_executor(executor, alanding.crea_bozza_landing, insights[0])
        await msg.edit_text("✅ Bozza landing generata:")
        await update.effective_message.reply_text(
            alanding.formatta_anteprima(entry),
            reply_markup=kb_landing_anteprima(entry["id"]),
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_leadmagnet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📄 Genero brief PDF lead magnet ACM (HTML → Puppeteer)...")
    loop = asyncio.get_event_loop()
    try:
        entry, testo = await loop.run_in_executor(executor, alm.genera_brief)
        if entry:
            await msg.edit_text(testo, reply_markup=kb_leadmagnet_anteprima(entry["id"]), parse_mode="Markdown")
        else:
            await msg.edit_text(testo, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi AIOS v2.0 — cross-sell & clienti esistenti (upsell/referral)
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_crosssell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Analisi opportunità di cross-sell tra le aziende in corso...")
    loop = asyncio.get_event_loop()
    try:
        _, messaggio = await loop.run_in_executor(executor, acrs.run)
        await msg.edit_text(messaggio, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_clienti(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("👥 Analisi upsell e referral su clienti esistenti in corso...")
    loop = asyncio.get_event_loop()
    try:
        _, _, messaggio = await loop.run_in_executor(executor, aclienti.run)
        await msg.edit_text(messaggio, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")


# ---------------------------------------------------------------------------
# Comandi ESG Monitor
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_esg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🌿 Analisi ESG/CSRD in corso...")
    loop = asyncio.get_event_loop()
    try:
        _, briefing = await loop.run_in_executor(executor, aesg.run)
        if not briefing:
            await msg.edit_text("ℹ️ Nessun aggiornamento ESG significativo rispetto all'ultima analisi.")
            return
        await msg.edit_text(briefing, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_esg_cal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(aesg.formatta_scadenze(), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Comandi Newsletter
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_newsletter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📬 Genero bozze newsletter...")
    loop = asyncio.get_event_loop()
    try:
        bozze = await loop.run_in_executor(executor, nm.run)
        if not bozze:
            # Mostra bozze in attesa
            in_attesa = nm.get_bozze_in_attesa()
            if not in_attesa:
                await msg.edit_text("✅ Nessuna bozza in attesa. Tutte già approvate o inviate.")
                return
            await msg.edit_text(f"📝 *{len(in_attesa)} bozze in attesa di approvazione:*",
                                 parse_mode="Markdown")
            for b in in_attesa[:3]:
                await update.effective_message.reply_text(
                    nm.formatta_preview_newsletter(b),
                    reply_markup=kb_newsletter(b["id"]),
                    parse_mode="Markdown"
                )
            return
        await msg.edit_text(f"✅ *{len(bozze)} bozze newsletter generate:*", parse_mode="Markdown")
        for b in bozze:
            await update.effective_message.reply_text(
                nm.formatta_preview_newsletter(b),
                reply_markup=kb_newsletter(b["id"]),
                parse_mode="Markdown"
            )
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")

@solo_owner
async def cmd_nl_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(nm.formatta_stats_newsletter(), parse_mode="Markdown")

@solo_owner
async def cmd_nl_iscrivi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = sessione(update.effective_chat.id)
    s["stato"] = "attesa_nl_iscrizione"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Renergy Energy Update", callback_data="nl_lista_Renergy")],
        [InlineKeyboardButton("🏢 ACM ESG Insights",     callback_data="nl_lista_ACM")],
    ])
    await update.message.reply_text(
        "📬 *Aggiungi iscritto newsletter*\nSeleziona la newsletter:", reply_markup=kb,
        parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# Comandi base
# ---------------------------------------------------------------------------
@solo_owner
async def cmd_stato(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    apollo  = "✅" if os.environ.get("APOLLO_API_KEY")  else "❌"
    claude_ = "✅" if os.environ.get("CLAUDE_API_KEY")  else "❌"
    openai_ = "✅" if os.environ.get("OPENAI_API_KEY")  else "❌"
    maps    = "✅" if os.environ.get("GOOGLE_MAPS_KEY") else "❌ (Vision disabilitata)"
    hub     = "✅" if os.environ.get("HUB_EMAIL")       else "❌"
    email_n = len(EMAIL_ACCOUNTS)
    nl_stats = nm.get_stats_newsletter()
    nl_txt   = " | ".join(f"{k}: {v['iscritti']}" for k,v in nl_stats.items())
    s = sessione(update.effective_chat.id)
    mittente = s["from_account"]["label"] if s["from_account"] else "non configurato"
    testo = (f"🔧 *Stato Sistema — {datetime.now().strftime('%d/%m/%Y %H:%M')}*\n\n"
             f"Apollo API: {apollo}\nClaude API: {claude_}\n"
             f"OpenAI Whisper: {openai_}\nGoogle Maps: {maps}\n"
             f"Hub CRM: {hub}\n\n"
             f"📤 Account email: {email_n} configurati\n"
             f"Mittente attivo: *{mittente}*\n\n"
             f"📬 Newsletter iscritti: {nl_txt or 'nessuno'}\n"
             f"📋 Task aperti: {len(att.get_tasks_aperti())}\n"
             f"📝 Contenuti in coda: {len(acnt.get_contenuti_in_attesa())}")
    await update.message.reply_text(testo, parse_mode="Markdown")

@solo_owner
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_sessione(update.effective_chat.id)
    await update.message.reply_text(
        "👋 *Bot Agenti AI — Sistema Completo*\n\n"
        "🔍 *Lead Generation:*\n"
        "/run\\_all — Renergy \\+ ACM\n"
        "/renergy — solo Renergy\n"
        "/acm — solo ACM\n"
        "/lead — ultimi lead in Hub\n\n"
        "📬 *Campagne & Mercato:*\n"
        "/nuova\\_campagna — crea campagna outbound\n"
        "/campagne — lista campagne\n"
        "/mercato — market intelligence\n"
        "/risposte — risposte email campagne\n\n"
        "📋 *Task Tracker:*\n"
        "/task — crea task da testo\n"
        "/tasks — lista task aperti\n\n"
        "✍️ *Content & SEO:*\n"
        "/content — genera post LinkedIn/blog\n"
        "/contenuti — coda contenuti\n"
        "/seo — analisi SEO\n\n"
        "🕵️ *Intelligence:*\n"
        "/competitor — monitor competitor\n"
        "/esg — monitor ESG/CSRD\n"
        "/esg\\_cal — calendario scadenze ESG\n\n"
        "📊 *Report:*\n"
        "/report — KPI settimanale\n"
        "/rsgas — aggiorna KPI RS Gas\\&Power\n\n"
        "🧠 *AIOS — Cervello commerciale:*\n"
        "/orchestra — proposte commerciali dagli insight\n"
        "/landing — genera landing page \\+ deploy Netlify\n"
        "/leadmagnet — brief PDF ESG per ACM \\(lead magnet\\)\n"
        "/crosssell — opportunità cross\\-sell tra le aziende\n"
        "/clienti — upsell \\& referral su clienti esistenti\n\n"
        "⚡🔵 *RS Gas\\&Power — Intelligence:*\n"
        "/tariffe — monitor tariffe competitor\n"
        "/prospect\\_rsgas — nuovi prospect qualificati\n\n"
        "🇮🇹 *Renergy — Incentivi:*\n"
        "/incentivi — bandi nazionali \\+ Veneto/Trentino\\-AA/FVG\n\n"
        "📨 *Newsletter:*\n"
        "/newsletter — genera/gestisci bozze\n"
        "/nl\\_stats — statistiche newsletter\n"
        "/nl\\_iscrivi — aggiungi iscritto\n\n"
        "🎙️ *Appunti → Email:*\n"
        "Manda vocale o testo con i tuoi appunti\n\n"
        "/mittente — cambia account invio\n"
        "/stato — stato sistema\n"
        "/annulla — annulla sessione",
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
# Processa input (vocale o testo) — appunti → email / task
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

    # Task tracker — attesa testo task
    if s.get("task_step") == "attendi_task_testo":
        s["task_step"] = "idle"
        msg = await update.message.reply_text("⏳ Elaboro il task...")
        loop = asyncio.get_event_loop()
        try:
            tasks_draft = await loop.run_in_executor(executor, att.estrai_task_da_testo, testo)
            s["task_drafts"] = tasks_draft
            righe = ["📋 *Task estratti:*\n"]
            for i, t in enumerate(tasks_draft):
                righe.append(f"{i+1}. *{t.get('titolo','')}*\n"
                             f"   👤 {t.get('assegnato_a','')} | 🏢 {t.get('azienda','')}\n"
                             f"   ⏰ {t.get('scadenza','')} | 🎯 {t.get('priorita','')}")
            await msg.edit_text("\n".join(righe), reply_markup=kb_task_conferma(tasks_draft),
                                parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Errore task: {e}")
        return

    # RS Gas&Power KPI update
    if s.get("stato") == "attesa_rsgas":
        s["stato"] = "idle"
        try:
            dati = json.loads(testo)
            arep.aggiorna_kpi_rsgas(dati)
            await update.message.reply_text("✅ KPI RS Gas&Power aggiornati.")
        except Exception as e:
            await update.message.reply_text(f"❌ JSON non valido: {e}")
        return

    # Newsletter — iscrizione manuale
    if s.get("stato") == "attesa_nl_dati":
        s["stato"] = "idle"
        nl = s.pop("nl_lista", "Renergy")
        parti = testo.split(",")
        if len(parti) >= 2:
            email_addr = parti[0].strip()
            nome       = parti[1].strip()
            azienda    = parti[2].strip() if len(parti) > 2 else ""
            nm.iscrivi_lead(email_addr, nome, nl, fonte="telegram_manuale")
            await update.message.reply_text(f"✅ {nome} ({email_addr}) iscritto a newsletter {nl}.")
        else:
            await update.message.reply_text("⚠️ Formato: email, nome, azienda")
        return

    # Modifica email
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

    # Delega al campaign handler
    if data.startswith("camp_"):
        await bot_campagna.handler_callback_campagna(update, ctx)
        return

    await q.answer()

    # Mittente email
    if data.startswith("mittente_"):
        idx = int(data.split("_")[1])
        s["from_account"] = EMAIL_ACCOUNTS[idx]
        await q.edit_message_text(f"✅ Mittente: *{EMAIL_ACCOUNTS[idx]['label']}*", parse_mode="Markdown")

    elif data == "annulla":
        reset_sessione(chat_id)
        await q.edit_message_text("🗑 Sessione annullata.")

    # Azioni appunti
    elif data == "azioni_ok":
        await q.edit_message_text("✅ Preparo le email...")
        await mostra_email(update, chat_id, 0)

    elif data == "azioni_modifica":
        await q.edit_message_text("✏️ Scrivi le correzioni o riformula gli appunti:")
        s["stato"] = "idle"

    # Approva/modifica/salta email
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
            await q.edit_message_text(f"✅ Email inviata a *{azione.get('destinatario_nome')}* ({to})",
                                       parse_mode="Markdown")
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

    # Task tracker
    elif data == "task_salva_ok":
        tasks_draft = s.get("task_drafts", [])
        salvati = 0
        for t in tasks_draft:
            try:
                att.crea_task(
                    titolo=t.get("titolo",""),
                    descrizione=t.get("descrizione",""),
                    assegnato_a=t.get("assegnato_a","Da assegnare"),
                    assegnato_email=t.get("assegnato_email") or "",
                    azienda=t.get("azienda","Generale"),
                    scadenza_str=t.get("scadenza",""),
                    priorita=t.get("priorita","media"),
                )
                salvati += 1
            except Exception as e:
                log.warning(f"Task save: {e}")
        s["task_drafts"] = []
        await q.edit_message_text(f"✅ {salvati} task salvati. Usa /tasks per vederli.")

    # Tasks per azienda
    elif data.startswith("tasks_az_"):
        az = data[len("tasks_az_"):]
        if az == "tutti":
            tasks = att.get_tasks_aperti()
            testo = att.formatta_lista_tasks(tasks, "Tutti i Task Aperti")
        else:
            tasks = att.get_tasks_aperti(az)
            testo = att.formatta_lista_tasks(tasks, f"Task — {az}")
        await q.edit_message_text(testo, parse_mode="Markdown")

    # Content queue
    elif data.startswith("cnt_approva_"):
        cnt_id = data[len("cnt_approva_"):]
        acnt.aggiorna_stato_contenuto(cnt_id, "approvato")
        await q.edit_message_text(f"✅ Contenuto {cnt_id} approvato.")

    elif data.startswith("cnt_vedi_"):
        cnt_id = data[len("cnt_vedi_"):]
        queue  = [c for c in acnt.carica_queue() if c["id"] == cnt_id]
        if queue:
            preview = acnt.formatta_preview_contenuto(queue[0], breve=False)
            await q.edit_message_text(preview[:4000], parse_mode="Markdown")

    elif data.startswith("cnt_scarta_"):
        cnt_id = data[len("cnt_scarta_"):]
        acnt.aggiorna_stato_contenuto(cnt_id, "scartato")
        await q.edit_message_text(f"🗑 Contenuto {cnt_id} scartato.")

    # Newsletter
    elif data.startswith("nl_approva_"):
        issue_id = data[len("nl_approva_"):]
        nm.approva_issue(issue_id)
        await q.edit_message_text(f"✅ Newsletter {issue_id} approvata. Usa /newsletter per inviarla.")

    elif data.startswith("nl_vedi_"):
        issue_id = data[len("nl_vedi_"):]
        issue = nm.get_issue(issue_id)
        if issue:
            preview = nm.formatta_preview_newsletter(issue)
            await q.edit_message_text(preview[:4000], parse_mode="Markdown")

    elif data.startswith("nl_invia_"):
        issue_id = data[len("nl_invia_"):]
        await q.edit_message_text(f"📤 Invio newsletter {issue_id}...")
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(executor, nm.invia_issue, issue_id)
            if result.get("errore"):
                await q.edit_message_text(f"❌ {result['errore']}")
            else:
                await q.edit_message_text(
                    f"✅ Newsletter inviata!\n📤 {result['inviati']} consegnate | ❌ {result['errori']} errori"
                )
        except Exception as e:
            await q.edit_message_text(f"❌ Errore invio: {e}")

    elif data.startswith("nl_lista_"):
        nl = data[len("nl_lista_"):]
        s["nl_lista"] = nl
        s["stato"]    = "attesa_nl_dati"
        await q.edit_message_text(
            f"📬 Newsletter: *{nl}*\n\n"
            "Invia i dati dell'iscritto:\n`email, nome, azienda`\n\n"
            "Es: `mario.rossi@azienda.it, Mario Rossi, Rossi SpA`",
            parse_mode="Markdown"
        )

    # Orchestratore — decisione owner su una proposta commerciale
    elif data.startswith("orch_"):
        azione, _, prop_id = data[len("orch_"):].partition("_")
        entry = aorch.get_proposta(prop_id)
        if not entry:
            await q.edit_message_text("⚠️ Proposta non trovata (forse troppo vecchia).")
            return
        insight = si.get_insight(entry.get("insight_id", ""))

        if azione == "ignora":
            aorch.registra_decisione(prop_id, "ignorata")
            await q.edit_message_text("🗑 Proposta ignorata.")

        elif azione == "landing":
            aorch.registra_decisione(prop_id, "landing_page")
            if not insight:
                await q.edit_message_text("⚠️ Insight di origine non più disponibile.")
                return
            await q.edit_message_text("🖥 Genero la landing page...")
            loop = asyncio.get_event_loop()
            try:
                land_entry = await loop.run_in_executor(executor, alanding.crea_bozza_landing, insight)
                await update.effective_message.reply_text(
                    alanding.formatta_anteprima(land_entry),
                    reply_markup=kb_landing_anteprima(land_entry["id"]),
                    parse_mode="Markdown"
                )
            except Exception as e:
                await update.effective_message.reply_text(f"❌ Errore generazione landing: {e}")

        elif azione == "campagna":
            aorch.registra_decisione(prop_id, "campagna_email")
            if insight:
                try: si.segna_usato(insight["id"], "campagna")
                except Exception: pass
            await q.edit_message_text(
                "📧 Ok — usa /nuova_campagna per creare la campagna basata su questa proposta."
            )

        elif azione == "newsletter":
            aorch.registra_decisione(prop_id, "newsletter")
            if insight:
                try: si.segna_usato(insight["id"], "newsletter")
                except Exception: pass
            await q.edit_message_text("📨 Ok — usa /newsletter per generare e approvare la bozza.")

        elif azione == "contatto":
            aorch.registra_decisione(prop_id, "contatto_diretto")
            if insight:
                try: si.segna_usato(insight["id"], "contatto_diretto")
                except Exception: pass
            await q.edit_message_text("📞 Segnato come contatto diretto da gestire personalmente.")

    # Landing page — deploy su Netlify
    elif data.startswith("land_"):
        azione, _, land_id = data[len("land_"):].partition("_")
        if azione == "deploy":
            await q.edit_message_text("🚀 Pubblico la landing su Netlify...")
            loop = asyncio.get_event_loop()
            try:
                esito      = await loop.run_in_executor(executor, alanding.pubblica_landing, land_id)
                land_entry = alanding.get_landing(land_id)
                await q.edit_message_text(alanding.formatta_esito_deploy(esito, land_entry), parse_mode="Markdown")
            except Exception as e:
                await q.edit_message_text(f"❌ Errore deploy: {e}")

    # Lead magnet PDF ACM — approvazione/scarto
    elif data.startswith("lm_"):
        azione, _, lm_id = data[len("lm_"):].partition("_")
        if azione == "approva":
            lm_entry = alm.approva_brief(lm_id)
            if lm_entry:
                await q.edit_message_text(alm.formatta_esito_approvazione(lm_entry), parse_mode="Markdown")
            else:
                await q.edit_message_text("⚠️ Brief non trovato.")
        elif azione == "scarta":
            lm_entry = alm.scarta_brief(lm_id)
            await q.edit_message_text(f"🗑 Brief `{lm_id}` scartato." if lm_entry else "⚠️ Brief non trovato.",
                                       parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Job schedulati
# ---------------------------------------------------------------------------
async def briefing_mattutino(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Briefing mattutino — avvio agenti lead gen...")
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

async def job_report_settimanale(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: report KPI settimanale")
    loop = asyncio.get_event_loop()
    try:
        report = await loop.run_in_executor(executor, arep.run)
        testo  = arep.formatta_report(report)
        await ctx.bot.send_message(OWNER_ID, testo, parse_mode="MarkdownV2")
    except Exception as e:
        log.warning(f"Job report: {e}")

async def job_esg_monitor(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: ESG monitor")
    loop = asyncio.get_event_loop()
    try:
        _, briefing = await loop.run_in_executor(executor, aesg.run)
        if briefing:
            await ctx.bot.send_message(OWNER_ID, briefing, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job ESG: {e}")

async def job_content(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: generazione contenuti settimanali")
    loop = asyncio.get_event_loop()
    try:
        creati = await loop.run_in_executor(executor, acnt.run)
        if creati:
            testo = f"✍️ *{len(creati)} contenuti generati* e in coda per approvazione.\nUsa /contenuti per revisione."
            await ctx.bot.send_message(OWNER_ID, testo, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job content: {e}")

async def job_competitor(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: monitoraggio competitor")
    loop = asyncio.get_event_loop()
    try:
        alerts = await loop.run_in_executor(executor, acomp.run)
        if alerts:
            await ctx.bot.send_message(
                OWNER_ID,
                f"🕵️ *{len(alerts)} alert competitor — urgenti:*",
                parse_mode="Markdown"
            )
            for a in alerts[:3]:
                await ctx.bot.send_message(OWNER_ID, acomp.formatta_alert(a), parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job competitor: {e}")

async def job_task_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: task reminder")
    try:
        testo = att.genera_reminder_text()
        if testo:
            await ctx.bot.send_message(OWNER_ID, testo, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job task reminder: {e}")

async def job_orchestratore(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: orchestratore commerciale")
    loop = asyncio.get_event_loop()
    try:
        proposte = await loop.run_in_executor(executor, aorch.run)
        for p in proposte:
            await ctx.bot.send_message(
                OWNER_ID, aorch.formatta_proposta(p["insight"], p["proposta"]),
                reply_markup=kb_orchestra(p["prop_id"], p["proposta"]), parse_mode="Markdown"
            )
    except Exception as e:
        log.warning(f"Job orchestratore: {e}")

async def job_prospect_rsgas(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: prospect RS Gas&Power")
    loop = asyncio.get_event_loop()
    try:
        stats = await loop.run_in_executor(executor, apros.run)
        await ctx.bot.send_message(OWNER_ID, apros.formatta_riepilogo(stats), parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job prospect RS Gas: {e}")

async def job_incentivi_renergy(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: incentivi Renergy (nazionali + Veneto/Trentino-AA/FVG)")
    loop = asyncio.get_event_loop()
    try:
        _, messaggio, bozza = await loop.run_in_executor(executor, ainc.run)
        if messaggio:
            await ctx.bot.send_message(OWNER_ID, messaggio, parse_mode="Markdown")
        if bozza:
            await ctx.bot.send_message(
                OWNER_ID,
                f"✉️ *Bozza email cliente — incentivo rilevato*\n\n*Oggetto:* {bozza.get('oggetto','')}\n\n"
                f"─────────────────\n{bozza.get('corpo','')}\n─────────────────",
                parse_mode="Markdown"
            )
    except Exception as e:
        log.warning(f"Job incentivi Renergy: {e}")

async def job_crosssell(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: cross-sell")
    loop = asyncio.get_event_loop()
    try:
        _, messaggio = await loop.run_in_executor(executor, acrs.run)
        await ctx.bot.send_message(OWNER_ID, messaggio, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job cross-sell: {e}")

async def job_tariffe_rs(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: tariffe competitor RS Gas&Power")
    loop = asyncio.get_event_loop()
    try:
        _, messaggio, _ = await loop.run_in_executor(executor, atar.run)
        if messaggio:
            await ctx.bot.send_message(OWNER_ID, messaggio, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job tariffe RS Gas: {e}")

async def job_clienti(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: clienti — upsell & referral")
    loop = asyncio.get_event_loop()
    try:
        _, _, messaggio = await loop.run_in_executor(executor, aclienti.run)
        await ctx.bot.send_message(OWNER_ID, messaggio, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job clienti: {e}")

async def job_leadmagnet_acm(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: lead magnet PDF ACM")
    loop = asyncio.get_event_loop()
    try:
        entry, testo = await loop.run_in_executor(executor, alm.genera_brief)
        if entry:
            await ctx.bot.send_message(OWNER_ID, testo, reply_markup=kb_leadmagnet_anteprima(entry["id"]), parse_mode="Markdown")
        elif testo:
            await ctx.bot.send_message(OWNER_ID, testo, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Job lead magnet ACM: {e}")


async def job_newsletter(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Job: generazione newsletter settimanale")
    loop = asyncio.get_event_loop()
    try:
        bozze = await loop.run_in_executor(executor, nm.run)
        if bozze:
            nomi = ", ".join(b.get("subject","")[:30] for b in bozze)
            await ctx.bot.send_message(
                OWNER_ID,
                f"📬 *Newsletter generate*\nBozze: {nomi}\nUsa /newsletter per approvare e inviare.",
                parse_mode="Markdown"
            )
    except Exception as e:
        log.warning(f"Job newsletter: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN mancante")
    if not OWNER_ID:
        raise ValueError("TELEGRAM_OWNER_ID mancante")

    log.info(f"Bot avviato | Email account: {len(EMAIL_ACCOUNTS)}")

    bot_campagna.init(sys.modules[__name__])

    app = Application.builder().token(BOT_TOKEN).build()

    # Comandi base
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("annulla",  cmd_annulla))
    app.add_handler(CommandHandler("mittente", cmd_mittente))
    app.add_handler(CommandHandler("stato",    cmd_stato))

    # Lead generation
    app.add_handler(CommandHandler("run_all",  cmd_run_all))
    app.add_handler(CommandHandler("renergy",  cmd_renergy))
    app.add_handler(CommandHandler("acm",      cmd_acm))
    app.add_handler(CommandHandler("lead",     cmd_lead))

    # Campagne e mercato
    bot_campagna.registra_handlers(app)
    app.add_handler(CommandHandler("risposte", cmd_risposte))

    # Task tracker
    app.add_handler(CommandHandler("task",     cmd_task))
    app.add_handler(CommandHandler("tasks",    cmd_tasks))
    app.add_handler(CommandHandler("tasks_az", cmd_tasks_az))

    # Content & SEO
    app.add_handler(CommandHandler("content",   cmd_content))
    app.add_handler(CommandHandler("contenuti", cmd_contenuti))
    app.add_handler(CommandHandler("seo",       cmd_seo))

    # Competitor & ESG
    app.add_handler(CommandHandler("competitor", cmd_competitor))
    app.add_handler(CommandHandler("esg",        cmd_esg))
    app.add_handler(CommandHandler("esg_cal",    cmd_esg_cal))

    # Report
    app.add_handler(CommandHandler("report",  cmd_report))
    app.add_handler(CommandHandler("rsgas",   cmd_rsgas))

    # AIOS v2.0 — orchestratore commerciale e nuovi agenti
    app.add_handler(CommandHandler("orchestra",      cmd_orchestra))
    app.add_handler(CommandHandler("tariffe",        cmd_tariffe))
    app.add_handler(CommandHandler("incentivi",      cmd_incentivi))
    app.add_handler(CommandHandler("landing",        cmd_landing))
    app.add_handler(CommandHandler("leadmagnet",     cmd_leadmagnet))
    app.add_handler(CommandHandler("crosssell",      cmd_crosssell))
    app.add_handler(CommandHandler("clienti",        cmd_clienti))
    app.add_handler(CommandHandler("prospect_rsgas", cmd_prospect_rsgas))

    # Newsletter
    app.add_handler(CommandHandler("newsletter", cmd_newsletter))
    app.add_handler(CommandHandler("nl_stats",   cmd_nl_stats))
    app.add_handler(CommandHandler("nl_iscrivi", cmd_nl_iscrivi))

    # Messaggi
    app.add_handler(MessageHandler(filters.VOICE,                   handler_vocale))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler_testo))
    app.add_handler(CallbackQueryHandler(handler_callback))

    # Scheduling
    # Lead gen — lunedì 07:00
    app.job_queue.run_daily(briefing_mattutino,    time=dt_time(7, 0),  days=(0,))
    # Market intelligence — martedì 08:00
    app.job_queue.run_daily(bot_campagna.job_mercato, time=dt_time(8, 0),  days=(1,))
    # Sequenze email campagne — tutti i giorni 08:30
    app.job_queue.run_daily(bot_campagna.job_sequenze, time=dt_time(8, 30), days=tuple(range(7)))
    # Task reminder — tutti i giorni 09:00
    app.job_queue.run_daily(job_task_reminder,     time=dt_time(9, 0),  days=tuple(range(7)))
    # Report KPI settimanale — lunedì 07:30
    app.job_queue.run_daily(job_report_settimanale, time=dt_time(7, 30), days=(0,))
    # ESG monitor — mercoledì 09:00
    app.job_queue.run_daily(job_esg_monitor,       time=dt_time(9, 0),  days=(2,))
    # Content generation — giovedì 09:00
    app.job_queue.run_daily(job_content,           time=dt_time(9, 0),  days=(3,))
    # Competitor monitor — venerdì 09:00
    app.job_queue.run_daily(job_competitor,        time=dt_time(9, 0),  days=(4,))
    # Newsletter — venerdì 10:00
    app.job_queue.run_daily(job_newsletter,        time=dt_time(10, 0), days=(4,))

    # --- AIOS v2.0 — orchestratore commerciale e nuovi agenti -----------------
    # Orchestratore commerciale — ogni giorno 06:30 (prima di tutti gli altri agenti)
    app.job_queue.run_daily(job_orchestratore,     time=dt_time(6, 30), days=tuple(range(7)))
    # Prospect RS Gas&Power — lunedì 07:00 (insieme a Renergy/ACM del briefing mattutino)
    app.job_queue.run_daily(job_prospect_rsgas,    time=dt_time(7, 0),  days=(0,))
    # Incentivi Renergy — nazionali + Veneto/Trentino-Alto Adige/FVG e province — martedì e venerdì 07:00
    app.job_queue.run_daily(job_incentivi_renergy, time=dt_time(7, 0),  days=(1, 4))
    # Cross-sell tra le tre aziende — lunedì 07:15
    app.job_queue.run_daily(job_crosssell,         time=dt_time(7, 15), days=(0,))
    # Tariffe competitor RS Gas&Power — lunedì e giovedì 07:45
    app.job_queue.run_daily(job_tariffe_rs,        time=dt_time(7, 45), days=(0, 3))
    # Clienti esistenti — upsell & referral — giovedì 09:30
    app.job_queue.run_daily(job_clienti,           time=dt_time(9, 30), days=(3,))
    # Lead magnet PDF ACM — venerdì 10:00 (insieme alla newsletter)
    app.job_queue.run_daily(job_leadmagnet_acm,    time=dt_time(10, 0), days=(4,))
    # Risposte email campagne — ogni giorno 10:30
    async def _job_risposte(ctx: ContextTypes.DEFAULT_TYPE):
        loop = asyncio.get_event_loop()
        nuove = await loop.run_in_executor(executor, are.run)
        if nuove:
            await ctx.bot.send_message(OWNER_ID, f"📬 {len(nuove)} nuove risposte email. Usa /risposte.")
    app.job_queue.run_daily(_job_risposte, time=dt_time(10, 30), days=tuple(range(7)))

    log.info("Sistema agenti AI avviato — scheduling attivo")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
