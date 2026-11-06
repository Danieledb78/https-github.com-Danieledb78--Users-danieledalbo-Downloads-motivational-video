"""
webapp.py — AIOS v2.0 Web Application
FastAPI backend + serve static frontend.
Avvia con: uvicorn webapp:app --host 0.0.0.0 --port 5000
"""
import os, sys, logging, threading, traceback
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import shared_intelligence as si
import hub_client
import config

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AIOS v2.0", version="2.0")

# Crea static/ se non esiste
Path("static").mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------------------
# In-memory run log (ultimi 50 run)
# ---------------------------------------------------------------------------
_run_log: list[dict] = []
_run_lock = threading.Lock()

def _log_run(agente: str, esito: str, dettaglio: str = ""):
    with _run_lock:
        _run_log.insert(0, {
            "agente": agente,
            "esito": esito,
            "dettaglio": dettaglio,
            "ts": datetime.now().strftime("%d/%m %H:%M"),
        })
        del _run_log[50:]

def _run_in_thread(nome: str, fn):
    """Esegue fn() in un thread separato, registra il risultato nel log."""
    def _wrapper():
        try:
            risultato = fn()
            _log_run(nome, "ok", str(risultato)[:200] if risultato else "completato")
        except Exception as e:
            _log_run(nome, "error", str(e)[:200])
    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()

# ---------------------------------------------------------------------------
# Lazy import agenti (evita crash se dipendenze mancanti)
# ---------------------------------------------------------------------------
def _import_agente(nome_modulo: str):
    try:
        return __import__(nome_modulo)
    except Exception as e:
        log.warning(f"Import {nome_modulo} fallito: {e}")
        return None

# ---------------------------------------------------------------------------
# Root — serve il frontend
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    p = Path("static/index.html")
    if not p.exists():
        return HTMLResponse("<h1>AIOS Web App</h1><p>static/index.html non trovato.</p>")
    return HTMLResponse(p.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# API — KPI
# ---------------------------------------------------------------------------
@app.get("/api/kpi")
def api_kpi():
    try:
        dati = si.carica()
        insights = dati.get("insights", [])
        per_az = {}
        for az in ("Renergy", "RS_Gas", "ACM"):
            per_az[az] = sum(1 for i in insights if i.get("azienda") == az)
        urgenti = sum(1 for i in insights if i.get("urgenza") == "alta")
        leads = hub_client.get_lead(n=200)
        return {
            "insights_attivi": len(insights),
            "urgenti": urgenti,
            "lead_totali": len(leads),
            "per_azienda": per_az,
            "last_updated": dati.get("last_updated", ""),
        }
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# API — Lead
# ---------------------------------------------------------------------------
@app.get("/api/leads")
def api_leads(n: int = 30, azienda: str = None):
    try:
        filtri = {}
        if azienda:
            filtri["azienda"] = azienda
        leads = hub_client.get_lead(filtri=filtri or None, n=n)
        return {"leads": leads, "totale": len(leads)}
    except Exception as e:
        return {"leads": [], "totale": 0, "error": str(e)}

# ---------------------------------------------------------------------------
# API — Intelligence
# ---------------------------------------------------------------------------
@app.get("/api/intelligence")
def api_intelligence(azienda: str = None, urgenza: str = None, limit: int = 40):
    try:
        dati = si.carica()
        insights = dati.get("insights", [])
        if azienda:
            insights = [i for i in insights if i.get("azienda") == azienda]
        if urgenza:
            insights = [i for i in insights if i.get("urgenza") == urgenza]
        insights = sorted(insights, key=lambda x: x.get("data_generazione", ""), reverse=True)[:limit]
        return {"insights": insights, "totale": len(insights)}
    except Exception as e:
        return {"insights": [], "totale": 0, "error": str(e)}

# ---------------------------------------------------------------------------
# API — Task
# ---------------------------------------------------------------------------
@app.get("/api/tasks")
def api_tasks():
    try:
        from agent_task_tracker import TaskTracker
        att = TaskTracker()
        tasks = att.get_tasks_aperti()
        return {"tasks": tasks, "totale": len(tasks)}
    except Exception as e:
        return {"tasks": [], "totale": 0, "error": str(e)}

# ---------------------------------------------------------------------------
# API — Run Log
# ---------------------------------------------------------------------------
@app.get("/api/run-log")
def api_run_log():
    with _run_lock:
        return {"log": list(_run_log)}

# ---------------------------------------------------------------------------
# API — Avvia agente
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    parametri: dict = {}

@app.post("/api/agents/{nome}/run")
def api_run_agent(nome: str, background_tasks: BackgroundTasks):
    _AGENTI = {
        "orchestratore":    ("agent_orchestratore",    "run"),
        "tariffe":          ("agent_tariffe_rs",        "run"),
        "incentivi":        ("agent_incentivi_renergy", "run"),
        "crosssell":        ("agent_crosssell",         "run"),
        "prospect_rsgas":   ("agent_prospect_rsgas",    "run"),
        "clienti":          ("agent_clienti",           "run"),
        "report":           ("agent_report",            "run"),
        "competitor":       ("agent_competitor",        "run"),
        "esg":              ("agent_esg_monitor",       "run"),
    }
    if nome not in _AGENTI:
        raise HTTPException(404, f"Agente '{nome}' non trovato")
    modulo_nome, _ = _AGENTI[nome]
    modulo = _import_agente(modulo_nome)
    if not modulo:
        raise HTTPException(500, f"Impossibile importare {modulo_nome}")

    def _avvia():
        mod = __import__(modulo_nome)
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if isinstance(obj, type) and hasattr(obj, "run"):
                try:
                    inst = obj()
                    return inst.run()
                except Exception as e:
                    raise RuntimeError(f"{attr}.run() fallito: {e}")
        raise RuntimeError(f"Nessuna classe con .run() trovata in {modulo_nome}")

    _log_run(nome, "avviato", "")
    background_tasks.add_task(_run_in_thread, nome, _avvia)
    return {"status": "avviato", "agente": nome, "ts": datetime.now().isoformat()}

# ---------------------------------------------------------------------------
# API — Landing Page
# ---------------------------------------------------------------------------
class LandingRequest(BaseModel):
    societa: str
    brief: str
    titolo: str = ""

@app.post("/api/landing/genera")
def api_landing_genera(req: LandingRequest):
    try:
        import agent_landing as al
        alanding = al.AgentLanding()
        insight_fake = {
            "id": f"web_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "azienda": req.societa,
            "titolo": req.titolo or req.brief[:60],
            "sintesi": req.brief,
            "dettaglio": req.brief,
            "tipo": "trend",
            "urgenza": "media",
            "impatto_commerciale": "alto",
        }
        entry = alanding.crea_bozza_landing(insight_fake)
        return {"ok": True, "landing": entry}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/landing/{land_id}/pubblica")
def api_landing_pubblica(land_id: str):
    try:
        import agent_landing as al
        alanding = al.AgentLanding()
        esito = alanding.pubblica_landing(land_id)
        return {"ok": True, "esito": esito}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("webapp:app", host="0.0.0.0", port=port, reload=False)
