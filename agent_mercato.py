"""
agent_mercato.py — Market Intelligence Agent
Monitora notizie su energie rinnovabili, ESG, efficienza energetica.
Analizza con Claude ed estrae: trend, opportunità, target suggeriti, idee campagne.

Viene chiamato dal bot ogni martedì mattina (o su richiesta /mercato).
"""

import os, logging, json
from datetime import datetime
import feedparser
import anthropic
import shared_intelligence as si

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude = anthropic.Anthropic(api_key=CLAUDE_KEY)

# ---------------------------------------------------------------------------
# Feed RSS da monitorare (Google News + fonti specializzate)
# ---------------------------------------------------------------------------
FEED_URLS = [
    # Google News — italiano
    "https://news.google.com/rss/search?q=energia+rinnovabile+Italia+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=ESG+normativa+Italia+PMI+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=fotovoltaico+incentivi+capannoni+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=efficienza+energetica+decreto+incentivi+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=comunità+energetiche+rinnovabili+Italia&hl=it&gl=IT&ceid=IT:it",
    # English — mercati internazionali
    "https://news.google.com/rss/search?q=solar+energy+Italy+incentives+2026&hl=en&gl=IT&ceid=IT:en",
    "https://news.google.com/rss/search?q=ESG+reporting+SME+Italy+regulation+2026&hl=en&gl=IT&ceid=IT:en",
]

MAX_ARTICOLI = 5   # articoli per feed
MAX_CARATTERI = 300  # sommario per articolo


# ---------------------------------------------------------------------------
# Raccolta notizie da RSS
# ---------------------------------------------------------------------------
def raccogli_notizie() -> list[dict]:
    notizie = []
    visti = set()

    for url in FEED_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_ARTICOLI]:
                titolo = entry.get("title", "").strip()
                if titolo in visti:
                    continue
                visti.add(titolo)
                sommario = entry.get("summary", entry.get("description", ""))
                # Pulisce HTML rudimentale
                sommario = sommario.replace("<b>","").replace("</b>","").replace("<br>","").replace("&nbsp;"," ")
                sommario = sommario[:MAX_CARATTERI] + "..." if len(sommario) > MAX_CARATTERI else sommario
                notizie.append({
                    "titolo":   titolo,
                    "sommario": sommario,
                    "link":     entry.get("link", ""),
                    "data":     entry.get("published", ""),
                })
        except Exception as e:
            log.warning(f"Errore feed {url}: {e}")

    log.info(f"Raccolte {len(notizie)} notizie")
    return notizie


# ---------------------------------------------------------------------------
# Claude — analisi e intelligence
# ---------------------------------------------------------------------------
PROMPT_ANALISI = """Sei un analista di mercato specializzato in energia rinnovabile e consulenza ESG in Italia.
Hai appena letto le seguenti notizie. Analizzale e produci un briefing per un imprenditore
che opera in questi settori:
- Renergy Project&Build: installazione fotovoltaico industriale, riqualificazione energetica, EV charging
- ACM&Partners: consulenza manageriale, strategia ESG, M&A, supporto PMI in crescita

NOTIZIE:
{notizie}

Rispondi SOLO con JSON valido:
{{
  "sintesi": "3-4 righe di sintesi dei trend principali",
  "opportunita": [
    {{
      "titolo": "nome opportunità",
      "descrizione": "cosa sta succedendo e perché è un'opportunità",
      "azienda_suggerita": "Renergy | ACM&Partners | entrambe",
      "urgenza": "alta | media | bassa"
    }}
  ],
  "target_suggeriti": [
    {{
      "settore": "settore aziendale",
      "motivo": "perché questo settore è interessante adesso",
      "offerta_consigliata": "es. audit energetico gratuito / valutazione ESG",
      "azienda": "Renergy | ACM&Partners | entrambe"
    }}
  ],
  "idee_campagne": [
    {{
      "nome": "nome campagna",
      "azienda": "Renergy | ACM&Partners",
      "target": "descrizione target",
      "offerta": "hook/offerta della campagna",
      "angolo": "leva comunicativa principale (es. risparmio, obbligo normativo, incentivo)"
    }}
  ],
  "notizie_rilevanti": [
    {{
      "titolo": "titolo notizia",
      "perche_importante": "una riga",
      "azione_suggerita": "cosa fare concretamente"
    }}
  ]
}}"""

def analizza_mercato(notizie: list[dict]) -> dict:
    notizie_testo = "\n\n".join(
        f"TITOLO: {n['titolo']}\nSOMMA: {n['sommario']}"
        for n in notizie[:25]
    )
    msg = claude.messages.create(
        model="claude-opus-4-8",
        max_tokens=3000,
        messages=[{"role": "user", "content": PROMPT_ANALISI.format(notizie=notizie_testo)}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Formattazione briefing Telegram
# ---------------------------------------------------------------------------
def formatta_briefing_mercato(analisi: dict, data: str) -> str:
    urgenza_emoji = {"alta": "🔴", "media": "🟡", "bassa": "🟢"}
    azienda_emoji = {"Renergy": "⚡", "ACM&Partners": "🏢", "entrambe": "🔄"}

    righe = [f"📡 *Market Intelligence — {data}*\n"]
    righe.append(f"_{analisi.get('sintesi', '')}_\n")

    opp = analisi.get("opportunita", [])
    if opp:
        righe.append("*🎯 Opportunità:*")
        for o in opp[:3]:
            e_urg = urgenza_emoji.get(o.get("urgenza",""), "•")
            e_az  = azienda_emoji.get(o.get("azienda_suggerita",""), "•")
            righe.append(f"{e_urg} {e_az} *{o.get('titolo','')}*\n   {o.get('descrizione','')}")

    idee = analisi.get("idee_campagne", [])
    if idee:
        righe.append("\n*💡 Idee campagne:*")
        for i in idee[:3]:
            e_az = azienda_emoji.get(i.get("azienda",""), "•")
            righe.append(f"{e_az} *{i.get('nome','')}*\n"
                         f"   Target: {i.get('target','')}\n"
                         f"   Offerta: {i.get('offerta','')}")

    target = analisi.get("target_suggeriti", [])
    if target:
        righe.append("\n*🎪 Nuovi target suggeriti:*")
        for t in target[:3]:
            e_az = azienda_emoji.get(t.get("azienda",""), "•")
            righe.append(f"{e_az} {t.get('settore','')} — {t.get('offerta_consigliata','')}")

    righe.append("\n_Usa /nuova\\_campagna per creare una campagna da queste idee._")
    return "\n".join(righe)


# ---------------------------------------------------------------------------
# Shared intelligence — pubblica le opportunità di mercato come insight
# ---------------------------------------------------------------------------
def _pubblica_insight(analisi: dict):
    urgenza_map = {"alta": "alta", "media": "media", "bassa": "bassa"}
    for o in analisi.get("opportunita", [])[:3]:
        try:
            urgenza = urgenza_map.get(o.get("urgenza", "media"), "media")
            si.aggiungi_insight("agent_mercato", {
                "azienda":             si.mappa_azienda(o.get("azienda_suggerita", "")),
                "tipo":                "mercato",
                "titolo":              o.get("titolo", "")[:120],
                "sintesi":             (o.get("descrizione", "") or "")[:200],
                "dettaglio":           o.get("descrizione", ""),
                "urgenza":             urgenza,
                "impatto_commerciale": "alto" if urgenza == "alta" else "medio",
                "azioni_suggerite":    ["campagna_email", "newsletter", "contenuto"],
            })
        except Exception as e:
            log.warning(f"Pubblicazione insight mercato fallita: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> tuple[dict, str]:
    """Esegui l'agente e restituisce (analisi, testo_briefing)."""
    log.info("=== AGENTE MERCATO — avvio ===")
    notizie  = raccogli_notizie()
    if not notizie:
        log.warning("Nessuna notizia raccolta")
        return {}, "⚠️ Nessuna notizia raccolta oggi."
    analisi  = analizza_mercato(notizie)
    data     = datetime.now().strftime("%d/%m/%Y")
    briefing = formatta_briefing_mercato(analisi, data)
    _pubblica_insight(analisi)
    log.info("=== AGENTE MERCATO — completato ===")
    return analisi, briefing


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _, briefing = run()
    print(briefing)
