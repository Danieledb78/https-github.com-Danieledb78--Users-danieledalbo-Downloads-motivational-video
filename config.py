"""
config.py — Costanti di configurazione centralizzate AIOS v2.0
Compilare i placeholder con valori reali prima del deploy in produzione.
"""

# ---------------------------------------------------------------------------
# Referral / incentivi clienti — DA DEFINIRE DA DANIELE
# ---------------------------------------------------------------------------
REFERRAL_INCENTIVE_RENERGY = "INSERIRE: es. buono Amazon 200€ per ogni cliente installato"
REFERRAL_INCENTIVE_RSGAS   = "INSERIRE: es. 1 mese di energia gratis per ogni cliente attivato"

# ---------------------------------------------------------------------------
# Soglie e parametri di scoring
# ---------------------------------------------------------------------------
SOLAR_THRESHOLD     = 60   # % confidenza minima per classificare un tetto come solar-ready
LEAD_SCORE_MIN_ACM  = 2    # score minimo (0-5) per salvare un lead ACM nel CRM

TARIFFA_ALERT_GAP_PERCENT = 8   # % di gap tariffario che triggera alert immediato RS Gas

UPSELL_MONTHS_RENERGY  = 18   # mesi da installazione per proposta ampliamento
NPS_REQUEST_MONTHS     = 8    # mesi da installazione per richiesta NPS
NPS_REFERRAL_THRESHOLD = 7    # NPS minimo per attivare email referral

# ---------------------------------------------------------------------------
# Deploy / API
# ---------------------------------------------------------------------------
LANDING_VERIFY_DELAY_SEC = 30   # secondi di attesa prima di verificare il deploy FTPS
API_RETRY_MAX            = 2    # numero massimo di retry su errori 529/overload
API_TIMEOUT_SEC          = 60   # timeout (secondi) per le chiamate a Claude API

# ---------------------------------------------------------------------------
# Brand standard — colori e font per azienda (usati da agent_landing, agent_leadmagnet_acm)
# ---------------------------------------------------------------------------
BRAND = {
    "Renergy": {
        "primario":   "#1B5E20",
        "secondario": "#FF6F00",
        "font_titoli": "Inter",
        "font_corpo":  "Inter",
        "google_fonts": "Inter:wght@400;600;700",
    },
    "RS_Gas": {
        "primario":   "#1565C0",
        "secondario": "#00ACC1",
        "font_titoli": "Inter",
        "font_corpo":  "Inter",
        "google_fonts": "Inter:wght@400;600;700",
    },
    "ACM": {
        "primario":   "#2E5902",
        "secondario": "#8D6E1E",
        "font_titoli": "Cormorant Garamond",
        "font_corpo":  "DM Sans",
        "google_fonts": "Cormorant+Garamond:wght@500;700&family=DM+Sans:wght@400;500",
    },
}

# ---------------------------------------------------------------------------
# Tono di voce — riferimento rapido per i prompt Claude
# ---------------------------------------------------------------------------
TONO_VOCE = {
    "Renergy": (
        "Tecnico-fiduciario, concreto, orientato al ROI. Usa numeri reali (kWp, kWh, anni payback, "
        "€ risparmio/anno), casi concreti, urgency su incentivi con scadenza reale, frasi brevi. "
        "NON usare 'sostenibilità' come parola vuota, superlativi senza prove, gergo tecnico non spiegato."
    ),
    "RS_Gas": (
        "Diretto, pragmatico, orientato al risparmio immediato. Usa confronto prima/dopo bolletta, "
        "semplicità del processo (firma -> attivo in pochi giorni, zero burocrazia), garanzie concrete. "
        "NON usare terminologia tecnica ARERA (PUN, PSV, dispacciamento), confronti diretti con competitor "
        "per nome, promesse non quantificate."
    ),
    "ACM": (
        "Autorevole, consulenziale, europeo nel riferimento normativo, italiano nella praticità. "
        "Usa riferimenti normativi precisi con date (CSRD, ESRS, EU Taxonomy), thought leadership, "
        "urgency normativa con scadenze reali e conseguenze concrete, struttura problema -> impatto -> "
        "soluzione -> prossimo passo. NON usare tono allarmistico, affermazioni generiche non supportate."
    ),
}

# ---------------------------------------------------------------------------
# Filelock — directory dei lock file per gli accessi concorrenti ai JSON
# ---------------------------------------------------------------------------
import os
FILELOCK_PATH = os.environ.get("FILELOCK_PATH", "/tmp/aios_locks/")
