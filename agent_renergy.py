"""
agent_renergy.py — Lead Generation per Renergy Project&Build
Pipeline: Apollo → Google Maps Satellite → Claude Vision → Hub CRM

Secrets Replit da configurare (icona lucchetto):
  APOLLO_API_KEY      → API key Apollo.io
  CLAUDE_API_KEY      → API key Anthropic
  GOOGLE_MAPS_KEY     → API key Google Maps Static API
  HUB_EMAIL           → email login Hub CRM
  HUB_PASSWORD        → password Hub CRM
"""

import os
import time
import base64
import json
import logging
import requests
import anthropic
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
APOLLO_KEY  = os.environ.get("APOLLO_API_KEY")
CLAUDE_KEY  = os.environ.get("CLAUDE_API_KEY")
MAPS_KEY    = os.environ.get("GOOGLE_MAPS_KEY")
HUB_EMAIL   = os.environ.get("HUB_EMAIL")
HUB_PASSWORD = os.environ.get("HUB_PASSWORD")
HUB_BASE    = "https://hub.renergygroup.it/api"

TARGET_LOCATIONS = [
    "Casier, Treviso, Veneto, Italy",
    "Silea, Treviso, Veneto, Italy",
]
TARGET_INDUSTRIES = [
    "manufacturing", "industrial", "logistics",
    "agriculture", "construction", "food and beverages",
    "wholesale", "automotive",
]
LEADS_PER_RUN   = 25
MIN_EMPLOYEES   = 10
MAX_EMPLOYEES   = 500
SOLAR_THRESHOLD = 60   # confidence minima per scartare un'azienda come "già ha solare"


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
    token = resp.json().get("token")
    log.info("Hub login OK")
    return token


def hub_crea_lead(token: str, dati: dict) -> dict:
    prossimo_lunedi = (
        datetime.now() + timedelta(days=(7 - datetime.now().weekday()) % 7 or 7)
    ).strftime("%Y-%m-%d")

    payload = {
        "ragione_sociale":      dati.get("name", ""),
        "referente_nome":       dati.get("referente", ""),
        "email":                dati.get("email", ""),
        "telefono":             dati.get("phone", ""),
        "citta":                dati.get("city", ""),
        "provincia":            dati.get("province", "TV"),
        "fonte":                "apollo_agente_automatico",
        "tipo_impianto":        "fotovoltaico",
        "kwp_stimati":          dati.get("kwp_stimati", 0),
        "valore_stimato":       dati.get("valore_stimato", 0),
        "score":                dati.get("score", 0),
        "note":                 dati.get("note", ""),
        "data_prossimo_followup": prossimo_lunedi,
    }
    # Rimuovi campi vuoti per non sovrascrivere defaults Hub
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
# Apollo
# ---------------------------------------------------------------------------
def apollo_cerca_aziende() -> list[dict]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": APOLLO_KEY,
    }
    body = {
        "q_organization_locations":          TARGET_LOCATIONS,
        "organization_industries":           TARGET_INDUSTRIES,
        "organization_num_employees_ranges": [f"{MIN_EMPLOYEES},{MAX_EMPLOYEES}"],
        "per_page": LEADS_PER_RUN,
        "page":     1,
    }
    resp = requests.post(
        "https://api.apollo.io/v1/mixed_companies/search",
        headers=headers,
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    # Apollo restituisce il risultato in chiavi diverse a seconda della versione
    aziende = data.get("organizations") or data.get("accounts") or []
    log.info(f"Apollo: {len(aziende)} aziende trovate")
    return aziende


# ---------------------------------------------------------------------------
# Google Maps Static API → immagine satellite
# ---------------------------------------------------------------------------
def get_satellite_b64(indirizzo: str) -> str | None:
    if not indirizzo or not MAPS_KEY:
        return None
    params = {
        "center":   indirizzo,
        "zoom":     19,
        "size":     "640x640",
        "maptype":  "satellite",
        "key":      MAPS_KEY,
    }
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/staticmap",
        params=params,
        timeout=15,
    )
    if resp.status_code != 200 or len(resp.content) < 5_000:
        log.warning(f"Immagine satellite non disponibile per: {indirizzo}")
        return None
    return base64.standard_b64encode(resp.content).decode("utf-8")


# ---------------------------------------------------------------------------
# Claude Vision — analisi tetto satellite
# ---------------------------------------------------------------------------
def analizza_tetto(image_b64: str, nome_azienda: str) -> dict:
    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    prompt = f"""Sei un analista di immagini satellitari specializzato in energia solare.
Stai analizzando il tetto dell'edificio: {nome_azienda}

Rispondi ESCLUSIVAMENTE con JSON valido, senza altro testo:
{{
  "solar_panels": true o false,
  "confidence": numero 0-100,
  "roof_type": "capannone_industriale" | "ufficio" | "misto" | "residenziale" | "non_identificabile",
  "roof_area": "piccola" | "media" | "grande",
  "lead_quality": "alta" | "media" | "bassa" | "non_pertinente",
  "kwp_stimati": numero intero,
  "valore_stimato": numero intero,
  "note": "una frase breve descrittiva"
}}

Regole:
- solar_panels true = superfici blu/nere rettangolari in file regolari sul tetto
- confidence < 40 se immagine poco chiara o zoom insufficiente
- roof_area: piccola <500mq, media 500-2000mq, grande >2000mq
- kwp_stimati: piccola→50, media→150, grande→400 (aggiusta in base a quanto vedi)
- valore_stimato = kwp_stimati * 1200
- lead_quality alta = capannone grande SENZA pannelli solari = target perfetto Renergy
- lead_quality non_pertinente = residenziale, parcheggio, terreno libero"""

    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type":       "base64",
                        "media_type": "image/png",
                        "data":       image_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    testo = msg.content[0].text.strip()
    start = testo.find("{")
    end   = testo.rfind("}") + 1
    return json.loads(testo[start:end])


# ---------------------------------------------------------------------------
# Claude Testo — enrichment con dati Apollo (fallback e score commerciale)
# ---------------------------------------------------------------------------
def enrichment_testo(azienda: dict) -> dict:
    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    prompt = f"""Sei un commerciale esperto di fotovoltaico industriale in Italia.
Analizza questa azienda e rispondi ESCLUSIVAMENTE con JSON valido:

Nome: {azienda.get("name", "")}
Settore: {azienda.get("industry", "")}
Dipendenti: {azienda.get("num_employees", "N/D")}
Città: {azienda.get("city", "")}
Descrizione: {azienda.get("short_description", "N/D")}
Sito: {azienda.get("website_url", "")}

{{
  "referente": "titolo del decisore probabile (es. Titolare, Resp. Acquisti, CEO)",
  "kwp_stimati": numero intero,
  "valore_stimato": numero intero,
  "score": intero 0-5,
  "note": "frase di approccio personalizzata per proposta fotovoltaico"
}}

Score: 5=manifatturiero >50dip, 4=agricoltura/logistica, 3=commercio fisico,
       2=piccola impresa generica, 1=uffici/professionale, 0=non pertinente"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
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
    log.info("  AGENTE RENERGY — Lead Generation Automatica")
    log.info(f"  Zone: {', '.join(l.split(',')[0] for l in TARGET_LOCATIONS)}")
    log.info("=" * 55)

    stats = {"analizzate": 0, "scartate_solare": 0, "scartate_irrilevanti": 0,
             "create_hub": 0, "errori": 0, "valore_totale": 0}

    hub_token = hub_login()
    aziende   = apollo_cerca_aziende()

    for az in aziende:
        nome      = az.get("name", "N/D")
        indirizzo = ", ".join(filter(None, [
            az.get("street_address", ""),
            az.get("city", ""),
            az.get("state", ""),
            "Italy",
        ]))
        stats["analizzate"] += 1
        log.info(f"\n[{stats['analizzate']}/{len(aziende)}] {nome}")

        try:
            analisi_visiva = None
            img_b64 = get_satellite_b64(indirizzo)

            if img_b64:
                analisi_visiva = analizza_tetto(img_b64, nome)
                solar   = analisi_visiva.get("solar_panels", False)
                conf    = analisi_visiva.get("confidence", 0)
                quality = analisi_visiva.get("lead_quality", "")

                log.info(f"  Vision → solar={solar} conf={conf}% quality={quality}")

                if solar and conf >= SOLAR_THRESHOLD:
                    log.info("  → SCARTATA: ha già pannelli solari")
                    stats["scartate_solare"] += 1
                    continue

                if quality == "non_pertinente":
                    log.info("  → SCARTATA: edificio non pertinente")
                    stats["scartate_irrilevanti"] += 1
                    continue
            else:
                log.info("  Vision → immagine non disponibile, procedo con testo")

            # Enrichment commerciale (sempre)
            enrich = enrichment_testo(az)

            if enrich.get("score", 0) == 0:
                log.info("  → SCARTATA: score 0, non pertinente")
                stats["scartate_irrilevanti"] += 1
                continue

            # Scegli kwp/valore: priorità Vision, fallback testo
            kwp    = (analisi_visiva or {}).get("kwp_stimati") or enrich.get("kwp_stimati", 100)
            valore = kwp * 1200

            note_visiva = f"Tetto: {analisi_visiva['note']}" if analisi_visiva else "Analisi visiva non disponibile"

            dati_lead = {
                "name":           nome,
                "email":          az.get("primary_email") or az.get("email", ""),
                "phone":          az.get("primary_phone") or az.get("phone", ""),
                "city":           az.get("city", ""),
                "province":       "TV",
                "referente":      enrich.get("referente", ""),
                "kwp_stimati":    kwp,
                "valore_stimato": valore,
                "score":          enrich.get("score", 3),
                "note":           f"{enrich.get('note', '')} | {note_visiva}",
            }

            hub_crea_lead(hub_token, dati_lead)
            stats["create_hub"]    += 1
            stats["valore_totale"] += valore
            log.info(f"  → LEAD CREATO ✅  {kwp} kWp — €{valore:,}")

        except Exception as e:
            log.error(f"  Errore: {e}")
            stats["errori"] += 1

        time.sleep(1.5)  # rispetta rate limit API

    log.info("\n" + "=" * 55)
    log.info("  RIEPILOGO")
    log.info(f"  Analizzate:            {stats['analizzate']}")
    log.info(f"  Scartate (solare):     {stats['scartate_solare']}")
    log.info(f"  Scartate (irrilevanti):{stats['scartate_irrilevanti']}")
    log.info(f"  Lead creati in Hub:    {stats['create_hub']}")
    log.info(f"  Valore pipeline:       €{stats['valore_totale']:,}")
    log.info(f"  Errori:                {stats['errori']}")
    log.info("=" * 55)
    return stats


if __name__ == "__main__":
    run()
