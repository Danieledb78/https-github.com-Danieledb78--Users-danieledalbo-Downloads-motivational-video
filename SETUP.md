# Setup Agenti AI — Guida Rapida

## 1. Secrets Replit (icona lucchetto nel menu laterale)

| Secret | Dove trovarlo |
|--------|---------------|
| `APOLLO_API_KEY` | apollo.io → Settings → Integrations → API Keys |
| `CLAUDE_API_KEY` | console.anthropic.com → API Keys |
| `GOOGLE_MAPS_KEY` | Vedi istruzioni sotto |
| `HUB_EMAIL` | la tua email Hub Renergy |
| `HUB_PASSWORD` | password Hub Renergy |
| `ACM_HUB_BASE` | URL Hub ACM (se diverso da Renergy) |
| `ACM_HUB_EMAIL` | email Hub ACM (se diverso) |
| `ACM_HUB_PASSWORD` | password Hub ACM (se diverso) |
| `TELEGRAM_BOT_TOKEN` | Vedi istruzioni sotto |
| `TELEGRAM_CHAT_ID` | Vedi istruzioni sotto |

---

## 2. Google Maps Static API (5 minuti)

1. Vai su https://console.cloud.google.com
2. Crea un nuovo progetto (es. "Renergy Agenti")
3. Menu → APIs & Services → Enable APIs
4. Cerca "Maps Static API" → Enable
5. Menu → APIs & Services → Credentials → Create Credentials → API Key
6. Copia la chiave → incollala in Replit Secrets come `GOOGLE_MAPS_KEY`
7. (Opzionale) Imposta restrizioni: solo Maps Static API, solo dal tuo IP Replit

**Costo:** $0 fino a $200/mese di utilizzo (~100.000 immagini). Per 25 lead/settimana = ~$0.05/mese.

---

## 3. Telegram Bot (10 minuti)

### Crea il bot:
1. Apri Telegram → cerca @BotFather → /newbot
2. Scegli un nome (es. "Renergy AI Agent")
3. Copia il token → `TELEGRAM_BOT_TOKEN`

### Ottieni il tuo Chat ID:
1. Manda un messaggio qualsiasi al tuo nuovo bot
2. Vai su: https://api.telegram.org/bot{IL_TUO_TOKEN}/getUpdates
3. Copia il valore `"id"` dentro `"chat"` → `TELEGRAM_CHAT_ID`

---

## 4. Installazione dipendenze (Shell Replit)

```bash
pip install -r requirements.txt
```

---

## 5. Test progressivi

```bash
# Test 1: connessione Hub CRM
python test_hub_api.py

# Test 2: solo agente Renergy
python agent_renergy.py

# Test 3: solo agente ACM
python agent_acm.py

# Produzione: orchestratore completo
python orchestrator.py
```

---

## 6. Schedulazione su Replit

Nel file `pyproject.toml` o tramite Replit Deployments:
- Usa **Scheduled Deployments** per lanciare `orchestrator.py` ogni lunedì alle 07:00
- Oppure usa un endpoint HTTP e un cron esterno (cron-job.org è gratuito)
