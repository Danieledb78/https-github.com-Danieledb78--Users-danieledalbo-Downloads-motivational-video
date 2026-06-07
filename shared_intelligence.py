"""
shared_intelligence.py — Memoria condivisa centrale (AIOS v2.0)
Ogni agente informativo scrive qui i propri insight strutturati.
agent_orchestratore legge SOLO da questo file per proporre azioni commerciali.

Schema di un insight:
{
  "id": "esg_20260607_090000",
  "fonte": "agent_esg_monitor",
  "azienda": "ACM",                  # Renergy | RS_Gas | ACM | Tutte
  "data_generazione": "2026-06-07T09:00:00",
  "tipo": "normativa",               # normativa|mercato|competitor|incentivo|tariffa|trend
  "titolo": "...",
  "sintesi": "...",
  "dettaglio": "...",
  "urgenza": "alta",                 # alta|media|bassa
  "impatto_commerciale": "alto",     # alto|medio|basso
  "servizi_collegati": [...],
  "target_icp": "...",
  "finestra_temporale": "3 mesi",
  "azioni_suggerite": [...],
  "usato_campagna": false,
  "usato_newsletter": false,
  "usato_landing": false,
  "usato_contatto_diretto": false,
  "risultati": {}
}
"""

import os, json, logging
from datetime import datetime
from pathlib import Path

try:
    from filelock import FileLock
except ImportError:
    FileLock = None

import config

log = logging.getLogger(__name__)

FILE_PATH  = "shared_intelligence.json"
LOCK_PATH  = os.path.join(config.FILELOCK_PATH, "shared_intelligence.lock")

AZIENDE_VALIDE  = ("Renergy", "RS_Gas", "ACM", "Tutte")
TIPI_VALIDI     = ("normativa", "mercato", "competitor", "incentivo", "tariffa", "trend")
LIVELLI_VALIDI  = ("alta", "media", "bassa")
IMPATTI_VALIDI  = ("alto", "medio", "basso")
FLAG_USO        = ("usato_campagna", "usato_newsletter", "usato_landing", "usato_contatto_diretto")

Path(config.FILELOCK_PATH).mkdir(parents=True, exist_ok=True)


def _lock():
    if FileLock:
        return FileLock(LOCK_PATH, timeout=10)
    # Fallback no-op se filelock non è installato
    class _NoLock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _NoLock()


# ---------------------------------------------------------------------------
# Caricamento / salvataggio
# ---------------------------------------------------------------------------
def _struttura_vuota() -> dict:
    return {"insights": [], "last_updated": "", "version": "2.0"}

def carica() -> dict:
    with _lock():
        if Path(FILE_PATH).exists():
            try:
                with open(FILE_PATH, encoding="utf-8") as f:
                    dati = json.load(f)
                dati.setdefault("insights", [])
                dati.setdefault("version", "2.0")
                return dati
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"shared_intelligence corrotto, ricreo: {e}")
        return _struttura_vuota()

def _salva(dati: dict):
    dati["last_updated"] = datetime.now().isoformat()
    with _lock():
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Validazione schema minimo prima della scrittura
# ---------------------------------------------------------------------------
def _valida(insight: dict) -> bool:
    campi_obbligatori = ("fonte", "azienda", "tipo", "titolo", "sintesi",
                         "urgenza", "impatto_commerciale")
    for campo in campi_obbligatori:
        if campo not in insight or not insight[campo]:
            log.warning(f"Insight scartato — campo mancante: {campo}")
            return False
    if insight["azienda"] not in AZIENDE_VALIDE:
        log.warning(f"Insight scartato — azienda non valida: {insight['azienda']}")
        return False
    if insight["tipo"] not in TIPI_VALIDI:
        log.warning(f"Insight scartato — tipo non valido: {insight['tipo']}")
        return False
    if insight["urgenza"] not in LIVELLI_VALIDI:
        log.warning(f"Insight scartato — urgenza non valida: {insight['urgenza']}")
        return False
    if insight["impatto_commerciale"] not in IMPATTI_VALIDI:
        log.warning(f"Insight scartato — impatto_commerciale non valido: {insight['impatto_commerciale']}")
        return False
    return True


_MAPPA_AZIENDE = {
    "renergy": "Renergy", "renergy project&build": "Renergy",
    "acm": "ACM", "acm&partners": "ACM",
    "rs gas&power": "RS_Gas", "rs_gas": "RS_Gas", "rsgas": "RS_Gas",
    "entrambe": "Tutte", "tutte": "Tutte", "combinata": "Tutte",
}

def mappa_azienda(nome: str) -> str:
    """Normalizza una stringa azienda eterogenea verso lo schema Renergy|RS_Gas|ACM|Tutte."""
    return _MAPPA_AZIENDE.get((nome or "").strip().lower(), "Tutte")


# ---------------------------------------------------------------------------
# Scrittura insight — chiamata da ogni agente informativo a fine run()
# ---------------------------------------------------------------------------
def aggiungi_insight(fonte: str, insight_data: dict) -> dict | None:
    """Salva un insight in shared_intelligence.json. Ritorna l'insight salvato o None se invalido."""
    insight = dict(insight_data)
    insight.setdefault("fonte", fonte)
    insight.setdefault("data_generazione", datetime.now().isoformat())
    insight.setdefault("dettaglio", insight.get("sintesi", ""))
    insight.setdefault("servizi_collegati", [])
    insight.setdefault("target_icp", "")
    insight.setdefault("finestra_temporale", "")
    insight.setdefault("azioni_suggerite", [])

    if not _valida(insight):
        return None

    insight["id"] = f"{fonte}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    for flag in FLAG_USO:
        insight[flag] = False
    insight["risultati"] = {}

    dati = carica()
    dati["insights"].append(insight)
    dati["insights"] = dati["insights"][-300:]   # mantieni storico limitato
    _salva(dati)
    log.info(f"Insight salvato in shared_intelligence: {insight['id']} ({insight['titolo'][:60]})")
    return insight


# ---------------------------------------------------------------------------
# Lettura per l'orchestratore
# ---------------------------------------------------------------------------
def get_insight(insight_id: str) -> dict | None:
    return next((i for i in carica().get("insights", []) if i["id"] == insight_id), None)

def get_insight_da_sfruttare(giorni_recenti: int = 7) -> list[dict]:
    """Insight con impatto alto/medio e almeno un flag usato_* = False, generati di recente."""
    soglia = datetime.now().timestamp() - giorni_recenti * 86400
    risultato = []
    for i in carica().get("insights", []):
        if i.get("impatto_commerciale") not in ("alto", "medio"):
            continue
        if not any(not i.get(flag, False) for flag in FLAG_USO):
            continue
        try:
            ts = datetime.fromisoformat(i.get("data_generazione", "")).timestamp()
        except ValueError:
            ts = 0
        if ts < soglia:
            continue
        risultato.append(i)
    return risultato

def segna_usato(insight_id: str, azione: str, risultato: dict | None = None):
    """azione in: campagna | newsletter | landing | contatto_diretto"""
    flag = f"usato_{azione}"
    if flag not in FLAG_USO:
        log.warning(f"Flag azione sconosciuto: {flag}")
        return
    dati = carica()
    for i in dati.get("insights", []):
        if i["id"] == insight_id:
            i[flag] = True
            if risultato:
                i.setdefault("risultati", {})
                i["risultati"][azione] = risultato
    _salva(dati)
