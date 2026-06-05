"""
campagna_manager.py — Gestione Campagne Outbound Marketing
Crea, archivia e gestisce sequenze di email per ogni obiettivo/azienda.

Ogni campagna ha:
  - Azienda mittente (Renergy, ACM, BevManager, Combinata)
  - Target (descrizione + filtri Apollo)
  - Offerta / obiettivo
  - Sequenza di 3 email generate da Claude
  - Lista lead con stato per ognuno
  - Stats di invio
"""

import os, json, uuid, logging, requests
from datetime import datetime
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)

CAMPAIGNS_FILE = "campaigns.json"
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
APOLLO_KEY = os.environ.get("APOLLO_API_KEY")
claude = anthropic.Anthropic(api_key=CLAUDE_KEY)


# ---------------------------------------------------------------------------
# Storage campagne (JSON semplice — nessun DB necessario)
# ---------------------------------------------------------------------------
def carica_campagne() -> dict:
    if Path(CAMPAIGNS_FILE).exists():
        with open(CAMPAIGNS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def salva_campagne(campagne: dict):
    with open(CAMPAIGNS_FILE, "w", encoding="utf-8") as f:
        json.dump(campagne, f, ensure_ascii=False, indent=2)

def get_campagna(camp_id: str) -> dict | None:
    return carica_campagne().get(camp_id)

def get_campagne_attive() -> list[dict]:
    return [c for c in carica_campagne().values() if c.get("stato") == "attiva"]

def get_tutte_campagne() -> list[dict]:
    return list(carica_campagne().values())


# ---------------------------------------------------------------------------
# Creazione campagna
# ---------------------------------------------------------------------------
def nuova_campagna(azienda: str, from_account_idx: int, target: str, offerta: str) -> dict:
    camp_id = f"camp_{uuid.uuid4().hex[:8]}"
    campagna = {
        "id":               camp_id,
        "azienda":          azienda,
        "from_account_idx": from_account_idx,
        "target_testo":     target,
        "offerta":          offerta,
        "stato":            "bozza",          # bozza → attiva → pausata → completata
        "email_sequence":   [],               # [{"tipo","oggetto","corpo","approvata"}]
        "lead":             [],
        "creata":           datetime.now().isoformat(),
        "stats":            {"inviati": 0, "opt_out": 0},
    }
    campagne = carica_campagne()
    campagne[camp_id] = campagna
    salva_campagne(campagne)
    log.info(f"Campagna creata: {camp_id} — {azienda} / {offerta}")
    return campagna


# ---------------------------------------------------------------------------
# Claude — genera sequenza email
# ---------------------------------------------------------------------------
PROMPT_SEQUENZA = """Sei un copywriter esperto di email marketing B2B in italiano.
Crea una sequenza di 3 email di cold outreach per questa campagna:

Azienda mittente: {azienda}
Target: {target}
Offerta / Obiettivo: {offerta}

Regole:
- Email 1 (cold_email): primo contatto, presenta l'offerta, tono curioso e diretto
- Email 2 (followup_1): follow-up dopo 3-4 giorni, angolo diverso (dato di settore, caso studio breve)
- Email 3 (followup_2): ultimo contatto dopo 8-10 giorni, FOMO + facilità d'azione

Stile: italiano professionale ma non formale, max 130 parole corpo.
Usa {{{{nome_referente}}}} e {{{{nome_azienda}}}} come variabili per personalizzazione.
In fondo ad ogni email aggiungi: "Per non ricevere altre comunicazioni: rispondi STOP."

Rispondi SOLO con JSON valido, nient'altro:
[
  {{"tipo": "cold_email",  "oggetto": "...", "corpo": "..."}},
  {{"tipo": "followup_1",  "oggetto": "...", "corpo": "..."}},
  {{"tipo": "followup_2",  "oggetto": "...", "corpo": "..."}}
]"""

def genera_sequenza_email(camp_id: str) -> list[dict]:
    campagne = carica_campagne()
    camp = campagne[camp_id]
    msg = claude.messages.create(
        model="claude-opus-4-8",
        max_tokens=3000,
        messages=[{"role": "user", "content": PROMPT_SEQUENZA.format(
            azienda=camp["azienda"],
            target=camp["target_testo"],
            offerta=camp["offerta"],
        )}],
    )
    t = msg.content[0].text.strip()
    sequenza = json.loads(t[t.find("["):t.rfind("]")+1])
    for email in sequenza:
        email["approvata"] = False
    camp["email_sequence"] = sequenza
    salva_campagne(campagne)
    return sequenza

def approva_email(camp_id: str, tipo: str, oggetto: str = None, corpo: str = None):
    """Segna un'email come approvata, con eventuale modifica di oggetto/corpo."""
    campagne = carica_campagne()
    camp = campagne[camp_id]
    for email in camp["email_sequence"]:
        if email["tipo"] == tipo:
            email["approvata"] = True
            if oggetto: email["oggetto"] = oggetto
            if corpo:   email["corpo"]   = corpo
    salva_campagne(campagne)

def tutte_approvate(camp_id: str) -> bool:
    camp = get_campagna(camp_id)
    return camp and all(e.get("approvata") for e in camp.get("email_sequence", []))


# ---------------------------------------------------------------------------
# Apollo — ricerca lead per campagna
# ---------------------------------------------------------------------------
PROMPT_FILTRI_APOLLO = """Traduci questo target di campagna in filtri Apollo.io per l'Italia.
Azienda: {azienda}
Target: {target}

Rispondi SOLO con JSON:
{{
  "q_organization_locations": ["lista città/regioni italiane o 'Italy'"],
  "organization_industries": ["lista settori Apollo in inglese"],
  "organization_num_employees_ranges": ["range es. '10,200'"]
}}"""

def cerca_lead_per_campagna(camp_id: str, n: int = 25) -> list[dict]:
    camp = get_campagna(camp_id)
    if not camp:
        return []

    # Claude interpreta il target e lo traduce in filtri Apollo
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=400,
        messages=[{"role": "user", "content": PROMPT_FILTRI_APOLLO.format(
            azienda=camp["azienda"], target=camp["target_testo"]
        )}],
    )
    t = msg.content[0].text.strip()
    filtri = json.loads(t[t.find("{"):t.rfind("}")+1])

    headers = {"Content-Type": "application/json", "X-Api-Key": APOLLO_KEY}
    body    = {**filtri, "per_page": n, "page": 1}
    resp    = requests.post("https://api.apollo.io/v1/mixed_companies/search",
                            headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("organizations") or data.get("accounts") or []


def aggiungi_lead_campagna(camp_id: str, lead_list: list[dict]):
    campagne = carica_campagne()
    camp = campagne[camp_id]
    aggiunti = 0
    email_esistenti = {l["email"] for l in camp["lead"] if l.get("email")}

    for az in lead_list:
        email = az.get("primary_email") or az.get("email", "")
        if not email or email in email_esistenti:
            continue
        camp["lead"].append({
            "id":           uuid.uuid4().hex[:8],
            "azienda":      az.get("name", ""),
            "email":        email,
            "referente":    "",
            "citta":        az.get("city", ""),
            "settore":      az.get("industry", ""),
            "stato":        "da_inviare",
            "data_email1":  None,
            "data_email2":  None,
            "data_email3":  None,
        })
        email_esistenti.add(email)
        aggiunti += 1

    camp["stato"] = "attiva"
    salva_campagne(campagne)
    log.info(f"Campagna {camp_id}: aggiunti {aggiunti} lead")
    return aggiunti


# ---------------------------------------------------------------------------
# Invio sequenze
# ---------------------------------------------------------------------------
def get_lead_da_processare(camp_id: str) -> dict:
    """Ritorna lead da inviare per ogni step della sequenza."""
    camp = get_campagna(camp_id)
    if not camp:
        return {}
    oggi = datetime.now().date()
    result = {"cold_email": [], "followup_1": [], "followup_2": []}

    for lead in camp.get("lead", []):
        stato = lead.get("stato")
        if stato == "da_inviare":
            result["cold_email"].append(lead)
        elif stato == "email1_inviata" and lead.get("data_email1"):
            giorni = (oggi - datetime.fromisoformat(lead["data_email1"]).date()).days
            if giorni >= 3:
                result["followup_1"].append(lead)
        elif stato == "email2_inviata" and lead.get("data_email2"):
            giorni = (oggi - datetime.fromisoformat(lead["data_email2"]).date()).days
            if giorni >= 5:
                result["followup_2"].append(lead)
    return result

def segna_inviata(camp_id: str, lead_id: str, tipo: str):
    campagne = carica_campagne()
    camp = campagne[camp_id]
    now = datetime.now().isoformat()
    for lead in camp["lead"]:
        if lead["id"] == lead_id:
            map_stato = {
                "cold_email":  ("email1_inviata", "data_email1"),
                "followup_1":  ("email2_inviata", "data_email2"),
                "followup_2":  ("email3_inviata", "data_email3"),
            }
            nuovo_stato, campo_data = map_stato[tipo]
            lead["stato"]      = nuovo_stato
            lead[campo_data]   = now
            camp["stats"]["inviati"] += 1
            break
    salva_campagne(campagne)

def segna_opt_out(camp_id: str, email: str):
    campagne = carica_campagne()
    camp = campagne.get(camp_id, {})
    for lead in camp.get("lead", []):
        if lead.get("email") == email:
            lead["stato"] = "opt_out"
            camp["stats"]["opt_out"] = camp["stats"].get("opt_out", 0) + 1
    salva_campagne(campagne)


# ---------------------------------------------------------------------------
# Stats e riepilogo
# ---------------------------------------------------------------------------
def get_stats(camp_id: str) -> dict:
    camp = get_campagna(camp_id)
    if not camp:
        return {}
    lead = camp.get("lead", [])
    stati = {}
    for l in lead:
        s = l.get("stato","?")
        stati[s] = stati.get(s, 0) + 1
    return {
        "nome":         f"{camp['azienda']} — {camp['offerta']}",
        "stato":        camp.get("stato"),
        "totale_lead":  len(lead),
        "da_inviare":   stati.get("da_inviare", 0),
        "in_sequenza":  stati.get("email1_inviata", 0) + stati.get("email2_inviata", 0),
        "completati":   stati.get("email3_inviata", 0),
        "opt_out":      stati.get("opt_out", 0),
        "inviati_tot":  camp["stats"].get("inviati", 0),
    }

def formatta_riepilogo_campagna(camp_id: str) -> str:
    s = get_stats(camp_id)
    if not s:
        return "Campagna non trovata."
    return (f"📊 *{s['nome']}*\n"
            f"Stato: {s['stato']} | Lead: {s['totale_lead']}\n"
            f"Da inviare: {s['da_inviare']} | In sequenza: {s['in_sequenza']}\n"
            f"Completati: {s['completati']} | Opt-out: {s['opt_out']}\n"
            f"Email inviate tot: {s['inviati_tot']}")
