"""
agent_risposte_email.py — Agente Risposte Email Campagne
Legge la posta in arrivo degli account configurati,
identifica risposte alle campagne outbound, classifica l'interesse,
suggerisce la risposta e gestisce gli opt-out.
"""

import os, json, imaplib, email, logging
from email.header import decode_header
from datetime import datetime, timedelta
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude = anthropic.Anthropic(api_key=CLAUDE_KEY)

RISPOSTE_FILE = "risposte_campagne.json"

# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------
def carica_imap_config() -> list[dict]:
    """Legge le configurazioni IMAP dagli stessi secrets delle email SMTP."""
    accounts = []
    for i in range(1, 10):
        label = os.environ.get(f"EMAIL_{i}_LABEL")
        if not label:
            break
        host_imap = os.environ.get(f"EMAIL_{i}_IMAP_HOST")
        if not host_imap:
            continue  # account senza IMAP configurato — salta
        accounts.append({
            "label":    label,
            "address":  os.environ.get(f"EMAIL_{i}_ADDRESS", ""),
            "password": os.environ.get(f"EMAIL_{i}_PASSWORD", ""),
            "host":     host_imap,
            "port":     int(os.environ.get(f"EMAIL_{i}_IMAP_PORT", "993")),
        })
    return accounts


def leggi_email_recenti(account: dict, giorni: int = 3) -> list[dict]:
    """Connette via IMAP e recupera le email non lette degli ultimi N giorni."""
    messaggi = []
    try:
        conn = imaplib.IMAP4_SSL(account["host"], account["port"])
        conn.login(account["address"], account["password"])
        conn.select("INBOX")

        data_da = (datetime.now() - timedelta(days=giorni)).strftime("%d-%b-%Y")
        _, nums = conn.search(None, f'(SINCE "{data_da}" UNSEEN)')
        if not nums[0]:
            conn.logout()
            return []

        for num in nums[0].split()[-30:]:   # max 30 email
            _, data = conn.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            def decode_str(s):
                if not s:
                    return ""
                parts = decode_header(s)
                result = ""
                for part, charset in parts:
                    if isinstance(part, bytes):
                        result += part.decode(charset or "utf-8", errors="replace")
                    else:
                        result += part
                return result

            corpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        corpo = part.get_payload(decode=True).decode("utf-8", errors="replace")[:1000]
                        break
            else:
                corpo = msg.get_payload(decode=True).decode("utf-8", errors="replace")[:1000]

            messaggi.append({
                "account":  account["label"],
                "da":       decode_str(msg.get("From", "")),
                "oggetto":  decode_str(msg.get("Subject", "")),
                "data":     msg.get("Date", ""),
                "corpo":    corpo.strip(),
            })

        conn.logout()
    except Exception as e:
        log.warning(f"IMAP {account['label']}: {e}")
    return messaggi


# ---------------------------------------------------------------------------
# Classificazione Claude
# ---------------------------------------------------------------------------
PROMPT_CLASSIFICA = """Sei l'assistente di un imprenditore italiano che gestisce campagne di cold email.
Analizza questa risposta email e classifica l'interesse.

Email ricevuta:
DA: {da}
OGGETTO: {oggetto}
CORPO:
{corpo}

Rispondi SOLO con JSON valido:
{{
  "classificazione": "interessato | non_interessato | stop | richiesta_info | neutro | spam",
  "motivo": "una riga con la motivazione",
  "prossimo_passo": "cosa fare (es: fissa chiamata, invia preventivo, rimuovi dalla lista)",
  "urgenza": "alta | media | bassa",
  "risposta_suggerita": "bozza di risposta in italiano (max 80 parole), oppure null se stop/spam"
}}"""

def classifica_risposta(msg: dict) -> dict:
    content = PROMPT_CLASSIFICA.format(
        da=msg["da"], oggetto=msg["oggetto"], corpo=msg["corpo"][:600]
    )
    res = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=600,
        messages=[{"role": "user", "content": content}],
    )
    t = res.content[0].text.strip()
    analisi = json.loads(t[t.find("{"):t.rfind("}")+1])
    return {**msg, **analisi}


# ---------------------------------------------------------------------------
# Gestione opt-out in campagne
# ---------------------------------------------------------------------------
def processa_opt_out(email_addr: str):
    """Marca l'email come opt-out in tutte le campagne attive."""
    try:
        from campagna_manager import carica_campagne, salva_campagne
        campagne = carica_campagne()
        modificate = 0
        for camp in campagne.values():
            for lead in camp.get("lead", []):
                if lead.get("email","").lower() == email_addr.lower() and lead.get("stato") != "opt_out":
                    lead["stato"] = "opt_out"
                    camp["stats"]["opt_out"] = camp["stats"].get("opt_out", 0) + 1
                    modificate += 1
        if modificate:
            salva_campagne(campagne)
            log.info(f"Opt-out registrato per {email_addr} in {modificate} campagne")
    except Exception as e:
        log.warning(f"Errore opt-out: {e}")


# ---------------------------------------------------------------------------
# Storage risposte
# ---------------------------------------------------------------------------
def salva_risposte(risposte: list[dict]):
    existing = []
    if Path(RISPOSTE_FILE).exists():
        with open(RISPOSTE_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    # Deduplication su (da, oggetto, data)
    chiavi = {(r["da"], r["oggetto"], r["data"]) for r in existing}
    nuove  = [r for r in risposte if (r["da"], r["oggetto"], r["data"]) not in chiavi]
    if nuove:
        existing.extend(nuove)
        with open(RISPOSTE_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    return nuove


def get_risposte_da_gestire() -> list[dict]:
    if not Path(RISPOSTE_FILE).exists():
        return []
    with open(RISPOSTE_FILE, encoding="utf-8") as f:
        risposte = json.load(f)
    return [r for r in risposte if not r.get("gestita") and r.get("classificazione") not in ("spam",)]


def segna_gestita(da: str, oggetto: str):
    if not Path(RISPOSTE_FILE).exists():
        return
    with open(RISPOSTE_FILE, encoding="utf-8") as f:
        risposte = json.load(f)
    for r in risposte:
        if r["da"] == da and r["oggetto"] == oggetto:
            r["gestita"] = True
            r["gestita_il"] = datetime.now().isoformat()
    with open(RISPOSTE_FILE, "w", encoding="utf-8") as f:
        json.dump(risposte, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(giorni: int = 2) -> list[dict]:
    """Legge IMAP, classifica risposte, salva. Restituisce le nuove risposte."""
    log.info("=== AGENTE RISPOSTE EMAIL — avvio ===")
    accounts = carica_imap_config()
    if not accounts:
        log.warning("Nessun account IMAP configurato (EMAIL_x_IMAP_HOST mancante)")
        return []

    tutte = []
    for acc in accounts:
        messaggi = leggi_email_recenti(acc, giorni)
        for msg in messaggi:
            try:
                analisi = classifica_risposta(msg)
                tutte.append(analisi)
                if analisi.get("classificazione") == "stop":
                    # estrai indirizzo email grezzo
                    raw = analisi["da"]
                    addr = raw.split("<")[-1].rstrip(">") if "<" in raw else raw.strip()
                    processa_opt_out(addr)
            except Exception as e:
                log.warning(f"Errore classificazione: {e}")

    nuove = salva_risposte(tutte)
    log.info(f"=== AGENTE RISPOSTE EMAIL — {len(nuove)} nuove risposte classificate ===")
    return nuove


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_risposta(r: dict) -> str:
    cl_emoji = {
        "interessato":    "🟢",
        "non_interessato":"🔴",
        "stop":           "🚫",
        "richiesta_info": "💬",
        "neutro":         "⚪",
        "spam":           "🗑",
    }.get(r.get("classificazione","neutro"), "•")
    urg_emoji = {"alta":"🔴","media":"🟡","bassa":"🟢"}.get(r.get("urgenza","bassa"),"")
    righe = [
        f"{cl_emoji} *{r.get('classificazione','').upper()}* {urg_emoji}",
        f"📧 Da: {r.get('da','')}",
        f"📋 Oggetto: {r.get('oggetto','')}",
        f"💡 {r.get('motivo','')}",
        f"➡️ Prossimo: _{r.get('prossimo_passo','')}_",
    ]
    if r.get("risposta_suggerita"):
        righe.append(f"\n*Bozza risposta:*\n{r['risposta_suggerita']}")
    return "\n".join(righe)
