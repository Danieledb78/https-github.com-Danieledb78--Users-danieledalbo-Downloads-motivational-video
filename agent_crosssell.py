"""
agent_crosssell.py — Cross-selling Inter-Aziendale (AIOS v2.0)
Logica core: i clienti esistenti di un'azienda del gruppo sono lead caldi per le altre.
Identifica opportunità di cross-sell tra Renergy, RS Gas&Power e ACM&Partners,
evita duplicati con crosssell_log.json e propone i contatti all'owner su Telegram.

Schedulato ogni lunedì alle 07:15 (dopo lead gen, prima del report). Trigger: /crosssell
"""

import os, json, logging
from datetime import datetime
from pathlib import Path

import hub_client as hub
import shared_intelligence as si

log = logging.getLogger(__name__)
LOG_FILE = "crosssell_log.json"

AZIENDE_NOME = {"Renergy": "Renergy Project&Build", "RS_Gas": "RS Gas&Power", "ACM": "ACM&Partners"}

# ---------------------------------------------------------------------------
# Regole di cross-sell — (azienda_origine, condizione, azienda_target, claim)
# ---------------------------------------------------------------------------
REGOLE = [
    {
        "origine": "Renergy", "condizione_stato": ("installato", "cliente"),
        "target": "RS_Gas",
        "claim": "Abbina energia verde al tuo impianto FV: ottimizza il mix energia prodotta/consumata "
                 "con un contratto su misura RS Gas&Power.",
        "tag": "rinergy_to_rsgas_clienti_installati",
    },
    {
        "origine": "RS_Gas", "condizione_stato": ("attivo", "cliente"),
        "target": "Renergy",
        "claim": "Produci tu stesso l'energia che oggi rivendiamo: valuta un impianto fotovoltaico "
                 "industriale e azzera la voce di costo energia in bolletta.",
        "tag": "rsgas_to_renergy_clienti_attivi",
    },
    {
        "origine": "Renergy", "condizione_stato": ("preventivo",),
        "target": "RS_Gas",
        "claim": "Nel frattempo che valuti l'impianto, riduci subito la bolletta energetica: "
                 "nessun investimento, attivazione in pochi giorni.",
        "tag": "renergy_preventivo_to_rsgas",
    },
    {
        "origine": "ACM", "condizione_stato": ("cliente", "in_consulenza"),
        "target": "Renergy",
        "claim": "La tua strategia ESG include la produzione di energia rinnovabile? "
                 "Un impianto FV industriale è spesso il modo più concreto per ridurre le emissioni Scope 2.",
        "tag": "acm_to_renergy_consulenza_esg",
    },
]

# Cross-sell verso ACM (PMI con >20 dipendenti diventano lead ESG/CSRD)
SOGLIA_DIPENDENTI_ACM = 20
CLAIM_ACM = ("L'obbligo di rendicontazione ESG (CSRD) si estende progressivamente anche alle PMI: "
             "una valutazione di readiness oggi evita rincorse normative domani.")


# ---------------------------------------------------------------------------
# Storage log + anti-duplicati (email come chiave univoca)
# ---------------------------------------------------------------------------
def carica_log() -> list[dict]:
    if Path(LOG_FILE).exists():
        with open(LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_log(entries: list[dict]):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(entries[-500:], f, ensure_ascii=False, indent=2)

def _gia_proposto(email: str, target: str) -> bool:
    email = (email or "").lower().strip()
    if not email:
        return True   # senza email non possiamo deduplicare né contattare: scarta
    return any(e.get("email", "").lower() == email and e.get("target") == target for e in carica_log())

def registra_proposta(lead: dict, regola: dict) -> dict:
    entry = {
        "id":         f"cs_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{abs(hash(lead.get('email','')))%10000}",
        "email":      (lead.get("email") or "").lower().strip(),
        "nome":       lead.get("ragione_sociale", lead.get("name", "")),
        "origine":    regola["origine"],
        "target":     regola["target"],
        "tag":        regola["tag"],
        "claim":      regola["claim"],
        "proposta_il": datetime.now().isoformat(),
        "esito":      "proposto",   # proposto | accettato | convertito | rifiutato
    }
    log_entries = carica_log()
    log_entries.append(entry)
    salva_log(log_entries)
    return entry


# ---------------------------------------------------------------------------
# Identificazione opportunità
# ---------------------------------------------------------------------------
def _email_in_lista(email: str, lista: list[dict]) -> bool:
    email = (email or "").lower().strip()
    return any((l.get("email") or "").lower().strip() == email for l in lista)

def trova_opportunita() -> list[dict]:
    """Scansiona i clienti per ogni azienda e individua opportunità di cross-sell non ancora proposte."""
    opportunita = []

    clienti_per_azienda = {}
    for azienda in ("Renergy", "RS_Gas", "ACM"):
        try:
            clienti_per_azienda[azienda] = hub.get_clienti_per_azienda(AZIENDE_NOME[azienda])
        except Exception as e:
            log.warning(f"Cross-sell — lettura clienti {azienda} fallita: {e}")
            clienti_per_azienda[azienda] = []

    for regola in REGOLE:
        origine = regola["origine"]
        target  = regola["target"]
        for cliente in clienti_per_azienda.get(origine, []):
            stato = str(cliente.get("stato", "")).lower()
            if regola["condizione_stato"] and stato not in regola["condizione_stato"]:
                continue
            email = cliente.get("email", "")
            if not email or _gia_proposto(email, target):
                continue
            # Verifica che il contatto non sia già cliente dell'azienda target
            if _email_in_lista(email, clienti_per_azienda.get(target, [])):
                continue
            opportunita.append({"lead": cliente, "regola": regola})

    # Cross-sell verso ACM per PMI strutturate (>20 dipendenti) clienti Renergy o RS Gas
    for azienda in ("Renergy", "RS_Gas"):
        for cliente in clienti_per_azienda.get(azienda, []):
            try:
                dipendenti = int(cliente.get("n_dipendenti", cliente.get("dipendenti", 0)) or 0)
            except (TypeError, ValueError):
                dipendenti = 0
            if dipendenti < SOGLIA_DIPENDENTI_ACM:
                continue
            email = cliente.get("email", "")
            if not email or _gia_proposto(email, "ACM"):
                continue
            if _email_in_lista(email, clienti_per_azienda.get("ACM", [])):
                continue
            opportunita.append({"lead": cliente, "regola": {
                "origine": azienda, "target": "ACM", "claim": CLAIM_ACM,
                "tag": f"{azienda.lower()}_pmi_strutturata_to_acm_esg",
                "condizione_stato": (),
            }})

    return opportunita


# ---------------------------------------------------------------------------
# Pubblica insight cumulativo per l'orchestratore
# ---------------------------------------------------------------------------
def _pubblica_insight(opportunita: list[dict]):
    if not opportunita:
        return
    by_target = {}
    for o in opportunita:
        by_target.setdefault(o["regola"]["target"], 0)
        by_target[o["regola"]["target"]] += 1
    for target, n in by_target.items():
        try:
            si.aggiungi_insight("agent_crosssell", {
                "azienda":             target,
                "tipo":                "trend",
                "titolo":              f"{n} opportunità di cross-sell verso {AZIENDE_NOME.get(target, target)}",
                "sintesi":             f"Identificati {n} clienti del gruppo profilati come lead caldi per {AZIENDE_NOME.get(target,target)}.",
                "urgenza":             "media",
                "impatto_commerciale": "alto" if n >= 3 else "medio",
                "azioni_suggerite":    ["contatto_diretto", "campagna_email"],
                "target_icp":          "Clienti esistenti del gruppo, profilo cross-sell",
            })
        except Exception as e:
            log.warning(f"Cross-sell — pubblicazione insight fallita: {e}")


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
AZ_EMOJI = {"Renergy": "⚡", "RS_Gas": "⚡🔵", "ACM": "🏢"}

def formatta_riepilogo(opportunita: list[dict]) -> str:
    if not opportunita:
        return "🔄 *Cross-sell* — nessuna nuova opportunità identificata questa settimana."
    righe = [f"🔄 *Cross-sell Inter-Aziendale — {len(opportunita)} opportunità*\n"]
    for o in opportunita[:8]:
        lead, regola = o["lead"], o["regola"]
        e_o = AZ_EMOJI.get(regola["origine"], "•")
        e_t = AZ_EMOJI.get(regola["target"], "•")
        righe.append(f"{e_o} → {e_t} *{lead.get('ragione_sociale', lead.get('name','(senza nome)'))}*")
        righe.append(f"   {regola['claim'][:140]}")
    if len(opportunita) > 8:
        righe.append(f"\n…e altre {len(opportunita)-8}.")
    righe.append("\n_Usa /crosssell per registrare e proporre i contatti._")
    return "\n".join(righe)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> tuple[list[dict], str]:
    log.info("=== AGENTE CROSS-SELL — avvio ===")
    opportunita = trova_opportunita()
    for o in opportunita:
        try:
            registra_proposta(o["lead"], o["regola"])
        except Exception as e:
            log.warning(f"Cross-sell — registrazione proposta fallita: {e}")
    _pubblica_insight(opportunita)
    messaggio = formatta_riepilogo(opportunita)
    log.info(f"=== AGENTE CROSS-SELL — {len(opportunita)} opportunità ===")
    return opportunita, messaggio
