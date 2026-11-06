"""
agent_tariffe_rs.py — Monitor Tariffe Competitor per RS Gas&Power
Rileva periodicamente le offerte luce/gas pubblicate dai principali competitor/broker
del Nord-Est e valuta con Claude se le tariffe RS Gas&Power restano competitive.

Schedulato lunedì e giovedì alle 07:45. Trigger manuale: /tariffe
Se l'analisi rileva un gap critico, invia un alert immediato (non aspetta l'orchestratore).
"""

import os, re, json, time, logging, hashlib
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import anthropic

import shared_intelligence as si
import config

log = logging.getLogger(__name__)
CLAUDE_KEY  = os.environ.get("CLAUDE_API_KEY")
claude      = anthropic.Anthropic(api_key=CLAUDE_KEY)
TARIFFE_FILE = "tariffe_rs.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------------------
# Fonti da monitorare (scraping pubblico, nessuna autenticazione)
# ---------------------------------------------------------------------------
FONTI_TARIFFE = [
    {"nome": "Il Portale delle Offerte (ARERA)", "url": "https://www.ilportaleofferte.it/portaleOfferte/"},
    {"nome": "S4Nergia",        "url": "https://www.s4nergia.it/offerte-luce-gas-business/"},
    {"nome": "Dolomiti Energia","url": "https://www.dolomitienergia.it/offerte-business"},
    {"nome": "Alperia",         "url": "https://www.alperia.eu/it/offerte-business"},
]

# Pattern per individuare prezzi €/kWh e €/Smc nel testo della pagina
RE_PREZZO_KWH = re.compile(r"(\d+[.,]\d+)\s*€?\s*/?\s*k?Wh", re.IGNORECASE)
RE_PREZZO_SMC = re.compile(r"(\d+[.,]\d+)\s*€?\s*/?\s*Smc", re.IGNORECASE)

DELAY_TRA_RICHIESTE_SEC = 2.5


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
def _competitor_dinamici() -> list[dict]:
    """Aggiunge alla lista fonti i broker identificati dinamicamente da agent_competitor."""
    extra = []
    try:
        if Path("competitor_data.json").exists():
            with open("competitor_data.json", encoding="utf-8") as f:
                dati = json.load(f)
            for snap in dati.get("snapshots", [])[-20:]:
                if "RS Gas" in snap.get("azienda", "") or "energia" in snap.get("area", "").lower():
                    for s in snap.get("segnali_rilevanti", []):
                        if s.get("tipo") == "nuova_offerta":
                            extra.append({"nome": f"[dinamico] {snap.get('area','')}", "url": None,
                                          "nota": s.get("descrizione", "")})
    except Exception as e:
        log.warning(f"Lettura competitor dinamici fallita: {e}")
    return extra


def rileva_tariffe() -> list[dict]:
    """Visita le fonti pubbliche e estrae frammenti di testo con eventuali prezzi rilevati."""
    rilevazioni = []
    headers = {"User-Agent": USER_AGENT}

    for fonte in FONTI_TARIFFE:
        try:
            resp = requests.get(fonte["url"], headers=headers, timeout=20)
            resp.raise_for_status()
            soup  = BeautifulSoup(resp.text, "html.parser")
            testo = soup.get_text(" ", strip=True)[:6000]

            prezzi_luce = RE_PREZZO_KWH.findall(testo)
            prezzi_gas  = RE_PREZZO_SMC.findall(testo)

            rilevazioni.append({
                "fonte":          fonte["nome"],
                "url":            fonte["url"],
                "prezzi_luce_kwh": prezzi_luce[:5],
                "prezzi_gas_smc":  prezzi_gas[:5],
                "estratto":       testo[:1200],
                "rilevato_il":    datetime.now().isoformat(),
            })
            log.info(f"Tariffe — rilevati dati da {fonte['nome']} "
                     f"({len(prezzi_luce)} prezzi luce, {len(prezzi_gas)} prezzi gas)")
        except Exception as e:
            log.warning(f"Tariffe — errore su {fonte['nome']}: {e}")
        time.sleep(DELAY_TRA_RICHIESTE_SEC)

    rilevazioni += [{"fonte": e["nome"], "url": None, "nota": e.get("nota", ""),
                     "rilevato_il": datetime.now().isoformat()} for e in _competitor_dinamici()]
    return rilevazioni


# ---------------------------------------------------------------------------
# Claude — analisi competitività RS Gas&Power
# ---------------------------------------------------------------------------
PROMPT_TARIFFE = """Analizza queste tariffe energia rilevate oggi {data}:
{tariffe_json}

Profili clienti target RS Gas&Power:
- PMI manifatturiero Veneto/FVG/Trentino, 50-500 kW/mese luce, 1000-5000 Smc/anno gas
- Potere decisionale: titolare o CFO
- Principale driver acquisto: risparmio immediato + semplicità contratto

Rispondi SOLO in JSON:
{{
  "rs_gas_competitiva": true,
  "profili_dove_conviene": ["profilo1", "profilo2"],
  "gap_rilevato": "descrizione se RS Gas non è competitiva, altrimenti stringa vuota",
  "tariffa_raccomandata": {{
    "descrizione": "...",
    "prezzo_luce_suggerito": "€/kWh",
    "prezzo_gas_suggerito": "€/Smc",
    "margine_stimato": "...",
    "profilo_target": "..."
  }},
  "urgenza": "alta",
  "messaggio_a_daniele": "cosa fare concretamente questa settimana"
}}"""

def analizza_tariffe(rilevazioni: list[dict]) -> dict:
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=1200, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_TARIFFE.format(
            data=datetime.now().strftime("%d/%m/%Y"),
            tariffe_json=json.dumps(rilevazioni, ensure_ascii=False)[:8000],
        )}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def carica_storico() -> dict:
    if Path(TARIFFE_FILE).exists():
        with open(TARIFFE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"rilevazioni": [], "ultimo_hash": ""}

def salva_storico(dati: dict):
    with open(TARIFFE_FILE, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)

def _hash(rilevazioni: list[dict]) -> str:
    base = "".join(r.get("fonte", "") + str(r.get("prezzi_luce_kwh", "")) + str(r.get("prezzi_gas_smc", ""))
                   for r in rilevazioni)
    return hashlib.md5(base.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Shared intelligence + alert immediato
# ---------------------------------------------------------------------------
def _pubblica_insight(analisi: dict) -> dict | None:
    urgenza = analisi.get("urgenza", "media")
    return si.aggiungi_insight("agent_tariffe_rs", {
        "azienda":             "RS_Gas",
        "tipo":                "tariffa",
        "titolo":              ("Gap tariffario rilevato" if not analisi.get("rs_gas_competitiva", True)
                                 else "Posizionamento tariffario verificato"),
        "sintesi":             (analisi.get("gap_rilevato") or analisi.get("messaggio_a_daniele", ""))[:200],
        "dettaglio":           json.dumps(analisi, ensure_ascii=False),
        "urgenza":             urgenza,
        "impatto_commerciale": "alto" if urgenza == "alta" else "medio",
        "servizi_collegati":   ["RS_Gas_offerta_luce", "RS_Gas_offerta_gas"],
        "target_icp":          analisi.get("tariffa_raccomandata", {}).get("profilo_target", ""),
        "azioni_suggerite":    ["campagna_email", "contatto_diretto"],
    })


def formatta_alert_gap(analisi: dict) -> str:
    return (
        "🚨 *ALERT TARIFFE — RS Gas&Power non competitiva*\n"
        f"⚠️ {analisi.get('gap_rilevato','')}\n\n"
        f"💡 *Tariffa raccomandata:*\n"
        f"   {analisi.get('tariffa_raccomandata',{}).get('descrizione','')}\n"
        f"   Luce: {analisi.get('tariffa_raccomandata',{}).get('prezzo_luce_suggerito','—')} | "
        f"Gas: {analisi.get('tariffa_raccomandata',{}).get('prezzo_gas_suggerito','—')}\n\n"
        f"📋 *Da fare questa settimana:*\n{analisi.get('messaggio_a_daniele','')}"
    )


def formatta_report(analisi: dict, rilevazioni: list[dict]) -> str:
    emoji = "🟢" if analisi.get("rs_gas_competitiva") else "🔴"
    righe = [
        f"⚡🔵 *Monitor Tariffe RS Gas&Power — {datetime.now().strftime('%d/%m/%Y')}*",
        f"{emoji} Competitiva: *{'Sì' if analisi.get('rs_gas_competitiva') else 'No'}*",
        f"🔍 Fonti monitorate: {len(rilevazioni)}",
    ]
    if analisi.get("profili_dove_conviene"):
        righe.append(f"\n✅ *Dove conviene:* {', '.join(analisi['profili_dove_conviene'])}")
    if analisi.get("gap_rilevato"):
        righe.append(f"\n⚠️ *Gap:* {analisi['gap_rilevato']}")
    racc = analisi.get("tariffa_raccomandata", {})
    if racc:
        righe.append(f"\n💡 *Raccomandazione:* {racc.get('descrizione','')}\n"
                     f"   Luce: {racc.get('prezzo_luce_suggerito','—')} | Gas: {racc.get('prezzo_gas_suggerito','—')} | "
                     f"Margine: {racc.get('margine_stimato','—')}")
    righe.append(f"\n📋 {analisi.get('messaggio_a_daniele','')}")
    righe.append("\n_Usa /tariffe per rilanciare l'analisi._")
    return "\n".join(righe)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> tuple[dict | None, str, bool]:
    """Ritorna (analisi, messaggio_telegram, is_alert_immediato)."""
    log.info("=== AGENTE TARIFFE RS — avvio ===")
    rilevazioni = rileva_tariffe()
    if not rilevazioni:
        return None, "⚠️ Nessuna tariffa rilevata oggi.", False

    storico = carica_storico()
    h = _hash(rilevazioni)
    if storico.get("ultimo_hash") == h:
        log.info("Tariffe invariate — nessun update")
        return None, "", False

    try:
        analisi = analizza_tariffe(rilevazioni)
    except Exception as e:
        log.warning(f"Tariffe — analisi Claude fallita: {e}")
        return None, "⚠️ Errore nell'analisi tariffe.", False

    storico.setdefault("rilevazioni", []).append({"data": datetime.now().isoformat(),
                                                   "analisi": analisi, "fonti": len(rilevazioni)})
    storico["rilevazioni"] = storico["rilevazioni"][-30:]
    storico["ultimo_hash"] = h
    salva_storico(storico)

    _pubblica_insight(analisi)

    alert_immediato = (analisi.get("urgenza") == "alta" and not analisi.get("rs_gas_competitiva", True))
    messaggio = formatta_alert_gap(analisi) if alert_immediato else formatta_report(analisi, rilevazioni)
    log.info(f"=== AGENTE TARIFFE RS — completato (alert={alert_immediato}) ===")
    return analisi, messaggio, alert_immediato
