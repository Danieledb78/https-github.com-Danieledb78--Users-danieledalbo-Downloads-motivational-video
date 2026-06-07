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
def nuova_campagna(azienda: str, from_account_idx: int, target: str, offerta: str,
                   scadenza_normativa: str | None = None) -> dict:
    camp_id = f"camp_{uuid.uuid4().hex[:8]}"
    campagna = {
        "id":               camp_id,
        "azienda":          azienda,
        "from_account_idx": from_account_idx,
        "target_testo":     target,
        "offerta":          offerta,
        "scadenza_normativa": scadenza_normativa,  # es. "2026-01-01" — countdown urgenza (es. scadenza CSRD per ACM)
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
# Segmentazione ACM&Partners — sequenze dedicate per area di interesse
# (ESG/CSRD, M&A/crescita, China Desk, Australia Desk, generico)
# ---------------------------------------------------------------------------
SEGMENTI_ACM = {
    "ESG/CSRD":       "normativa ESG/CSRD, rendicontazione di sostenibilità, compliance ambientale",
    "M&A/crescita":   "operazioni di M&A, fusioni e acquisizioni, strategie di crescita e scaling",
    "China Desk":     "espansione e operatività in Cina, partnership e supply chain asiatica",
    "Australia Desk": "espansione e operatività in Australia e mercati APAC",
    "generico":       "consulenza manageriale generale per PMI in crescita",
}

PAROLE_CHIAVE_SEGMENTO_ACM = {
    "ESG/CSRD":       ("esg", "csrd", "sostenibilit", "ambiente", "bilancio sociale", "rendicontazione"),
    "M&A/crescita":   ("m&a", "fusione", "acquisizione", "merger", "crescita", "scaling", "exit"),
    "China Desk":     ("cina", "china", "asia", "shanghai", "pechino", "beijing"),
    "Australia Desk": ("australia", "sydney", "melbourne", "apac", "oceania"),
}

def assegna_segmento_acm(lead: dict) -> str:
    """Classifica euristicamente un lead ACM in un segmento tematico in base a
    settore/azienda/note (parole chiave). Fallback a 'generico' se nessun match."""
    testo = " ".join(str(lead.get(c, "")) for c in ("settore", "azienda", "note", "messaggio")).lower()
    for segmento, parole in PAROLE_CHIAVE_SEGMENTO_ACM.items():
        if any(p in testo for p in parole):
            return segmento
    return "generico"


def giorni_a_scadenza(scadenza_iso: str) -> int | None:
    """Giorni mancanti a una scadenza normativa (es. CSRD), per il countdown urgenza nelle email."""
    try:
        return (datetime.fromisoformat(scadenza_iso[:10]).date() - datetime.now().date()).days
    except (ValueError, TypeError):
        return None


PROMPT_SEQUENZA_SEGMENTATA = """Sei un copywriter esperto di email marketing B2B in italiano.
Crea una sequenza di 3 email di cold outreach per questa campagna ACM&Partners,
mirata SPECIFICAMENTE al segmento "{segmento}" (focus: {focus}).

Target generale campagna: {target}
Offerta / Obiettivo: {offerta}
{scadenza_txt}

Regole:
- Email 1 (cold_email): primo contatto, aggancio sul tema specifico del segmento, tono curioso e diretto
- Email 2 (followup_1): follow-up dopo 3-4 giorni, dato di settore o caso studio sul tema del segmento
- Email 3 (followup_2): ultimo contatto dopo 8-10 giorni, FOMO + facilità d'azione{urgenza_nota}

Stile: italiano professionale ma non formale, max 130 parole corpo.
Usa {{{{nome_referente}}}} e {{{{nome_azienda}}}} come variabili per personalizzazione.
In fondo ad ogni email aggiungi: "Per non ricevere altre comunicazioni: rispondi STOP."

Rispondi SOLO con JSON valido, nient'altro:
[
  {{"tipo": "cold_email",  "oggetto": "...", "corpo": "..."}},
  {{"tipo": "followup_1",  "oggetto": "...", "corpo": "..."}},
  {{"tipo": "followup_2",  "oggetto": "...", "corpo": "..."}}
]"""

def genera_sequenze_segmentate_acm(camp_id: str, segmenti: list[str] | None = None) -> dict:
    """Genera (o rigenera) sequenze email dedicate per ciascun segmento ACM presente
    tra i lead della campagna. Salvate in camp['sequenze_segmentate'][segmento],
    parallele alla 'email_sequence' di default (usata come fallback)."""
    campagne = carica_campagne()
    camp = campagne[camp_id]
    if "ACM" not in camp.get("azienda", ""):
        raise ValueError("Segmentazione disponibile solo per campagne ACM&Partners")

    segmenti = segmenti or sorted({l.get("segmento", "generico") for l in camp.get("lead", [])}) or ["generico"]
    scadenza = camp.get("scadenza_normativa")
    giorni   = giorni_a_scadenza(scadenza) if scadenza else None
    scadenza_txt = f"Scadenza normativa di riferimento: {scadenza} (mancano {giorni} giorni)." if giorni is not None else ""
    urgenza_nota = (" — richiama la scadenza ormai vicina per creare urgenza, senza allarmismo"
                    if giorni is not None and giorni <= 120 else "")

    sequenze = camp.setdefault("sequenze_segmentate", {})
    for segmento in segmenti:
        focus = SEGMENTI_ACM.get(segmento, SEGMENTI_ACM["generico"])
        msg = claude.messages.create(
            model="claude-opus-4-8", max_tokens=3000,
            messages=[{"role": "user", "content": PROMPT_SEQUENZA_SEGMENTATA.format(
                segmento=segmento, focus=focus, target=camp["target_testo"], offerta=camp["offerta"],
                scadenza_txt=scadenza_txt, urgenza_nota=urgenza_nota,
            )}],
        )
        t = msg.content[0].text.strip()
        seq = json.loads(t[t.find("["):t.rfind("]")+1])
        for email in seq:
            email["approvata"] = False
        sequenze[segmento] = seq

    salva_campagne(campagne)
    return sequenze

def approva_email_segmento(camp_id: str, segmento: str, tipo: str, oggetto: str = None, corpo: str = None):
    """Segna come approvata un'email di una sequenza segmentata ACM, con eventuale modifica."""
    campagne = carica_campagne()
    camp = campagne[camp_id]
    for email in camp.get("sequenze_segmentate", {}).get(segmento, []):
        if email["tipo"] == tipo:
            email["approvata"] = True
            if oggetto: email["oggetto"] = oggetto
            if corpo:   email["corpo"]   = corpo
    salva_campagne(campagne)

def tutte_approvate_segmento(camp_id: str, segmento: str) -> bool:
    camp = get_campagna(camp_id)
    seq = camp.get("sequenze_segmentate", {}).get(segmento, []) if camp else []
    return bool(seq) and all(e.get("approvata") for e in seq)


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
    is_acm = "ACM" in camp.get("azienda", "")

    for az in lead_list:
        email = az.get("primary_email") or az.get("email", "")
        if not email or email in email_esistenti:
            continue
        nuovo_lead = {
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
        }
        if is_acm:
            nuovo_lead["segmento"] = assegna_segmento_acm(nuovo_lead)
        camp["lead"].append(nuovo_lead)
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

def get_lead_da_processare_segmentato(camp_id: str) -> dict:
    """Come get_lead_da_processare ma raggruppato per segmento ACM. I lead il cui
    segmento non ha una sequenza dedicata ricadono su 'generico' (o sul proprio
    segmento se nessun 'generico' esiste, per non perdere l'invio)."""
    camp = get_campagna(camp_id)
    if not camp:
        return {}
    oggi = datetime.now().date()
    sequenze_disponibili = camp.get("sequenze_segmentate", {})
    result: dict[str, dict[str, list]] = {}

    for lead in camp.get("lead", []):
        segmento = lead.get("segmento") or "generico"
        if segmento not in sequenze_disponibili and "generico" in sequenze_disponibili:
            segmento = "generico"
        bucket = result.setdefault(segmento, {"cold_email": [], "followup_1": [], "followup_2": []})
        stato = lead.get("stato")
        if stato == "da_inviare":
            bucket["cold_email"].append(lead)
        elif stato == "email1_inviata" and lead.get("data_email1"):
            if (oggi - datetime.fromisoformat(lead["data_email1"]).date()).days >= 3:
                bucket["followup_1"].append(lead)
        elif stato == "email2_inviata" and lead.get("data_email2"):
            if (oggi - datetime.fromisoformat(lead["data_email2"]).date()).days >= 5:
                bucket["followup_2"].append(lead)
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
