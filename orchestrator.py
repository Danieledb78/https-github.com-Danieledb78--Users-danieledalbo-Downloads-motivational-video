"""
orchestrator.py — Orchestratore centrale multi-azienda
Lancia gli agenti, aggrega i risultati, invia il briefing mattutino.

Notifiche supportate (configura almeno una):
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID  → messaggio Telegram
  NOTIFY_EMAIL                           → email (richiede config SMTP)

Esecuzione manuale:    python orchestrator.py
Schedulazione Replit:  usa il task scheduler integrato
                       oppure aggiungi un endpoint /run e chiama con cron esterno
"""

import os
import logging
from datetime import datetime

import agent_renergy
import agent_acm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# Notifiche
# ---------------------------------------------------------------------------
def invia_telegram(messaggio: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Telegram non configurato — stampo solo su console")
        return False
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       messaggio,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Briefing inviato su Telegram ✅")
        return True
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


def componi_briefing(ora: str, stats_renergy: dict, stats_acm: dict) -> str:
    valore_fmt = f"€{stats_renergy.get('valore_totale', 0):,}"
    return f"""🤖 *Briefing Agenti AI — {ora}*

⚡ *Renergy Project&Build*
• Lead creati: {stats_renergy.get('create_hub', 0)}
• Pipeline generata: {valore_fmt}
• Scartati (hanno solare): {stats_renergy.get('scartate_solare', 0)}
• Errori: {stats_renergy.get('errori', 0)}

🏢 *ACM&Partners*
• Lead creati: {stats_acm.get('create_hub', 0)}
• Scartati (non pertinenti): {stats_acm.get('scartate', 0)}
• Errori: {stats_acm.get('errori', 0)}

📊 *Totale lead oggi: {stats_renergy.get('create_hub', 0) + stats_acm.get('create_hub', 0)}*
Vai su Hub per revisione e approvazione."""


# ---------------------------------------------------------------------------
# Runner principale
# ---------------------------------------------------------------------------
def run():
    ora = datetime.now().strftime("%d/%m/%Y %H:%M")
    log.info(f"\n{'='*55}")
    log.info(f"  ORCHESTRATORE AVVIATO — {ora}")
    log.info(f"{'='*55}\n")

    # --- Renergy ---
    log.info(">>> Avvio agent_renergy...")
    try:
        stats_renergy = agent_renergy.run()
    except Exception as e:
        log.error(f"agent_renergy fallito: {e}")
        stats_renergy = {"create_hub": 0, "valore_totale": 0,
                         "scartate_solare": 0, "errori": 1}

    # --- ACM ---
    log.info("\n>>> Avvio agent_acm...")
    try:
        stats_acm = agent_acm.run()
    except Exception as e:
        log.error(f"agent_acm fallito: {e}")
        stats_acm = {"create_hub": 0, "scartate": 0, "errori": 1}

    # --- Briefing ---
    briefing = componi_briefing(ora, stats_renergy, stats_acm)
    log.info(f"\n{briefing}")
    invia_telegram(briefing)

    return {"renergy": stats_renergy, "acm": stats_acm}


if __name__ == "__main__":
    run()
