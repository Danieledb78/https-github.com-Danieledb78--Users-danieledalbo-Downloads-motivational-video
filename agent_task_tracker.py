"""
agent_task_tracker.py — Task Tracker Agente
Gestisce task assegnati ai collaboratori con scadenze e reminder.
Archivia in tasks.json. Integrato nel bot Telegram.
"""

import os, json, uuid, logging
from datetime import datetime, timedelta
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)
TASKS_FILE = "tasks.json"
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude = anthropic.Anthropic(api_key=CLAUDE_KEY)

AZIENDE = ["Renergy Project&Build", "ACM&Partners", "RS Gas&Power", "Generale"]
STATI   = ["aperto", "in_corso", "completato", "annullato"]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def carica_tasks() -> list[dict]:
    if Path(TASKS_FILE).exists():
        with open(TASKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_tasks(tasks: list[dict]):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def get_task(task_id: str) -> dict | None:
    return next((t for t in carica_tasks() if t["id"] == task_id), None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def crea_task(
    titolo: str,
    descrizione: str,
    assegnato_a: str,
    assegnato_email: str,
    azienda: str,
    scadenza_str: str,
    priorita: str = "media",
    creato_da: str = "Daniele"
) -> dict:
    task = {
        "id":              uuid.uuid4().hex[:8],
        "titolo":          titolo,
        "descrizione":     descrizione,
        "assegnato_a":     assegnato_a,
        "assegnato_email": assegnato_email,
        "azienda":         azienda,
        "scadenza":        scadenza_str,
        "priorita":        priorita,
        "stato":           "aperto",
        "creato_da":       creato_da,
        "creato":          datetime.now().isoformat(),
        "aggiornato":      datetime.now().isoformat(),
        "note":            [],
    }
    tasks = carica_tasks()
    tasks.append(task)
    salva_tasks(tasks)
    log.info(f"Task creato: {task['id']} — {titolo}")
    return task

def aggiorna_stato_task(task_id: str, nuovo_stato: str) -> bool:
    tasks = carica_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["stato"]       = nuovo_stato
            t["aggiornato"]  = datetime.now().isoformat()
            salva_tasks(tasks)
            return True
    return False

def aggiungi_nota_task(task_id: str, nota: str) -> bool:
    tasks = carica_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["note"].append({"testo": nota, "data": datetime.now().isoformat()})
            t["aggiornato"] = datetime.now().isoformat()
            salva_tasks(tasks)
            return True
    return False


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
def get_tasks_aperti(azienda: str = None) -> list[dict]:
    tasks = carica_tasks()
    result = [t for t in tasks if t["stato"] in ("aperto", "in_corso")]
    if azienda:
        result = [t for t in result if t["azienda"] == azienda]
    return sorted(result, key=lambda t: ({"alta":0,"media":1,"bassa":2}.get(t.get("priorita","media"),1), t.get("scadenza","")))

def get_tasks_scaduti() -> list[dict]:
    oggi = datetime.now().date()
    tasks = carica_tasks()
    scaduti = []
    for t in tasks:
        if t["stato"] not in ("aperto", "in_corso"):
            continue
        try:
            scadenza = datetime.fromisoformat(t["scadenza"]).date()
            if scadenza < oggi:
                scaduti.append(t)
        except Exception:
            pass
    return scaduti

def get_tasks_in_scadenza(giorni: int = 2) -> list[dict]:
    oggi = datetime.now().date()
    soglia = oggi + timedelta(days=giorni)
    tasks = carica_tasks()
    result = []
    for t in tasks:
        if t["stato"] not in ("aperto", "in_corso"):
            continue
        try:
            scadenza = datetime.fromisoformat(t["scadenza"]).date()
            if oggi <= scadenza <= soglia:
                result.append(t)
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Claude — crea task da testo libero
# ---------------------------------------------------------------------------
PROMPT_TASK = """Sei l'assistente personale di un imprenditore italiano.
Estrai uno o più task dal seguente testo e restituisci JSON.

Testo:
{testo}

Rispondi SOLO con JSON valido:
[
  {{
    "titolo": "titolo breve del task",
    "descrizione": "descrizione dettagliata",
    "assegnato_a": "nome collaboratore (se non menzionato: 'Da assegnare')",
    "assegnato_email": "email se menzionata, altrimenti null",
    "azienda": "Renergy Project&Build | ACM&Partners | RS Gas&Power | Generale",
    "scadenza": "YYYY-MM-DD (stima ragionevole se non indicata)",
    "priorita": "alta | media | bassa"
  }}
]"""

def estrai_task_da_testo(testo: str) -> list[dict]:
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=1000,
        messages=[{"role": "user", "content": PROMPT_TASK.format(testo=testo)}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("["):t.rfind("]")+1])


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_task(t: dict, dettaglio: bool = False) -> str:
    p_emoji = {"alta":"🔴","media":"🟡","bassa":"🟢"}.get(t.get("priorita","media"),"⚪")
    s_emoji = {"aperto":"📋","in_corso":"⚙️","completato":"✅","annullato":"❌"}.get(t.get("stato","aperto"),"•")
    righe = [f"{p_emoji}{s_emoji} *[{t['id']}]* {t['titolo']}",
             f"   👤 {t.get('assegnato_a','')} | 🏢 {t.get('azienda','')}",
             f"   ⏰ Scadenza: {t.get('scadenza','N/D')}"]
    if dettaglio:
        righe.append(f"   📝 {t.get('descrizione','')}")
        if t.get("note"):
            righe.append(f"   💬 Ultima nota: {t['note'][-1]['testo']}")
    return "\n".join(righe)

def formatta_lista_tasks(tasks: list[dict], titolo: str) -> str:
    if not tasks:
        return f"*{titolo}*\n\nNessun task trovato."
    righe = [f"*{titolo}* ({len(tasks)} task)\n"]
    for t in tasks:
        righe.append(formatta_task(t))
    return "\n\n".join(righe)


# ---------------------------------------------------------------------------
# Reminder automatico
# ---------------------------------------------------------------------------
def genera_reminder_text() -> str:
    scaduti      = get_tasks_scaduti()
    in_scadenza  = get_tasks_in_scadenza(2)

    if not scaduti and not in_scadenza:
        return ""

    righe = ["⚠️ *Task Tracker — Reminder*\n"]
    if scaduti:
        righe.append(f"🔴 *Scaduti ({len(scaduti)}):*")
        for t in scaduti[:5]:
            righe.append(f"  • [{t['id']}] {t['titolo']} — {t.get('assegnato_a','N/D')} (sc. {t.get('scadenza','')})")
    if in_scadenza:
        righe.append(f"\n🟡 *In scadenza entro 48h ({len(in_scadenza)}):*")
        for t in in_scadenza[:5]:
            righe.append(f"  • [{t['id']}] {t['titolo']} — {t.get('assegnato_a','N/D')} (sc. {t.get('scadenza','')})")

    righe.append("\nUsa /tasks per vedere tutti i task aperti.")
    return "\n".join(righe)
