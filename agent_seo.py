"""
agent_seo.py — Agente SEO Monitor
Monitora keyword di settore, trend di ricerca e opportunità di posizionamento
per renergygroup.it e acmpartners.it.
Suggerisce contenuti da creare per migliorare il ranking organico.
"""

import os, json, logging, feedparser
from datetime import datetime
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude = anthropic.Anthropic(api_key=CLAUDE_KEY)
SEO_FILE = "seo_report.json"

KEYWORD_PROFILES = {
    "Renergy Project&Build": {
        "sito": "renergygroup.it",
        "keyword_core": [
            "fotovoltaico industriale Veneto",
            "impianto solare capannone",
            "riqualificazione energetica industria",
            "EV charging aziendale",
            "energy audit PMI",
            "comunità energetiche imprese",
        ],
        "feed_urls": [
            "https://news.google.com/rss/search?q=fotovoltaico+industriale+2026&hl=it&gl=IT&ceid=IT:it",
            "https://news.google.com/rss/search?q=incentivi+energia+rinnovabile+imprese+2026&hl=it&gl=IT&ceid=IT:it",
            "https://news.google.com/rss/search?q=solarizzazione+capannoni+Veneto&hl=it&gl=IT&ceid=IT:it",
        ],
    },
    "ACM&Partners": {
        "sito": "acmpartners.it",
        "keyword_core": [
            "consulenza ESG PMI Italia",
            "strategia sostenibilità impresa",
            "CSRD rendicontazione PMI",
            "M&A consulenza aziendale",
            "crescita PMI nord Italia",
            "rating ESG azienda",
        ],
        "feed_urls": [
            "https://news.google.com/rss/search?q=CSRD+PMI+obbligo+2026&hl=it&gl=IT&ceid=IT:it",
            "https://news.google.com/rss/search?q=consulenza+ESG+Italia+2026&hl=it&gl=IT&ceid=IT:it",
            "https://news.google.com/rss/search?q=sostenibilita+PMI+vantaggi&hl=it&gl=IT&ceid=IT:it",
        ],
    },
}


# ---------------------------------------------------------------------------
# Raccolta trend da RSS
# ---------------------------------------------------------------------------
def analizza_trend_keyword(azienda: str) -> list[dict]:
    profilo = KEYWORD_PROFILES.get(azienda, {})
    notizie = []
    visti   = set()
    for url in profilo.get("feed_urls", []):
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:5]:
                t = e.get("title","").strip()
                if t and t not in visti:
                    visti.add(t)
                    notizie.append({"titolo": t, "sommario": e.get("summary","")[:200]})
        except Exception as ex:
            log.warning(f"Feed SEO: {ex}")
    return notizie


# ---------------------------------------------------------------------------
# Claude — analisi SEO e suggerimenti contenuti
# ---------------------------------------------------------------------------
PROMPT_SEO = """Sei un esperto SEO italiano specializzato in B2B.
Analizza i trend di ricerca per l'azienda e suggerisci contenuti da creare.

Azienda: {azienda}
Sito web: {sito}
Keyword core del business:
{keywords}

Notizie/trend attuali del settore:
{notizie}

Rispondi SOLO con JSON valido:
{{
  "opportunita_keyword": [
    {{
      "keyword": "...",
      "intento": "informazionale | commerciale | transazionale",
      "difficolta": "bassa | media | alta",
      "idea_contenuto": "tipo di pagina o articolo da creare"
    }}
  ],
  "contenuti_suggeriti": [
    {{
      "tipo": "articolo_blog | landing_page | FAQ | caso_studio",
      "titolo": "titolo SEO ottimizzato",
      "keyword_target": "...",
      "descrizione": "di cosa parlare in 2 righe",
      "urgenza": "alta | media | bassa"
    }}
  ],
  "action_items": [
    "azione concreta 1 (es. crea pagina per keyword X)",
    "azione concreta 2"
  ],
  "sintesi": "2 righe di analisi generale"
}}"""

def genera_report_seo(azienda: str) -> dict:
    profilo  = KEYWORD_PROFILES.get(azienda, {})
    notizie  = analizza_trend_keyword(azienda)
    keywords = "\n".join(f"- {k}" for k in profilo.get("keyword_core",[]))
    notizie_txt = "\n".join(f"- {n['titolo']}" for n in notizie[:10])

    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT_SEO.format(
            azienda=azienda,
            sito=profilo.get("sito",""),
            keywords=keywords,
            notizie=notizie_txt,
        )}],
    )
    t = msg.content[0].text.strip()
    report = json.loads(t[t.find("{"):t.rfind("}")+1])
    report["azienda"] = azienda
    report["data"]    = datetime.now().isoformat()
    return report


# ---------------------------------------------------------------------------
# Storage e entry point
# ---------------------------------------------------------------------------
def salva_report(reports: list[dict]):
    existing = []
    if Path(SEO_FILE).exists():
        with open(SEO_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    existing.extend(reports)
    with open(SEO_FILE, "w", encoding="utf-8") as f:
        json.dump(existing[-10:], f, ensure_ascii=False, indent=2)  # ultimi 10


def run() -> list[dict]:
    log.info("=== AGENTE SEO — avvio ===")
    reports = []
    for azienda in KEYWORD_PROFILES:
        try:
            r = genera_report_seo(azienda)
            reports.append(r)
            log.info(f"SEO report generato per {azienda}")
        except Exception as e:
            log.warning(f"SEO {azienda}: {e}")
    salva_report(reports)
    log.info("=== AGENTE SEO — completato ===")
    return reports


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_report_seo(report: dict) -> str:
    az_emoji = {"Renergy Project&Build":"⚡","ACM&Partners":"🏢"}.get(report.get("azienda",""),"🔍")
    righe = [f"{az_emoji} *SEO Report — {report.get('azienda','')}*",
             f"_{report.get('sintesi','')}_\n"]

    opp = report.get("opportunita_keyword",[])[:3]
    if opp:
        righe.append("*🔑 Keyword Opportunità:*")
        for o in opp:
            d_emoji = {"bassa":"🟢","media":"🟡","alta":"🔴"}.get(o.get("difficolta",""),"•")
            righe.append(f"{d_emoji} `{o['keyword']}` — {o.get('idea_contenuto','')}")

    sug = report.get("contenuti_suggeriti",[])[:3]
    if sug:
        righe.append("\n*📝 Contenuti da creare:*")
        for s in sug:
            u_emoji = {"alta":"🔴","media":"🟡","bassa":"🟢"}.get(s.get("urgenza",""),"•")
            righe.append(f"{u_emoji} *{s.get('titolo','')}*\n   → {s.get('descrizione','')}")

    actions = report.get("action_items",[])[:3]
    if actions:
        righe.append("\n*✅ Action items:*")
        for a in actions:
            righe.append(f"• {a}")

    return "\n".join(righe)
