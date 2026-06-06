"""
agent_esg_monitor.py — Agente Monitor ESG & Normativa
Monitora: CSRD, EU Taxonomy, normativa sostenibilità italiana,
obblighi di rendicontazione ESG per PMI.
Invia alert quando ci sono aggiornamenti rilevanti per ACM&Partners e i suoi clienti.
"""

import os, json, logging, feedparser, hashlib
from datetime import datetime
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)
ESG_FILE   = "esg_monitor_data.json"

FEED_ESG = [
    # CSRD e normativa europea
    "https://news.google.com/rss/search?q=CSRD+obbligo+PMI+2026+Italia&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=tassonomia+europea+green+imprese+Italia&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=rendicontazione+sostenibilita+obbligatoria+PMI&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=ESG+rating+banche+finanziamenti+PMI+Italia&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=ESRS+standard+sostenibilita+imprese+italiane&hl=it&gl=IT&ceid=IT:it",
    # English — regolamentazione EU
    "https://news.google.com/rss/search?q=CSRD+SME+exemption+Italy+2026&hl=en&gl=IT&ceid=IT:en",
    "https://news.google.com/rss/search?q=EU+taxonomy+regulation+update+2026&hl=en&gl=IT&ceid=IT:en",
    "https://news.google.com/rss/search?q=ESG+supply+chain+due+diligence+Italy&hl=en&gl=IT&ceid=IT:en",
]

SCADENZE_CSRD = [
    {"data": "2025-01-01", "descrizione": "CSRD — grandi imprese EU (>500 dipendenti)", "impatto": "alto"},
    {"data": "2026-01-01", "descrizione": "CSRD — grandi imprese quotate (>250 dip.)",  "impatto": "alto"},
    {"data": "2027-01-01", "descrizione": "CSRD — PMI quotate in borsa (250+ dip.)",    "impatto": "alto"},
    {"data": "2028-01-01", "descrizione": "CSRD — PMI non quotate (graduale)",           "impatto": "medio"},
    {"data": "2026-06-01", "descrizione": "SFDR Level 2 aggiornamento obblighi banche", "impatto": "medio"},
]


# ---------------------------------------------------------------------------
# Raccolta notizie
# ---------------------------------------------------------------------------
def raccogli_notizie_esg() -> list[dict]:
    notizie, visti = [], set()
    for url in FEED_ESG:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:4]:
                t = e.get("title","").strip()
                if t and t not in visti:
                    visti.add(t)
                    sommario = e.get("summary","").replace("<b>","").replace("</b>","")[:300]
                    notizie.append({"titolo": t, "sommario": sommario, "link": e.get("link","")})
        except Exception as ex:
            log.warning(f"Feed ESG: {ex}")
    return notizie


# ---------------------------------------------------------------------------
# Scadenze imminenti
# ---------------------------------------------------------------------------
def get_scadenze_imminenti(giorni: int = 90) -> list[dict]:
    oggi    = datetime.now().date()
    soglia  = oggi
    from datetime import date
    scadenti = []
    for s in SCADENZE_CSRD:
        try:
            data_sc = datetime.strptime(s["data"], "%Y-%m-%d").date()
            diff = (data_sc - oggi).days
            if 0 <= diff <= giorni:
                scadenti.append({**s, "giorni_mancanti": diff})
            elif diff < 0:
                scadenti.append({**s, "giorni_mancanti": diff, "scaduta": True})
        except Exception:
            pass
    return sorted(scadenti, key=lambda x: x["giorni_mancanti"])


# ---------------------------------------------------------------------------
# Claude — analisi impatto normativo
# ---------------------------------------------------------------------------
PROMPT_ESG = """Sei un esperto di normativa ESG e sostenibilità aziendale per il mercato italiano.
Analizza le seguenti notizie e identifica impatti rilevanti per ACM&Partners,
una società di consulenza manageriale specializzata in ESG per PMI italiane.

Notizie:
{notizie}

Rispondi SOLO con JSON valido:
{{
  "alert_level": "critico | importante | informativo",
  "sintesi": "3-4 righe su cosa sta succedendo nel panorama ESG",
  "impatti_acm": [
    {{
      "area": "CSRD | EU_Taxonomy | SFDR | LkSG | altro",
      "descrizione": "cosa cambia e perché è rilevante",
      "clienti_impattati": "quali tipologie di clienti PMI sono coinvolti",
      "opportunita_servizio": "servizio ACM che può rispondere a questa esigenza",
      "urgenza": "immediata | entro_3_mesi | entro_anno"
    }}
  ],
  "notizie_top": [
    {{
      "titolo": "titolo notizia",
      "rilevanza": "perché è importante per ACM",
      "azione": "cosa fare concretamente"
    }}
  ],
  "messaggi_chiave_clienti": [
    "punto chiave da comunicare ai clienti PMI"
  ]
}}"""

def analizza_esg(notizie: list[dict]) -> dict:
    notizie_txt = "\n".join(f"- {n['titolo']}: {n['sommario']}" for n in notizie[:15])
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT_ESG.format(notizie=notizie_txt)}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def carica_dati() -> dict:
    if Path(ESG_FILE).exists():
        with open(ESG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"analisi": [], "ultimo_hash": ""}

def salva_dati(dati: dict):
    with open(ESG_FILE, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> tuple[dict, str]:
    log.info("=== AGENTE ESG MONITOR — avvio ===")
    dati    = carica_dati()
    notizie = raccogli_notizie_esg()
    if not notizie:
        return {}, "⚠️ Nessuna notizia ESG raccolta."

    h = hashlib.md5("".join(n["titolo"] for n in notizie).encode()).hexdigest()
    if dati.get("ultimo_hash") == h:
        log.info("Notizie ESG invariate — nessun update")
        return {}, ""

    analisi = analizza_esg(notizie)
    analisi["data"] = datetime.now().isoformat()
    dati["analisi"].append(analisi)
    dati["analisi"]   = dati["analisi"][-20:]
    dati["ultimo_hash"] = h
    salva_dati(dati)

    briefing = formatta_briefing_esg(analisi)
    log.info("=== AGENTE ESG MONITOR — completato ===")
    return analisi, briefing


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_briefing_esg(analisi: dict) -> str:
    lv_emoji = {"critico":"🔴","importante":"🟡","informativo":"🟢"}.get(
        analisi.get("alert_level","informativo"), "ℹ️"
    )
    righe = [f"🌿 *ESG Monitor — {datetime.now().strftime('%d/%m/%Y')}*",
             f"{lv_emoji} Alert level: *{analisi.get('alert_level','').upper()}*\n",
             f"_{analisi.get('sintesi','')}_\n"]

    scadenze = get_scadenze_imminenti(90)
    if scadenze:
        righe.append("⏰ *Scadenze CSRD/ESG prossime:*")
        for s in scadenze[:3]:
            giorni = s.get("giorni_mancanti", 0)
            s_emoji = "🔴" if giorni <= 30 else ("🟡" if giorni <= 60 else "🟢")
            stato   = f"fra {giorni}gg" if giorni >= 0 else f"SCADUTA {-giorni}gg fa"
            righe.append(f"{s_emoji} {s['descrizione']} — {stato}")
        righe.append("")

    impatti = analisi.get("impatti_acm",[])[:2]
    if impatti:
        righe.append("*📋 Impatti per ACM&Partners:*")
        for i in impatti:
            u_emoji = {"immediata":"🔴","entro_3_mesi":"🟡","entro_anno":"🟢"}.get(i.get("urgenza",""),"•")
            righe.append(f"{u_emoji} *{i.get('area','')}* — {i.get('descrizione','')}")
            righe.append(f"   💼 Servizio: _{i.get('opportunita_servizio','')}_")

    messaggi = analisi.get("messaggi_chiave_clienti",[])[:2]
    if messaggi:
        righe.append("\n*💬 Da comunicare ai clienti:*")
        for m in messaggi:
            righe.append(f"• {m}")

    righe.append("\n_Usa /esg per rilanciare l'analisi._")
    return "\n".join(righe)


def formatta_scadenze() -> str:
    scadenze = SCADENZE_CSRD
    righe = ["📅 *Scadenze CSRD/ESG — Calendario Completo*\n"]
    oggi  = datetime.now().date()
    for s in sorted(scadenze, key=lambda x: x["data"]):
        try:
            data_sc = datetime.strptime(s["data"], "%Y-%m-%d").date()
            diff    = (data_sc - oggi).days
            if diff < 0:
                emoji = "✅"
                stato = "Entrata in vigore"
            elif diff <= 30:
                emoji = "🔴"
                stato = f"fra {diff} giorni"
            elif diff <= 90:
                emoji = "🟡"
                stato = f"fra {diff} giorni"
            else:
                emoji = "🟢"
                stato = f"fra {diff} giorni"
            righe.append(f"{emoji} *{s['data']}* — {s['descrizione']}\n   {stato}")
        except Exception:
            righe.append(f"• {s['data']} — {s['descrizione']}")
    return "\n".join(righe)
