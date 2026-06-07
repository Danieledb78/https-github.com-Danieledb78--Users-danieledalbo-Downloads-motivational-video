"""
agent_competitor.py — Agente Monitoraggio Competitor
Monitora siti e news dei competitor tramite RSS e Google News.
Claude analizza i cambiamenti significativi e invia alert.
Salva snapshot in competitor_data.json.
"""

import os, json, logging, feedparser, hashlib
from datetime import datetime
from pathlib import Path
import anthropic
import shared_intelligence as si

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)
COMP_FILE  = "competitor_data.json"

COMPETITOR_PROFILES = {
    "Renergy Project&Build": [
        {"nome": "Competitor Fotovoltaico IT",  "query": "fotovoltaico industriale installatore Veneto concorrenti"},
        {"nome": "Installatori FV Nord Italia", "query": "impianti solari industriali nord Italia prezzi offerte 2026"},
        {"nome": "EV Charging Business",        "query": "stazioni ricarica EV aziende installazione Italia"},
        {"nome": "Rinnovabili Industria",       "query": "efficienza energetica industria offerta consulenza Italia"},
    ],
    "ACM&Partners": [
        {"nome": "Consulenza ESG Italia",        "query": "consulenza ESG PMI Italia 2026 servizi"},
        {"nome": "Competitor M&A PMI",           "query": "M&A consulenza PMI nord Italia acquisizioni"},
        {"nome": "Rating ESG Providers",         "query": "rating ESG certificazione aziende Italia"},
        {"nome": "Consulenza strategica PMI",    "query": "consulenza strategica PMI crescita Italia 2026"},
    ],
    "RS Gas&Power": [
        {"nome": "Broker luce e gas B2B",        "query": "offerte luce gas business PMI Italia 2026"},
        {"nome": "Mercato energia liberalizzato", "query": "prezzi energia elettrica gas naturale imprese Italia"},
        {"nome": "Fornitori energia rinnovabile", "query": "fornitura energia verde certificata imprese Italia"},
    ],
}

MAX_ENTRIES = 5


# ---------------------------------------------------------------------------
# Raccolta news competitor via Google News RSS
# ---------------------------------------------------------------------------
def cerca_notizie_competitor(query: str) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={query.replace(' ','+')}&hl=it&gl=IT&ceid=IT:it"
    notizie = []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries[:MAX_ENTRIES]:
            notizie.append({
                "titolo":   e.get("title","").strip(),
                "sommario": e.get("summary","")[:200].replace("<b>","").replace("</b>",""),
                "link":     e.get("link",""),
                "data":     e.get("published",""),
            })
    except Exception as ex:
        log.warning(f"Feed competitor: {ex}")
    return notizie


# ---------------------------------------------------------------------------
# Claude — analisi competitive intelligence
# ---------------------------------------------------------------------------
PROMPT_COMPETITOR = """Sei un analista di competitive intelligence per un'azienda italiana.
Analizza le seguenti notizie sui competitor e fornisci intelligence actionable.

Azienda cliente: {azienda}
Area monitorata: {area}

Notizie competitor:
{notizie}

Rispondi SOLO con JSON valido:
{{
  "alert_level": "alto | medio | basso",
  "sintesi": "2-3 righe di analisi",
  "segnali_rilevanti": [
    {{
      "tipo": "nuova_offerta | cambio_prezzi | espansione | partnership | marketing_aggressivo | altro",
      "descrizione": "cosa sta succedendo",
      "impatto": "come potrebbe impattare la nostra azienda",
      "azione_consigliata": "cosa fare in risposta"
    }}
  ],
  "opportunita": "opportunità che questa situazione crea per noi (o null)",
  "priorita_risposta": "immediata | questa_settimana | monitorare"
}}"""

def analizza_competitor(azienda: str, comp: dict, notizie: list[dict]) -> dict:
    notizie_txt = "\n".join(f"- {n['titolo']}: {n['sommario']}" for n in notizie[:8])
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=1000,
        messages=[{"role": "user", "content": PROMPT_COMPETITOR.format(
            azienda=azienda,
            area=comp["nome"],
            notizie=notizie_txt,
        )}],
    )
    t = msg.content[0].text.strip()
    result = json.loads(t[t.find("{"):t.rfind("}")+1])
    result["azienda"]  = azienda
    result["area"]     = comp["nome"]
    result["data"]     = datetime.now().isoformat()
    result["notizie"]  = notizie
    return result


# ---------------------------------------------------------------------------
# Storage e deduplication
# ---------------------------------------------------------------------------
def carica_dati() -> dict:
    if Path(COMP_FILE).exists():
        with open(COMP_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": [], "ultimo_run": None}

def salva_dati(dati: dict):
    with open(COMP_FILE, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)

def hash_notizie(notizie: list[dict]) -> str:
    titoli = "".join(n.get("titolo","") for n in notizie)
    return hashlib.md5(titoli.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Shared intelligence — pubblica gli alert competitor rilevanti come insight
# ---------------------------------------------------------------------------
def _pubblica_insight(azienda: str, comp: dict, analisi: dict):
    try:
        livello = analisi.get("alert_level", "basso")
        si.aggiungi_insight("agent_competitor", {
            "azienda":             si.mappa_azienda(azienda),
            "tipo":                "competitor",
            "titolo":              f"{comp.get('nome','')}: {analisi.get('sintesi','')[:80]}",
            "sintesi":             (analisi.get("sintesi", "") or "")[:200],
            "dettaglio":           json.dumps(analisi.get("segnali_rilevanti", []), ensure_ascii=False),
            "urgenza":             "alta" if livello == "alto" else ("media" if livello == "medio" else "bassa"),
            "impatto_commerciale": "alto" if livello == "alto" else "medio",
            "azioni_suggerite":    ["campagna_email", "contatto_diretto"],
            "target_icp":          comp.get("nome", ""),
        })
    except Exception as e:
        log.warning(f"Pubblicazione insight competitor fallita: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> list[dict]:
    log.info("=== AGENTE COMPETITOR — avvio ===")
    dati    = carica_dati()
    alerts  = []

    for azienda, competitors in COMPETITOR_PROFILES.items():
        for comp in competitors:
            try:
                notizie = cerca_notizie_competitor(comp["query"])
                if not notizie:
                    continue

                # Salta se le notizie non sono cambiate dall'ultimo run
                h = hash_notizie(notizie)
                chiave = f"{azienda}_{comp['nome']}"
                if dati.get("hashes", {}).get(chiave) == h:
                    continue

                analisi = analizza_competitor(azienda, comp, notizie)
                dati.setdefault("snapshots", []).append(analisi)
                dati.setdefault("hashes", {})[chiave] = h

                if analisi.get("alert_level") in ("alto", "medio"):
                    alerts.append(analisi)
                    _pubblica_insight(azienda, comp, analisi)
                log.info(f"Competitor analizzato: {azienda} / {comp['nome']}")
            except Exception as e:
                log.warning(f"Competitor {comp['nome']}: {e}")

    # Mantieni solo ultimi 50 snapshots
    dati["snapshots"] = dati.get("snapshots", [])[-50:]
    dati["ultimo_run"] = datetime.now().isoformat()
    salva_dati(dati)

    log.info(f"=== AGENTE COMPETITOR — {len(alerts)} alert generati ===")
    return alerts


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_alert(a: dict) -> str:
    lv_emoji = {"alto":"🔴","medio":"🟡","basso":"🟢"}.get(a.get("alert_level","basso"),"•")
    az_emoji = {"Renergy Project&Build":"⚡","ACM&Partners":"🏢","RS Gas&Power":"⚡🔵"}.get(a.get("azienda",""),"•")
    righe = [
        f"{lv_emoji}{az_emoji} *COMPETITOR — {a.get('area','')}*",
        f"_{a.get('sintesi','')}_\n",
    ]
    for s in a.get("segnali_rilevanti",[])[:2]:
        righe.append(f"📌 *{s.get('tipo','').replace('_',' ').title()}*")
        righe.append(f"   {s.get('descrizione','')}")
        righe.append(f"   ➡️ {s.get('azione_consigliata','')}")

    if a.get("opportunita"):
        righe.append(f"\n💡 *Opportunità:* {a['opportunita']}")
    righe.append(f"\n⏱ Priorità: *{a.get('priorita_risposta','monitorare').replace('_',' ')}*")
    return "\n".join(righe)
