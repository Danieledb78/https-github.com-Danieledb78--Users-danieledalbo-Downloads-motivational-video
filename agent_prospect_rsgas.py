"""
agent_prospect_rsgas.py — Lead Generation Specifica per RS Gas&Power
Pipeline: Apollo → Claude (qualifica ICP energivori) → Hub CRM

Differenza chiave rispetto agli altri agenti lead-gen: RS Gas ha barriera d'ingresso
ZERO per il cliente (nessun investimento, nessun cantiere, firma in 24 ore). Niente
analisi satellitare: qui conta il profilo di consumo energetico, non il tetto.

Schedulato ogni lunedì alle 07:00 (insieme a Renergy e ACM). Trigger manuale: /prospect_rsgas
"""

import os, json, logging
from datetime import datetime, timedelta
import requests
import anthropic

import hub_client as hub
import shared_intelligence as si
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

APOLLO_KEY = os.environ.get("APOLLO_API_KEY")
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)

# ---------------------------------------------------------------------------
# ICP RS Gas&Power — settori energivori, Nord-Est con priorità
# ---------------------------------------------------------------------------
TARGET_LOCATIONS = [
    "Veneto, Italy", "Friuli Venezia Giulia, Italy", "Trentino-Alto Adige, Italy",
]
TARGET_INDUSTRIES = [
    "manufacturing", "logistics", "food and beverages", "automotive",
    "industrial automation", "water treatment", "warehousing",
]
LEADS_PER_RUN  = 25
MIN_EMPLOYEES  = 10
MAX_EMPLOYEES  = 200

CLAIM_PRINCIPALE = "Riduci la bolletta energetica da subito, senza investimenti e senza cambiare nulla."
PAIN_POINTS = [
    "Stai pagando la tua energia al prezzo di listino quando potresti avere condizioni migliori",
    "Il tuo contratto attuale ha penali di recesso che ti tengono bloccato?",
    "L'energia è un costo fisso: ogni mese che passa senza ottimizzarlo è denaro perso",
]


# ---------------------------------------------------------------------------
# Apollo — ricerca aziende energivore Nord-Est
# ---------------------------------------------------------------------------
def apollo_cerca_aziende() -> list[dict]:
    headers = {"Content-Type": "application/json", "X-Api-Key": APOLLO_KEY}
    body = {
        "q_organization_locations":          TARGET_LOCATIONS,
        "organization_industries":           TARGET_INDUSTRIES,
        "organization_num_employees_ranges": [f"{MIN_EMPLOYEES},{MAX_EMPLOYEES}"],
        "per_page": LEADS_PER_RUN,
        "page":     1,
    }
    try:
        resp = requests.post("https://api.apollo.io/v1/mixed_companies/search",
                             headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        aziende = data.get("organizations") or data.get("accounts") or []
        log.info(f"Apollo RS Gas: {len(aziende)} aziende trovate")
        return aziende
    except Exception as e:
        log.warning(f"Apollo RS Gas — ricerca fallita: {e}")
        return []


# ---------------------------------------------------------------------------
# Claude — qualifica profilo energetico e messaging
# ---------------------------------------------------------------------------
PROMPT_QUALIFICA = """Sei un consulente commerciale per RS Gas&Power, rivenditore di energia (luce e gas)
abilitato ARERA che opera nel Nord-Est Italia. Il punto di forza dell'offerta è la SEMPLICITÀ:
zero investimenti, zero cantieri, firma in 24 ore, attivazione rapida.

Valuta questa azienda come potenziale cliente energia:
{azienda_json}

ICP target: settori energivori (manifattura, logistica, food processing, automotive, trattamento acque),
10-200 dipendenti, consumi luce >50 kW/mese o gas >500 Smc/anno, Veneto/FVG/Trentino-Alto Adige prioritari.
Decisore: titolare (PMI piccole) o CFO/responsabile acquisti (PMI medie).

Rispondi SOLO con JSON valido:
{{
  "score": intero 0-5 (5 = ICP perfetto, energivoro, decisore raggiungibile),
  "profilo_energetico_stimato": "stima consumi e potenziale risparmio",
  "decisore_probabile": "titolare | CFO/responsabile acquisti | altro",
  "pain_point_piu_rilevante": "quale dei pain point colpisce di più questa azienda e perché",
  "messaggio_apertura": "prima riga di un'email di primo contatto, diretta, senza gergo tecnico ARERA"
}}"""

def qualifica_azienda(azienda: dict) -> dict:
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=600, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_QUALIFICA.format(
            azienda_json=json.dumps({
                "nome":       azienda.get("name", ""),
                "settore":    azienda.get("industry", ""),
                "dipendenti": azienda.get("estimated_num_employees", ""),
                "città":      azienda.get("city", ""),
                "descrizione": (azienda.get("short_description", "") or "")[:300],
            }, ensure_ascii=False)
        )}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Hub CRM — creazione lead
# ---------------------------------------------------------------------------
def hub_crea_lead_rsgas(azienda: dict, qualifica: dict) -> dict | None:
    token = hub.hub_token()
    if not token:
        return None
    prossimo_lunedi = (datetime.now() + timedelta(days=(7 - datetime.now().weekday()) % 7 or 7)).strftime("%Y-%m-%d")
    payload = {
        "ragione_sociale":       azienda.get("name", ""),
        "email":                 azienda.get("primary_domain", ""),
        "citta":                 azienda.get("city", ""),
        "fonte":                 "agent_prospect_rsgas",
        "tipo_servizio":         "energia_luce_gas",
        "score":                 qualifica.get("score", 0),
        "note":                  f"{qualifica.get('profilo_energetico_stimato','')} | "
                                 f"Decisore: {qualifica.get('decisore_probabile','')} | "
                                 f"Pain point: {qualifica.get('pain_point_piu_rilevante','')}",
        "data_prossimo_followup": prossimo_lunedi,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "", 0)}
    try:
        r = requests.post(f"{hub.HUB_BASE}/crm/lead",
                          headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"Hub — creazione lead RS Gas fallita: {e}")
        return None


# ---------------------------------------------------------------------------
# Shared intelligence
# ---------------------------------------------------------------------------
def _pubblica_insight(stats: dict):
    if stats["create_hub"] == 0:
        return
    try:
        si.aggiungi_insight("agent_prospect_rsgas", {
            "azienda":             "RS_Gas",
            "tipo":                "trend",
            "titolo":              f"{stats['create_hub']} nuovi prospect energivori Nord-Est qualificati",
            "sintesi":             f"Pipeline RS Gas&Power: {stats['create_hub']} aziende energivore "
                                   f"in Veneto/FVG/Trentino con profilo ICP idoneo.",
            "urgenza":             "media",
            "impatto_commerciale": "medio",
            "azioni_suggerite":    ["campagna_email", "contatto_diretto"],
            "target_icp":          "PMI energivore 10-200 dip, Veneto/FVG/Trentino-Alto Adige",
        })
    except Exception as e:
        log.warning(f"Pubblicazione insight prospect RS Gas fallita: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> dict:
    log.info("=== AGENTE PROSPECT RS GAS — avvio ===")
    stats = {"trovate": 0, "create_hub": 0, "scartate": 0, "errori": 0}

    aziende = apollo_cerca_aziende()
    stats["trovate"] = len(aziende)

    for az in aziende:
        nome = az.get("name", "azienda sconosciuta")
        try:
            qualifica = qualifica_azienda(az)
            if qualifica.get("score", 0) < 2:
                stats["scartate"] += 1
                log.info(f"  → SCARTATA: {nome} (score={qualifica.get('score')})")
                continue
            esito = hub_crea_lead_rsgas(az, qualifica)
            if esito is not None:
                stats["create_hub"] += 1
                log.info(f"  → LEAD CREATO ✅ {nome} score={qualifica.get('score')}")
            else:
                stats["errori"] += 1
        except Exception as e:
            stats["errori"] += 1
            log.warning(f"  → Errore su {nome}: {e}")

    _pubblica_insight(stats)
    log.info(f"=== AGENTE PROSPECT RS GAS — completato: {stats} ===")
    return stats


def formatta_riepilogo(stats: dict) -> str:
    return (
        f"⚡🔵 *Prospect RS Gas&Power — Lead Generation*\n"
        f"🔍 Aziende analizzate: {stats.get('trovate',0)}\n"
        f"✅ Lead creati in Hub: {stats.get('create_hub',0)}\n"
        f"⏭ Scartate (score basso): {stats.get('scartate',0)}\n"
        f"❌ Errori: {stats.get('errori',0)}\n\n"
        f"💬 Claim: _{CLAIM_PRINCIPALE}_"
    )


if __name__ == "__main__":
    s = run()
    print(formatta_riepilogo(s))
