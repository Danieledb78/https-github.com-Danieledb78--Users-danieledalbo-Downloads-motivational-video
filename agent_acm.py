"""
agent_acm.py — Lead Generation per ACM&Partners
Pipeline: Apollo → Claude Enrichment → Hub CRM

Target: PMI italiane in crescita, startup pre-serie B, aziende
che cercano supporto strategico, M&A, espansione internazionale.

Secrets Replit aggiuntivi rispetto a Renergy:
  ACM_HUB_EMAIL       → email login Hub ACM (se diverso da Renergy)
  ACM_HUB_PASSWORD    → password Hub ACM
  ACM_HUB_BASE        → es. https://hub.acmpartners.it/api
                         (se usano la stessa istanza Hub di Renergy,
                          metti lo stesso URL e stesse credenziali)
"""

import os
import time
import json
import logging
import requests
import anthropic
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione — fallback su Renergy Hub se ACM usa la stessa istanza
# ---------------------------------------------------------------------------
APOLLO_KEY   = os.environ.get("APOLLO_API_KEY")
CLAUDE_KEY   = os.environ.get("CLAUDE_API_KEY")
HUB_EMAIL    = os.environ.get("ACM_HUB_EMAIL")    or os.environ.get("HUB_EMAIL")
HUB_PASSWORD = os.environ.get("ACM_HUB_PASSWORD") or os.environ.get("HUB_PASSWORD")
HUB_BASE     = os.environ.get("ACM_HUB_BASE")     or "https://hub.renergygroup.it/api"

# ACM è trasversale: tutta Italia, settori misti
TARGET_COUNTRIES = ["Italy"]
TARGET_INDUSTRIES = [
    "manufacturing", "industrial", "technology", "software",
    "retail", "food and beverages", "healthcare", "construction",
    "logistics", "agriculture", "services", "wholesale",
    "media", "education", "real estate",
]
EMPLOYEE_RANGES = ["10,200"]   # PMI: 10-200 dipendenti
LEADS_PER_RUN   = 30

# Segnali di crescita — Apollo filtri aggiuntivi
GROWTH_SIGNALS = {
    "organization_latest_funding_stage_cd": ["seed", "series_a", "series_b"],
}


# ---------------------------------------------------------------------------
# Hub CRM
# ---------------------------------------------------------------------------
def hub_login() -> str:
    resp = requests.post(
        f"{HUB_BASE}/auth/login",
        json={"email": HUB_EMAIL, "password": HUB_PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    log.info("Hub ACM login OK")
    return resp.json().get("token")


def hub_crea_lead(token: str, dati: dict) -> dict:
    prossimo_lunedi = (
        datetime.now() + timedelta(days=(7 - datetime.now().weekday()) % 7 or 7)
    ).strftime("%Y-%m-%d")

    payload = {
        "ragione_sociale":        dati.get("name", ""),
        "referente_nome":         dati.get("referente", ""),
        "email":                  dati.get("email", ""),
        "telefono":               dati.get("phone", ""),
        "citta":                  dati.get("city", ""),
        "provincia":              dati.get("province", ""),
        "fonte":                  "apollo_agente_acm",
        "score":                  dati.get("score", 0),
        "note":                   dati.get("note", ""),
        "data_prossimo_followup": prossimo_lunedi,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "", 0)}

    resp = requests.post(
        f"{HUB_BASE}/crm/lead",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Apollo — cerca PMI in crescita
# ---------------------------------------------------------------------------
def apollo_cerca_aziende_acm() -> list[dict]:
    """Due ricerche separate: startup con funding + PMI in crescita."""
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": APOLLO_KEY,
    }
    risultati = []

    # Ricerca 1: startup con round di finanziamento recente
    body_startup = {
        "organization_locations":            TARGET_COUNTRIES,
        "organization_industries":           TARGET_INDUSTRIES,
        "organization_num_employees_ranges": EMPLOYEE_RANGES,
        "organization_latest_funding_stage_cd": ["seed", "series_a", "series_b"],
        "per_page": LEADS_PER_RUN // 2,
        "page": 1,
    }
    try:
        resp = requests.post(
            "https://api.apollo.io/v1/mixed_companies/search",
            headers=headers, json=body_startup, timeout=30,
        )
        resp.raise_for_status()
        startup_list = resp.json().get("organizations") or resp.json().get("accounts") or []
        log.info(f"Apollo startup: {len(startup_list)} trovate")
        risultati.extend(startup_list)
    except Exception as e:
        log.warning(f"Apollo ricerca startup fallita: {e}")

    time.sleep(1)

    # Ricerca 2: PMI mature con segnali di crescita (job posting attivi)
    body_pmi = {
        "organization_locations":            TARGET_COUNTRIES,
        "organization_industries":           TARGET_INDUSTRIES,
        "organization_num_employees_ranges": ["20,200"],
        "per_page": LEADS_PER_RUN // 2,
        "page": 1,
    }
    try:
        resp = requests.post(
            "https://api.apollo.io/v1/mixed_companies/search",
            headers=headers, json=body_pmi, timeout=30,
        )
        resp.raise_for_status()
        pmi_list = resp.json().get("organizations") or resp.json().get("accounts") or []
        log.info(f"Apollo PMI: {len(pmi_list)} trovate")
        # Evita duplicati per nome
        nomi_esistenti = {r.get("name") for r in risultati}
        risultati.extend([p for p in pmi_list if p.get("name") not in nomi_esistenti])
    except Exception as e:
        log.warning(f"Apollo ricerca PMI fallita: {e}")

    log.info(f"Totale aziende candidate: {len(risultati)}")
    return risultati


# ---------------------------------------------------------------------------
# Claude — enrichment consulenziale
# ---------------------------------------------------------------------------
def enrichment_acm(azienda: dict) -> dict:
    client = anthropic.Anthropic(api_key=CLAUDE_KEY)

    funding = azienda.get("latest_funding_stage") or azienda.get("funding_stage", "non disponibile")
    founded = azienda.get("founded_year", "N/D")

    prompt = f"""Sei un partner di una società di consulenza manageriale (ACM&Partners).
Ti viene presentata un'azienda italiana da valutare come potenziale cliente.
ACM offre: consulenza strategica, supporto M&A, analisi di mercato, piani di crescita,
affiancamento manageriale, business plan per aziende 10-200 dipendenti.

AZIENDA:
Nome: {azienda.get("name", "")}
Settore: {azienda.get("industry", "")}
Dipendenti: {azienda.get("num_employees", "N/D")}
Città: {azienda.get("city", "")}, {azienda.get("state", "")}
Anno fondazione: {founded}
Ultimo round: {funding}
Descrizione: {azienda.get("short_description", "N/D")}
Sito: {azienda.get("website_url", "")}

Rispondi ESCLUSIVAMENTE con JSON valido:
{{
  "referente": "ruolo del decisore ideale da contattare (es. CEO, Founder, CFO, Titolare)",
  "tipo_cliente": "startup" | "pmi_crescita" | "pmi_matura" | "non_pertinente",
  "bisogno_principale": "il problema/opportunità più probabile (es. espansione, M&A, ottimizzazione costi, internazionalizzazione)",
  "servizio_acm": "quale servizio ACM è più adatto (es. piano strategico, due diligence, affiancamento CEO)",
  "score": intero 0-5,
  "angolo_approccio": "una frase di apertura personalizzata per email fredda",
  "note": "massimo 2 righe di contesto utile per il commerciale ACM"
}}

Score ACM:
5 = startup con funding recente O PMI in fase di M&A/espansione evidente
4 = PMI in crescita con team piccolo, bisogno di struttura manageriale
3 = PMI consolidata che potrebbe beneficiare di consulenza strategica
2 = azienda stabile senza segnali di bisogno immediato
1 = troppo grande o troppo piccola per ACM
0 = non pertinente (multinazionale, PA, associazione, no-profit)"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=450,
        messages=[{"role": "user", "content": prompt}],
    )
    testo = msg.content[0].text.strip()
    start = testo.find("{")
    end   = testo.rfind("}") + 1
    return json.loads(testo[start:end])


# ---------------------------------------------------------------------------
# Pipeline principale
# ---------------------------------------------------------------------------
def run() -> dict:
    log.info("=" * 55)
    log.info("  AGENTE ACM&PARTNERS — Lead Generation")
    log.info("  Target: PMI italiane in crescita + startup")
    log.info("=" * 55)

    stats = {"analizzate": 0, "scartate": 0, "create_hub": 0, "errori": 0}

    hub_token = hub_login()
    aziende   = apollo_cerca_aziende_acm()

    for az in aziende:
        nome = az.get("name", "N/D")
        stats["analizzate"] += 1
        log.info(f"\n[{stats['analizzate']}/{len(aziende)}] {nome}")

        try:
            enrich = enrichment_acm(az)

            if enrich.get("score", 0) <= 1 or enrich.get("tipo_cliente") == "non_pertinente":
                log.info(f"  → SCARTATA: score={enrich.get('score')} tipo={enrich.get('tipo_cliente')}")
                stats["scartate"] += 1
                continue

            note_completa = (
                f"[{enrich.get('tipo_cliente')}] "
                f"Bisogno: {enrich.get('bisogno_principale')} | "
                f"Servizio: {enrich.get('servizio_acm')} | "
                f"Approccio: {enrich.get('angolo_approccio')} | "
                f"{enrich.get('note', '')}"
            )

            dati_lead = {
                "name":      nome,
                "email":     az.get("primary_email") or az.get("email", ""),
                "phone":     az.get("primary_phone") or az.get("phone", ""),
                "city":      az.get("city", ""),
                "province":  az.get("state", "")[:2].upper() if az.get("state") else "",
                "referente": enrich.get("referente", ""),
                "score":     enrich.get("score", 3),
                "note":      note_completa,
            }

            hub_crea_lead(hub_token, dati_lead)
            stats["create_hub"] += 1
            log.info(f"  → LEAD CREATO ✅  score={enrich.get('score')} | {enrich.get('bisogno_principale')}")

        except Exception as e:
            log.error(f"  Errore: {e}")
            stats["errori"] += 1

        time.sleep(1.2)

    log.info("\n" + "=" * 55)
    log.info("  RIEPILOGO ACM")
    log.info(f"  Analizzate:        {stats['analizzate']}")
    log.info(f"  Scartate:          {stats['scartate']}")
    log.info(f"  Lead creati Hub:   {stats['create_hub']}")
    log.info(f"  Errori:            {stats['errori']}")
    log.info("=" * 55)
    return stats


if __name__ == "__main__":
    run()
