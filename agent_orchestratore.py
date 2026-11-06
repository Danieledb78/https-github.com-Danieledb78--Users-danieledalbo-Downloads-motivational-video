"""
agent_orchestratore.py — Cervello Commerciale Centrale (AIOS v2.0)
Legge shared_intelligence.json, valuta ogni insight non ancora sfruttato e propone
all'owner un'azione commerciale concreta (campagna, landing, newsletter, contatto diretto).

Schedulato ogni giorno alle 06:30, prima di tutti gli altri agenti.
Trigger manuale: /orchestra
"""

import os, json, logging
from datetime import datetime
from pathlib import Path
import anthropic

import shared_intelligence as si
import campagna_manager as cm
import newsletter_manager as nm
import agent_content as acnt
import config

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)
LOG_FILE   = "orchestratore_log.json"

AZIENDE_NOME = {
    "Renergy": "Renergy Project&Build",
    "RS_Gas":  "RS Gas&Power",
    "ACM":     "ACM&Partners",
    "Tutte":   "Tutte e tre le aziende",
}


# ---------------------------------------------------------------------------
# Contesto — cosa sta succedendo oggi nel sistema commerciale
# ---------------------------------------------------------------------------
def raccogli_contesto(azienda: str) -> dict:
    nome_az = AZIENDE_NOME.get(azienda, azienda)
    try:
        campagne = [c for c in cm.get_campagne_attive()
                    if azienda == "Tutte" or c.get("azienda") == nome_az]
    except Exception:
        campagne = []
    try:
        leads_nl = nm.get_stats_newsletter()
    except Exception:
        leads_nl = {}
    try:
        contenuti = [c.get("titolo", "") for c in acnt.carica_queue()
                     if c.get("stato") == "pubblicato"][-5:]
    except Exception:
        contenuti = []

    return {
        "campagne_attive":   [c.get("nome", c.get("id", "")) for c in campagne][:8],
        "iscritti_newsletter": leads_nl,
        "contenuti_recenti": contenuti,
    }


# ---------------------------------------------------------------------------
# Claude — valutazione azione commerciale
# ---------------------------------------------------------------------------
PROMPT_ORCHESTRATORE = """Sei il direttore commerciale di un gruppo con tre aziende:
 - Renergy (fotovoltaico industriale Veneto)
 - RS Gas&Power (rivendita energia ARERA, network marketing)
 - ACM&Partners (consulenza ESG, M&A, China/Australia Desk)

Insight disponibile:
{insight_json}

Contesto commerciale attuale per l'azienda interessata:
- Campagne attive: {campagne}
- Iscritti newsletter: {newsletter}
- Contenuti pubblicati di recente: {contenuti}

Valuta OGNI possibile azione commerciale e rispondi SOLO in JSON:
{{
  "azione_primaria": "campagna_email|newsletter|landing_page|contatto_diretto|nessuna",
  "azione_secondaria": "campagna_email|newsletter|landing_page|contatto_diretto|nessuna",
  "giustificazione": "max 3 frasi sul perché questa è l'azione giusta ora",
  "urgenza_azione": "oggi|questa_settimana|questo_mese",
  "target_specifico": "descrizione ICP per questa azione",
  "messaggio_chiave": "il claim principale da usare",
  "kpi_atteso": "es. 5 lead qualificati in 2 settimane",
  "note_owner": "cosa deve sapere Daniele prima di approvare"
}}"""

def genera_proposta(insight: dict, contesto: dict) -> dict:
    insight_compatto = {k: insight[k] for k in
                        ("titolo", "sintesi", "tipo", "azienda", "urgenza",
                         "impatto_commerciale", "target_icp", "finestra_temporale",
                         "azioni_suggerite") if k in insight}
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=900, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_ORCHESTRATORE.format(
            insight_json=json.dumps(insight_compatto, ensure_ascii=False, indent=2),
            campagne=", ".join(contesto.get("campagne_attive", [])) or "nessuna",
            newsletter=json.dumps(contesto.get("iscritti_newsletter", {}), ensure_ascii=False),
            contenuti=", ".join(contesto.get("contenuti_recenti", [])) or "nessuno",
        )}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Storage log proposte/decisioni
# ---------------------------------------------------------------------------
def carica_log() -> list[dict]:
    if Path(LOG_FILE).exists():
        with open(LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_proposta(insight_id: str, proposta: dict) -> str:
    log_entries = carica_log()
    entry = {
        "id":          f"prop_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "insight_id":  insight_id,
        "proposta":    proposta,
        "creata_il":   datetime.now().isoformat(),
        "decisione":   None,        # azione scelta dall'owner | "ignorata"
        "decisa_il":   None,
    }
    log_entries.append(entry)
    log_entries = log_entries[-200:]
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_entries, f, ensure_ascii=False, indent=2)
    return entry["id"]

def registra_decisione(prop_id: str, decisione: str):
    entries = carica_log()
    for e in entries:
        if e["id"] == prop_id:
            e["decisione"] = decisione
            e["decisa_il"] = datetime.now().isoformat()
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

def get_proposta(prop_id: str) -> dict | None:
    return next((e for e in carica_log() if e["id"] == prop_id), None)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
MAX_INSIGHT_PER_RUN = 4   # limita i costi Claude Opus — solo i più recenti/rilevanti

def run() -> list[dict]:
    """Valuta gli insight non sfruttati e genera proposte commerciali. Ritorna lista di
    {insight, proposta, prop_id} pronte per essere mostrate su Telegram con bottoni."""
    log.info("=== ORCHESTRATORE — avvio ===")
    insights = si.get_insight_da_sfruttare(giorni_recenti=7)
    insights.sort(key=lambda i: {"alto": 0, "medio": 1}.get(i.get("impatto_commerciale"), 2))
    insights = insights[:MAX_INSIGHT_PER_RUN]

    proposte = []
    for insight in insights:
        try:
            contesto = raccogli_contesto(insight.get("azienda", "Tutte"))
            proposta = genera_proposta(insight, contesto)
            prop_id  = salva_proposta(insight["id"], proposta)
            proposte.append({"insight": insight, "proposta": proposta, "prop_id": prop_id})
            log.info(f"Proposta generata per insight {insight['id']}: {proposta.get('azione_primaria')}")
        except Exception as e:
            log.warning(f"Orchestratore — errore su insight {insight.get('id')}: {e}")

    log.info(f"=== ORCHESTRATORE — {len(proposte)} proposte generate ===")
    return proposte


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
AZIONE_LABEL = {
    "campagna_email":  "📧 Campagna Email",
    "newsletter":      "📨 Newsletter",
    "landing_page":    "🖥 Landing Page",
    "contatto_diretto":"📞 Contatto diretto",
    "nessuna":         "— Nessuna azione",
}
URGENZA_EMOJI = {"oggi": "🔴", "questa_settimana": "🟡", "questo_mese": "🟢"}

def formatta_proposta(insight: dict, proposta: dict) -> str:
    az_emoji = {"Renergy": "⚡", "RS_Gas": "⚡🔵", "ACM": "🏢", "Tutte": "🔄"}.get(insight.get("azienda"), "•")
    u_emoji  = URGENZA_EMOJI.get(proposta.get("urgenza_azione", ""), "•")
    righe = [
        f"🧠 *PROPOSTA COMMERCIALE — {az_emoji} {AZIENDE_NOME.get(insight.get('azienda',''), '')}*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 Insight: {insight.get('titolo','')}",
        f"🎯 Azione suggerita: *{AZIONE_LABEL.get(proposta.get('azione_primaria',''), '—')}*",
    ]
    if proposta.get("azione_secondaria") not in (None, "nessuna", ""):
        righe.append(f"➕ Azione secondaria: {AZIONE_LABEL.get(proposta.get('azione_secondaria',''), '—')}")
    righe += [
        f"{u_emoji} Urgenza: *{proposta.get('urgenza_azione','').replace('_',' ')}*",
        f"👥 Target: {proposta.get('target_specifico','')}",
        f"💬 Messaggio chiave: _{proposta.get('messaggio_chiave','')}_",
        f"📊 KPI atteso: {proposta.get('kpi_atteso','')}",
        f"📝 {proposta.get('giustificazione','')}",
    ]
    if proposta.get("note_owner"):
        righe.append(f"⚠️ Nota: {proposta['note_owner']}")
    return "\n".join(righe)
