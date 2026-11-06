"""
hub_client.py — Client condiviso per Hub CRM (hub.renergygroup.it)
Centralizza login JWT e lettura lead/clienti, riutilizzato dagli agenti che hanno
bisogno di consultare lo stato dei clienti esistenti (cross-sell, upsell, prospecting).
"""

import os, logging
from datetime import datetime
import requests

log = logging.getLogger(__name__)

HUB_BASE     = "https://hub.renergygroup.it/api"
HUB_EMAIL    = os.environ.get("HUB_EMAIL")
HUB_PASSWORD = os.environ.get("HUB_PASSWORD")

_token_cache = {"token": None, "scaduto_il": 0}


def hub_token() -> str | None:
    """Login JWT con cache (il token Hub dura ~7 giorni)."""
    now = datetime.now().timestamp()
    if _token_cache["token"] and now < _token_cache["scaduto_il"]:
        return _token_cache["token"]
    if not HUB_EMAIL or not HUB_PASSWORD:
        return None
    try:
        r = requests.post(f"{HUB_BASE}/auth/login",
                          json={"email": HUB_EMAIL, "password": HUB_PASSWORD}, timeout=10)
        r.raise_for_status()
        token = r.json().get("token")
        _token_cache["token"]      = token
        _token_cache["scaduto_il"] = now + 6 * 86400
        return token
    except Exception as e:
        log.warning(f"Hub login fallito: {e}")
        return None


def get_lead(filtri: dict | None = None, n: int = 100) -> list[dict]:
    """Recupera lead/clienti dal CRM, con fallback su più endpoint possibili."""
    token = hub_token()
    if not token:
        return []
    params = {"limit": n, "orderBy": "created_at", "order": "desc"}
    if filtri:
        params.update(filtri)
    for endpoint in ["/crm/lead", "/crm/leads", "/crm/lead/list"]:
        try:
            r = requests.get(f"{HUB_BASE}{endpoint}",
                             headers={"Authorization": f"Bearer {token}"},
                             params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("leads", data.get("data", []))
        except Exception:
            continue
    return []


def get_clienti_per_azienda(azienda: str, stato: str | None = None) -> list[dict]:
    """Lead/clienti filtrati per azienda (e opzionalmente per stato es. 'cliente', 'installato')."""
    filtri = {"azienda": azienda}
    if stato:
        filtri["stato"] = stato
    leads = get_lead(filtri, n=200)
    # Filtro applicativo di sicurezza (l'endpoint potrebbe ignorare i filtri)
    risultato = []
    for l in leads:
        if azienda and azienda.lower() not in str(l.get("azienda", l.get("ragione_sociale", ""))).lower() \
           and azienda.lower() not in str(l.get("fonte", "")).lower():
            # Se il record non riporta esplicitamente l'azienda, includilo comunque
            # (molti CRM single-tenant non hanno questo campo)
            pass
        if stato and str(l.get("stato", "")).lower() != stato.lower():
            continue
        risultato.append(l)
    return risultato


def aggiorna_stato_lead(lead_id: str, stato: str) -> bool:
    token = hub_token()
    if not token:
        return False
    try:
        r = requests.patch(f"{HUB_BASE}/crm/lead/{lead_id}/stato",
                           headers={"Authorization": f"Bearer {token}"},
                           json={"stato": stato}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        log.warning(f"Hub — aggiornamento stato lead {lead_id} fallito: {e}")
        return False
