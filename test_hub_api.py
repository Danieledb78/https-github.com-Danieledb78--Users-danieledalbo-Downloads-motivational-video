"""
Test script: verifica connessione Hub CRM + crea lead di test
Da usare su Replit con variabili d'ambiente configurate.

Variabili d'ambiente necessarie (Replit Secrets):
  HUB_EMAIL    → email account Hub
  HUB_PASSWORD → password Hub
  HUB_BASE_URL → https://hub.renergygroup.it/api
"""

import os
import requests
import json
from datetime import datetime, timedelta


HUB_BASE = os.environ.get("HUB_BASE_URL", "https://hub.renergygroup.it/api")
HUB_EMAIL = os.environ.get("HUB_EMAIL")
HUB_PASSWORD = os.environ.get("HUB_PASSWORD")


def login() -> str:
    """Ottieni JWT token dal Hub CRM."""
    resp = requests.post(
        f"{HUB_BASE}/auth/login",
        json={"email": HUB_EMAIL, "password": HUB_PASSWORD},
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token")
    utente = data.get("user", {}).get("nome", "?")
    print(f"✅ Login OK — utente: {utente}")
    return token


def crea_lead_test(token: str) -> dict:
    """Crea un lead di test nella pipeline Hub."""
    prossimo_lunedi = (datetime.now() + timedelta(days=(7 - datetime.now().weekday()))).strftime("%Y-%m-%d")

    payload = {
        "ragione_sociale": "TEST AUTOMATICO - Cancellare",
        "referente_nome": "Agente AI Test",
        "email": "test@agente-ai.test",
        "telefono": "+39 000 0000000",
        "citta": "Milano",
        "provincia": "MI",
        "fonte": "apollo_automatico",
        "tipo_impianto": "fotovoltaico",
        "kwp_stimati": 150,
        "valore_stimato": 180000,
        "score": 4,
        "note": "Lead generato automaticamente dallo script di test agenti AI. Da cancellare.",
        "data_prossimo_followup": prossimo_lunedi
    }

    resp = requests.post(
        f"{HUB_BASE}/crm/lead",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=10
    )
    resp.raise_for_status()
    lead = resp.json()
    lead_id = lead.get("id") or lead.get("lead", {}).get("id")
    print(f"✅ Lead creato — ID: {lead_id}")
    print(json.dumps(lead, indent=2, ensure_ascii=False))
    return lead


def aggiorna_stato(token: str, lead_id: int, stato: str):
    """Testa il cambio di stato nella pipeline."""
    resp = requests.patch(
        f"{HUB_BASE}/crm/lead/{lead_id}/stato",
        headers={"Authorization": f"Bearer {token}"},
        json={"stato": stato},
        timeout=10
    )
    resp.raise_for_status()
    print(f"✅ Stato aggiornato → {stato}")
    return resp.json()


if __name__ == "__main__":
    print("\n=== TEST CONNESSIONE HUB CRM ===\n")

    if not HUB_EMAIL or not HUB_PASSWORD:
        print("❌ Variabili HUB_EMAIL e HUB_PASSWORD non configurate.")
        print("   Aggiungile come Secrets in Replit.")
        exit(1)

    try:
        token = login()
        lead = crea_lead_test(token)

        lead_id = lead.get("id") or lead.get("lead", {}).get("id")
        if lead_id:
            aggiorna_stato(token, lead_id, "contattato")

        print("\n✅ TUTTI I TEST PASSATI — Hub API funziona correttamente.")
        print(f"   Ricordati di cancellare il lead di test ID {lead_id} da Hub.")

    except requests.HTTPError as e:
        print(f"❌ Errore HTTP: {e.response.status_code} — {e.response.text}")
    except Exception as e:
        print(f"❌ Errore: {e}")
