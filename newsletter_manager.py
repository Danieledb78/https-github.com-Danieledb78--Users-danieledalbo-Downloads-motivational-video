"""
newsletter_manager.py — Sistema Newsletter Autonomo
Raccoglie lead (iscrizioni via form/Telegram), gestisce la lista,
genera newsletter settimanali con Claude e le invia via SMTP.
Obiettivo: crescita follower e lead generation per Renergy e ACM.
"""

import os, json, uuid, smtplib, logging
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import anthropic

log = logging.getLogger(__name__)
CLAUDE_KEY   = os.environ.get("CLAUDE_API_KEY")
claude       = anthropic.Anthropic(api_key=CLAUDE_KEY)
LEADS_FILE   = "newsletter_leads.json"
ISSUES_FILE  = "newsletter_issues.json"
SENT_FILE    = "newsletter_sent.json"

NEWSLETTER_PROFILES = {
    "Renergy": {
        "nome":       "⚡ Renergy Energy Update",
        "azienda":    "Renergy Project&Build",
        "tagline":    "Energia, incentivi e tecnologia per le imprese",
        "topic":      "energia rinnovabile, fotovoltaico industriale, incentivi 2026, efficienza energetica, EV charging",
        "cta":        "Vuoi sapere quanto puoi risparmiare con il fotovoltaico? Scrivici.",
        "from_name":  "Renergy Project&Build",
        "from_email_env": "NEWSLETTER_RENERGY_EMAIL",
    },
    "ACM": {
        "nome":       "🏢 ACM ESG Insights",
        "azienda":    "ACM&Partners",
        "tagline":    "Strategie ESG, normativa e crescita per PMI",
        "topic":      "ESG, CSRD, sostenibilità aziendale, M&A, crescita PMI, normativa europea",
        "cta":        "Vuoi una valutazione ESG gratuita per la tua azienda? Contattaci.",
        "from_name":  "ACM&Partners",
        "from_email_env": "NEWSLETTER_ACM_EMAIL",
    },
}


# ---------------------------------------------------------------------------
# Gestione iscritti
# ---------------------------------------------------------------------------
def carica_leads() -> list[dict]:
    if Path(LEADS_FILE).exists():
        with open(LEADS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_leads(leads: list[dict]):
    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

def iscrivi_lead(email: str, nome: str, azienda_nl: str, fonte: str = "telegram") -> dict:
    leads = carica_leads()
    # Check duplicato
    existing = next((l for l in leads if l["email"].lower() == email.lower()
                     and l["newsletter"] == azienda_nl), None)
    if existing:
        return existing

    lead = {
        "id":         uuid.uuid4().hex[:8],
        "email":      email.lower().strip(),
        "nome":       nome,
        "newsletter": azienda_nl,   # "Renergy" | "ACM"
        "fonte":      fonte,
        "iscritto":   datetime.now().isoformat(),
        "attivo":     True,
        "email_ricevute": 0,
    }
    leads.append(lead)
    salva_leads(leads)
    log.info(f"Nuovo iscritto newsletter {azienda_nl}: {email}")
    return lead

def disiscrivi_lead(email: str, azienda_nl: str = None):
    leads = carica_leads()
    for l in leads:
        if l["email"].lower() == email.lower():
            if azienda_nl is None or l["newsletter"] == azienda_nl:
                l["attivo"] = False
                l["disiscritto"] = datetime.now().isoformat()
    salva_leads(leads)

def get_iscritti_attivi(azienda_nl: str) -> list[dict]:
    return [l for l in carica_leads() if l.get("newsletter") == azienda_nl and l.get("attivo")]

def get_stats_newsletter() -> dict:
    leads = carica_leads()
    result = {}
    for nl in NEWSLETTER_PROFILES:
        attivi = [l for l in leads if l.get("newsletter") == nl and l.get("attivo")]
        result[nl] = {"iscritti": len(attivi)}
    return result


# ---------------------------------------------------------------------------
# Generazione newsletter con Claude
# ---------------------------------------------------------------------------
PROMPT_NEWSLETTER = """Sei un editor esperto di newsletter B2B italiane.
Crea una newsletter professionale per il seguente brand.

Newsletter: {nome_nl}
Tagline: {tagline}
Topic del brand: {topic}
Data: {data}

Genera contenuti originali basati sui trend attuali del settore.
La newsletter deve avere:
1. Subject line accattivante (max 60 caratteri)
2. Intestazione con data
3. Editoriale breve (80-100 parole): insight o opinione su un trend del momento
4. 3 notizie/aggiornamenti del settore con commento (40-60 parole ciascuna)
5. Tip pratico della settimana (50-70 parole)
6. Call to action: {cta}
7. Footer con link disiscrizione

Stile: professionale, diretto, utile. Non promozionale. Valore per il lettore.

Rispondi SOLO con JSON:
{{
  "subject": "...",
  "preheader": "anteprima breve (max 90 caratteri)",
  "corpo_html": "HTML completo della newsletter (usa tag basici: <h2>,<p>,<ul>,<li>,<b>)",
  "corpo_testo": "versione testo piano della newsletter",
  "argomento_principale": "tema centrale di questo numero"
}}"""

def genera_newsletter(azienda_nl: str) -> dict:
    profilo = NEWSLETTER_PROFILES[azienda_nl]
    data    = datetime.now().strftime("%d %B %Y")
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=3000,
        messages=[{"role": "user", "content": PROMPT_NEWSLETTER.format(
            nome_nl=profilo["nome"],
            tagline=profilo["tagline"],
            topic=profilo["topic"],
            data=data,
            cta=profilo["cta"],
        )}],
    )
    t = msg.content[0].text.strip()
    result = json.loads(t[t.find("{"):t.rfind("}")+1])
    result["newsletter"]  = azienda_nl
    result["generata"]    = datetime.now().isoformat()
    result["stato"]       = "bozza"   # bozza | approvata | inviata
    result["id"]          = f"nl_{azienda_nl.lower()}_{datetime.now().strftime('%Y%m%d')}"
    return result


# ---------------------------------------------------------------------------
# Storage issues
# ---------------------------------------------------------------------------
def carica_issues() -> list[dict]:
    if Path(ISSUES_FILE).exists():
        with open(ISSUES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_issue(issue: dict):
    issues = carica_issues()
    # Aggiorna se esiste, altrimenti aggiungi
    for i, ex in enumerate(issues):
        if ex["id"] == issue["id"]:
            issues[i] = issue
            with open(ISSUES_FILE, "w", encoding="utf-8") as f:
                json.dump(issues, f, ensure_ascii=False, indent=2)
            return
    issues.append(issue)
    with open(ISSUES_FILE, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

def get_bozze_in_attesa() -> list[dict]:
    return [i for i in carica_issues() if i.get("stato") == "bozza"]

def approva_issue(issue_id: str):
    issues = carica_issues()
    for i in issues:
        if i["id"] == issue_id:
            i["stato"]      = "approvata"
            i["approvata_il"] = datetime.now().isoformat()
    with open(ISSUES_FILE, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

def get_issue(issue_id: str) -> dict | None:
    return next((i for i in carica_issues() if i["id"] == issue_id), None)


# ---------------------------------------------------------------------------
# Invio email
# ---------------------------------------------------------------------------
def _get_smtp_account(env_key: str) -> dict | None:
    addr = os.environ.get(env_key)
    if not addr:
        # Fallback: primo account email configurato
        for i in range(1, 10):
            a = os.environ.get(f"EMAIL_{i}_ADDRESS")
            if a:
                return {
                    "address":  a,
                    "password": os.environ.get(f"EMAIL_{i}_PASSWORD",""),
                    "host":     os.environ.get(f"EMAIL_{i}_SMTP_HOST",""),
                    "port":     int(os.environ.get(f"EMAIL_{i}_SMTP_PORT","587")),
                    "ssl":      os.environ.get(f"EMAIL_{i}_SSL","false").lower() == "true",
                    "label":    os.environ.get(f"EMAIL_{i}_LABEL",""),
                }
    return None

def invia_issue(issue_id: str) -> dict:
    issue   = get_issue(issue_id)
    if not issue:
        return {"inviati": 0, "errori": 0, "errore": "Issue non trovata"}

    azienda_nl = issue["newsletter"]
    profilo    = NEWSLETTER_PROFILES.get(azienda_nl, {})
    account    = _get_smtp_account(profilo.get("from_email_env",""))
    iscritti   = get_iscritti_attivi(azienda_nl)

    if not account:
        return {"inviati": 0, "errori": 0, "errore": "Account email non configurato"}

    inviati, errori = 0, 0
    corpo_html = issue.get("corpo_html","")
    corpo_txt  = issue.get("corpo_testo","")

    for lead in iscritti:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"]    = f"{profilo.get('from_name','')} <{account['address']}>"
            msg["To"]      = lead["email"]
            msg["Subject"] = issue.get("subject","Newsletter")

            # Personalizza con nome
            corpo_personalizzato = corpo_html.replace("{{nome}}", lead.get("nome",""))
            # Aggiungi link disiscrizione
            corpo_personalizzato += (
                f"<br><br><hr><p style='font-size:11px;color:#888'>"
                f"Hai ricevuto questa email perché sei iscritto alla newsletter {profilo.get('nome','')}. "
                f"Per disiscriverti rispondi con oggetto STOP o scrivi a {account['address']}.</p>"
            )

            msg.attach(MIMEText(corpo_txt, "plain", "utf-8"))
            msg.attach(MIMEText(corpo_personalizzato, "html", "utf-8"))

            if account["ssl"]:
                srv = smtplib.SMTP_SSL(account["host"], account["port"], timeout=15)
            else:
                srv = smtplib.SMTP(account["host"], account["port"], timeout=15)
                srv.starttls()
            srv.login(account["address"], account["password"])
            srv.send_message(msg)
            srv.quit()

            lead["email_ricevute"] = lead.get("email_ricevute",0) + 1
            inviati += 1
            log.info(f"Newsletter inviata a {lead['email']}")

        except Exception as e:
            log.warning(f"Errore invio {lead['email']}: {e}")
            errori += 1

    # Aggiorna lista e stato issue
    salva_leads(carica_leads())
    issues = carica_issues()
    for i in issues:
        if i["id"] == issue_id:
            i["stato"]     = "inviata"
            i["inviata_il"]= datetime.now().isoformat()
            i["stats"]     = {"inviati": inviati, "errori": errori, "destinatari": len(iscritti)}
    with open(ISSUES_FILE, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

    return {"inviati": inviati, "errori": errori}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> list[dict]:
    """Genera le bozze newsletter settimanali per tutte le liste."""
    log.info("=== NEWSLETTER MANAGER — generazione bozze ===")
    bozze = []
    for azienda_nl in NEWSLETTER_PROFILES:
        try:
            # Evita di generare se esiste già una bozza non inviata questa settimana
            esistenti = [i for i in carica_issues()
                         if i.get("newsletter") == azienda_nl and i.get("stato") in ("bozza","approvata")]
            if esistenti:
                log.info(f"Newsletter {azienda_nl}: esiste già bozza/approvata, skip")
                continue

            issue = genera_newsletter(azienda_nl)
            salva_issue(issue)
            bozze.append(issue)
            log.info(f"Newsletter {azienda_nl} generata: {issue['id']}")
        except Exception as e:
            log.warning(f"Newsletter {azienda_nl}: {e}")

    log.info(f"=== NEWSLETTER MANAGER — {len(bozze)} bozze generate ===")
    return bozze


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_preview_newsletter(issue: dict) -> str:
    nl_name = NEWSLETTER_PROFILES.get(issue.get("newsletter",""),{}).get("nome","Newsletter")
    righe = [
        f"📬 *{nl_name}*",
        f"ID: `{issue.get('id','')}`",
        f"Subject: *{issue.get('subject','')}*",
        f"Preheader: _{issue.get('preheader','')}_",
        f"Argomento: {issue.get('argomento_principale','')}",
        f"Stato: {issue.get('stato','')}",
        f"\n_Anteprima testo:_\n{issue.get('corpo_testo','')[:300]}...",
    ]
    return "\n".join(righe)

def formatta_stats_newsletter() -> str:
    stats   = get_stats_newsletter()
    issues  = carica_issues()
    inviate = [i for i in issues if i.get("stato") == "inviata"]
    bozze   = [i for i in issues if i.get("stato") in ("bozza","approvata")]
    righe   = ["📊 *Newsletter — Statistiche*\n"]
    for nl, s in stats.items():
        profilo = NEWSLETTER_PROFILES.get(nl,{})
        righe.append(f"*{profilo.get('nome',nl)}*")
        righe.append(f"  Iscritti attivi: {s['iscritti']}")
    if inviate:
        ultima = inviate[-1]
        righe.append(f"\n📤 Ultima inviata: {ultima.get('id','')} il {ultima.get('inviata_il','')[:10]}")
        s_inv = ultima.get("stats",{})
        righe.append(f"  Inviati: {s_inv.get('inviati',0)} | Errori: {s_inv.get('errori',0)}")
    if bozze:
        righe.append(f"\n📝 Bozze in attesa di approvazione: {len(bozze)}")
        for b in bozze:
            righe.append(f"  • {b['id']} ({b.get('newsletter','')}) — {b.get('subject','')}")
    return "\n".join(righe)
