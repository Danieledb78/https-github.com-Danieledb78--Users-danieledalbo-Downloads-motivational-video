"""
agent_incentivi_renergy.py — Monitor Incentivi Fotovoltaico per Renergy Project&Build
Monitora fonti nazionali (GSE, MASE, Gazzetta Ufficiale, ENEA) e fonti regionali/provinciali
di Veneto, Trentino-Alto Adige e Friuli Venezia Giulia — l'area di competenza diretta di Renergy —
per intercettare bandi e contributi locali con la massima tempestività e precisione.

Finestra di opportunità: quando esce un nuovo incentivo i clienti sono massimamente ricettivi
per 2-4 settimane. Il sistema deve reagire entro 2 ore dalla pubblicazione.

Schedulato martedì e venerdì alle 07:00. Trigger manuale: /incentivi
"""

import os, json, logging, hashlib
from datetime import datetime
from pathlib import Path
import feedparser
import anthropic

import shared_intelligence as si
import config

log = logging.getLogger(__name__)
CLAUDE_KEY    = os.environ.get("CLAUDE_API_KEY")
claude        = anthropic.Anthropic(api_key=CLAUDE_KEY)
INCENTIVI_FILE = "incentivi_renergy.json"

# ---------------------------------------------------------------------------
# Fonti nazionali — GSE, MASE, Gazzetta Ufficiale, ENEA, stampa specializzata
# ---------------------------------------------------------------------------
FEED_NAZIONALI = [
    "https://www.gse.it/dati-e-scenari/rss",
    "https://news.google.com/rss/search?q=GSE+incentivi+fotovoltaico+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=MASE+ministero+ambiente+incentivi+energia+imprese&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=gazzetta+ufficiale+fotovoltaico+Transizione+5.0+conto+energia&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=ENEA+certificati+bianchi+efficienza+energetica+imprese&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=incentivi+fotovoltaico+capannoni+industriali+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=Transizione+5.0+aggiornamento+credito+imposta&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=conto+energia+GSE+nuovo+decreto&hl=it&gl=IT&ceid=IT:it",
]

# ---------------------------------------------------------------------------
# Fonti regionali e provinciali — area di competenza diretta Renergy
# Ricerche mirate per regione + tutte le rispettive province, per intercettare
# bandi camerali, regionali e dei consorzi locali (più precisi dei feed nazionali)
# ---------------------------------------------------------------------------
REGIONI_RENERGY = {
    "Veneto": {
        "province": ["Verona", "Vicenza", "Padova", "Treviso", "Venezia", "Rovigo", "Belluno"],
        "enti_riferimento": ["Regione Veneto", "Veneto Sviluppo", "Camere di Commercio Veneto"],
        "query": [
            "bandi contributi fotovoltaico imprese Regione Veneto 2026",
            "bando efficientamento energetico PMI Veneto Camera Commercio",
            "Verona OR Vicenza OR Padova OR Treviso OR Venezia OR Rovigo OR Belluno bando fotovoltaico aziende",
        ],
    },
    "Trentino-Alto Adige": {
        "province": ["Trento", "Bolzano"],
        "enti_riferimento": ["Provincia Autonoma di Trento", "Provincia Autonoma di Bolzano", "IDM Sudtirol"],
        "query": [
            "bando contributi fotovoltaico imprese Provincia Autonoma Trento",
            "bando energia rinnovabile aziende Provincia Autonoma Bolzano Alto Adige",
            "Trentino Alto Adige incentivi efficienza energetica PMI 2026",
        ],
    },
    "Friuli Venezia Giulia": {
        "province": ["Udine", "Pordenone", "Gorizia", "Trieste"],
        "enti_riferimento": ["Regione Friuli Venezia Giulia", "FVG via libera imprese"],
        "query": [
            "bandi contributi fotovoltaico imprese Regione Friuli Venezia Giulia 2026",
            "Udine OR Pordenone OR Gorizia OR Trieste bando efficientamento energetico aziende",
            "FVG via libera imprese incentivi energia rinnovabile PMI",
        ],
    },
}

def _genera_feed_regionali() -> list[dict]:
    """Costruisce dinamicamente i feed Google News per ogni regione/query di interesse Renergy."""
    feeds = []
    for regione, cfg in REGIONI_RENERGY.items():
        for query in cfg["query"]:
            url_query = query.replace(" ", "+")
            feeds.append({
                "regione": regione,
                "url": f"https://news.google.com/rss/search?q={url_query}&hl=it&gl=IT&ceid=IT:it",
            })
    return feeds

MAX_PER_FEED  = 4
MAX_CARATTERI = 280


# ---------------------------------------------------------------------------
# Raccolta — nazionale + regionale/provinciale
# ---------------------------------------------------------------------------
def raccogli_incentivi() -> dict:
    """Ritorna {"nazionali": [...], "regionali": {regione: [...]}}"""
    visti = set()

    def _parse(url: str) -> list[dict]:
        risultati = []
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:MAX_PER_FEED]:
                t = e.get("title", "").strip()
                if not t or t in visti:
                    continue
                visti.add(t)
                sommario = e.get("summary", "").replace("<b>", "").replace("</b>", "")[:MAX_CARATTERI]
                risultati.append({"titolo": t, "sommario": sommario, "link": e.get("link", ""),
                                  "data": e.get("published", "")})
        except Exception as ex:
            log.warning(f"Incentivi — errore feed {url}: {ex}")
        return risultati

    nazionali = []
    for url in FEED_NAZIONALI:
        nazionali += _parse(url)

    regionali = {regione: [] for regione in REGIONI_RENERGY}
    for f in _genera_feed_regionali():
        regionali[f["regione"]] += _parse(f["url"])

    log.info(f"Incentivi — raccolti {len(nazionali)} nazionali, "
             f"{sum(len(v) for v in regionali.values())} regionali/provinciali")
    return {"nazionali": nazionali, "regionali": regionali}


# ---------------------------------------------------------------------------
# Claude — analisi impatto e priorità
# ---------------------------------------------------------------------------
PROMPT_INCENTIVI = """Sei un esperto di incentivi fotovoltaico e efficienza energetica per imprese italiane,
con focus sull'area Nord-Est (Veneto, Trentino-Alto Adige, Friuli Venezia Giulia) — il territorio
di competenza diretta di Renergy Project&Build (fotovoltaico industriale).

NOTIZIE NAZIONALI (GSE, MASE, Gazzetta Ufficiale, ENEA, stampa):
{notizie_nazionali}

NOTIZIE REGIONALI/PROVINCIALI (bandi locali Veneto, Trentino-Alto Adige, FVG e relative province):
{notizie_regionali}

Identifica SOLO incentivi/bandi NUOVI o con scadenze imminenti, rilevanti per imprese che
vogliono installare un impianto fotovoltaico industriale o efficientare i consumi energetici.
Dai priorità a bandi regionali/provinciali specifici: sono più precisi e meno conosciuti dai
competitor nazionali, quindi rappresentano un vantaggio competitivo per Renergy.

Rispondi SOLO con JSON valido:
{{
  "nuovo_incentivo_rilevato": true,
  "incentivi_top": [
    {{
      "titolo": "nome incentivo/bando",
      "livello": "nazionale|regionale|provinciale",
      "area_geografica": "es. Regione Veneto / Provincia di Vicenza / Italia",
      "descrizione": "cosa offre, beneficiari, importo/percentuale contributo",
      "scadenza": "data o finestra temporale se nota",
      "impatto_clienti_renergy": "perché interessa ai clienti Renergy e quanto",
      "azione_consigliata": "cosa fare concretamente entro 2 ore"
    }}
  ],
  "urgenza_globale": "alta|media|bassa",
  "sintesi": "3-4 righe sul quadro complessivo incentivi rilevati"
}}"""

def analizza_incentivi(raccolta: dict) -> dict:
    naz_txt = "\n".join(f"- {n['titolo']}: {n['sommario']}" for n in raccolta["nazionali"][:15])
    reg_txt = ""
    for regione, notizie in raccolta["regionali"].items():
        if notizie:
            reg_txt += f"\n[{regione}]\n" + "\n".join(f"- {n['titolo']}: {n['sommario']}" for n in notizie[:6])

    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=2000, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_INCENTIVI.format(
            notizie_nazionali=naz_txt or "(nessuna)",
            notizie_regionali=reg_txt or "(nessuna)",
        )}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Bozza email per clienti esistenti Renergy (senza impianto o impianto parziale)
# ---------------------------------------------------------------------------
PROMPT_EMAIL_INCENTIVO = """Scrivi una bozza email breve (120-150 parole) per clienti Renergy Project&Build
che non hanno ancora un impianto fotovoltaico (o lo hanno solo parziale), per informarli
di questo nuovo incentivo:

{incentivo_json}

Tono: tecnico-fiduciario, concreto, orientato al ROI. Numeri reali quando possibile.
Niente promesse vaghe. Urgency basata sulla scadenza reale dell'incentivo.

Rispondi SOLO con JSON: {{"oggetto": "...", "corpo": "testo email pronto, con [Nome] come placeholder"}}"""

def genera_bozza_email_incentivo(incentivo: dict) -> dict:
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=700, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_EMAIL_INCENTIVO.format(
            incentivo_json=json.dumps(incentivo, ensure_ascii=False))}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def carica_storico() -> dict:
    if Path(INCENTIVI_FILE).exists():
        with open(INCENTIVI_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"rilevazioni": [], "ultimo_hash": ""}

def salva_storico(dati: dict):
    with open(INCENTIVI_FILE, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)

def _hash(raccolta: dict) -> str:
    titoli = "".join(n["titolo"] for n in raccolta["nazionali"])
    titoli += "".join(n["titolo"] for lst in raccolta["regionali"].values() for n in lst)
    return hashlib.md5(titoli.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Shared intelligence
# ---------------------------------------------------------------------------
def _pubblica_insight(analisi: dict, incentivo: dict) -> dict | None:
    return si.aggiungi_insight("agent_incentivi_renergy", {
        "azienda":             "Renergy",
        "tipo":                "incentivo",
        "titolo":              incentivo.get("titolo", "")[:120],
        "sintesi":             incentivo.get("descrizione", "")[:200],
        "dettaglio":           json.dumps(incentivo, ensure_ascii=False),
        "urgenza":             "alta",
        "impatto_commerciale": "alto",
        "servizi_collegati":   ["Renergy_FV_industriale", "Renergy_audit_energetico"],
        "target_icp":          incentivo.get("impatto_clienti_renergy", ""),
        "finestra_temporale":  incentivo.get("scadenza", "2-4 settimane"),
        "azioni_suggerite":    ["email_clienti_esistenti", "campagna_nuovi_lead", "landing_page", "post_linkedin"],
    })


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
LIVELLO_EMOJI = {"nazionale": "🇮🇹", "regionale": "📍", "provinciale": "🏛"}

def formatta_alert_incentivo(analisi: dict) -> str:
    righe = [
        f"🆕 *NUOVO INCENTIVO RILEVATO — Renergy Project&Build*",
        f"⏱ Finestra di opportunità: agire entro 2 ore\n",
        f"_{analisi.get('sintesi','')}_\n",
    ]
    for inc in analisi.get("incentivi_top", [])[:3]:
        e = LIVELLO_EMOJI.get(inc.get("livello", ""), "•")
        righe.append(f"{e} *{inc.get('titolo','')}* — {inc.get('area_geografica','')}")
        righe.append(f"   {inc.get('descrizione','')}")
        righe.append(f"   📅 Scadenza: {inc.get('scadenza','n.d.')}")
        righe.append(f"   ➡️ {inc.get('azione_consigliata','')}\n")
    righe.append("_Usa /incentivi per rilanciare l'analisi o approvare le bozze email clienti._")
    return "\n".join(righe)

def formatta_nessun_aggiornamento() -> str:
    return "📅 *Monitor Incentivi Renergy* — nessun nuovo bando rilevato (Italia + Veneto/Trentino-Alto Adige/FVG e relative province)."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> tuple[dict | None, str, dict | None]:
    """Ritorna (analisi, messaggio_telegram, bozza_email_o_None)."""
    log.info("=== AGENTE INCENTIVI RENERGY — avvio ===")
    raccolta = raccogli_incentivi()
    if not raccolta["nazionali"] and not any(raccolta["regionali"].values()):
        return None, "⚠️ Nessuna notizia incentivi raccolta.", None

    storico = carica_storico()
    h = _hash(raccolta)
    if storico.get("ultimo_hash") == h:
        log.info("Incentivi invariati — nessun update")
        return None, "", None

    try:
        analisi = analizza_incentivi(raccolta)
    except Exception as e:
        log.warning(f"Incentivi — analisi Claude fallita: {e}")
        return None, "⚠️ Errore nell'analisi incentivi.", None

    storico.setdefault("rilevazioni", []).append({"data": datetime.now().isoformat(), "analisi": analisi})
    storico["rilevazioni"] = storico["rilevazioni"][-30:]
    storico["ultimo_hash"] = h
    salva_storico(storico)

    bozza_email = None
    if analisi.get("nuovo_incentivo_rilevato") and analisi.get("incentivi_top"):
        top = analisi["incentivi_top"][0]
        _pubblica_insight(analisi, top)
        try:
            bozza_email = genera_bozza_email_incentivo(top)
            bozza_email["incentivo"] = top.get("titolo", "")
        except Exception as e:
            log.warning(f"Incentivi — bozza email fallita: {e}")
        messaggio = formatta_alert_incentivo(analisi)
    else:
        messaggio = formatta_nessun_aggiornamento()

    log.info("=== AGENTE INCENTIVI RENERGY — completato ===")
    return analisi, messaggio, bozza_email
