"""
agent_report.py — Agente Report KPI Settimanale
Genera report KPI per: Renergy Project&Build, ACM&Partners, RS Gas&Power.
Aggrega dati da Hub CRM, campagne, task tracker e metriche manuali.
Invia report formattato su Telegram ogni lunedì.
"""

import os, json, logging, requests
from datetime import datetime, timedelta
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)
CLAUDE_KEY  = os.environ.get("CLAUDE_API_KEY")
HUB_BASE    = "https://hub.renergygroup.it/api"
HUB_EMAIL   = os.environ.get("HUB_EMAIL")
HUB_PASS    = os.environ.get("HUB_PASSWORD")
REPORT_FILE = "report_history.json"
claude      = anthropic.Anthropic(api_key=CLAUDE_KEY)


# ---------------------------------------------------------------------------
# Hub CRM data
# ---------------------------------------------------------------------------
def _hub_token() -> str:
    r = requests.post(f"{HUB_BASE}/auth/login",
                      json={"email": HUB_EMAIL, "password": HUB_PASS}, timeout=10)
    r.raise_for_status()
    return r.json().get("token")

def fetch_hub_leads() -> list[dict]:
    try:
        token = _hub_token()
        for endpoint in ["/crm/lead", "/crm/leads"]:
            r = requests.get(f"{HUB_BASE}{endpoint}",
                             headers={"Authorization": f"Bearer {token}"},
                             params={"limit": 200, "orderBy": "created_at", "order": "desc"},
                             timeout=15)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("leads", data.get("data", []))
    except Exception as e:
        log.warning(f"Hub leads: {e}")
    return []


def calcola_kpi_renergy(leads: list[dict]) -> dict:
    oggi  = datetime.now().date()
    sett  = oggi - timedelta(days=7)
    stati = ["nuovo","contattato","interesse","preventivo","chiuso_vinto","chiuso_perso","sospeso"]
    conteggi = {s: 0 for s in stati}
    nuovi_sett = 0
    pipeline_eur = 0

    for l in leads:
        stato = l.get("stato","")
        if stato in conteggi:
            conteggi[stato] += 1
        try:
            creato = datetime.fromisoformat(l.get("created_at","")).date()
            if creato >= sett:
                nuovi_sett += 1
        except Exception:
            pass
        if l.get("valore_stimato"):
            try:
                pipeline_eur += float(str(l["valore_stimato"]).replace("€","").replace(",","").strip())
            except Exception:
                pass

    tasso_conv = 0
    if conteggi.get("preventivo",0) + conteggi.get("chiuso_vinto",0) > 0:
        tasso_conv = round(conteggi.get("chiuso_vinto",0) /
                           max(conteggi.get("preventivo",0) + conteggi.get("chiuso_vinto",0), 1) * 100)

    return {
        "totale_lead":     len(leads),
        "nuovi_settimana": nuovi_sett,
        "pipeline_eur":    pipeline_eur,
        "per_stato":       conteggi,
        "tasso_conv_pct":  tasso_conv,
    }


# ---------------------------------------------------------------------------
# Campagne data
# ---------------------------------------------------------------------------
def calcola_kpi_campagne() -> dict:
    try:
        from campagna_manager import carica_campagne
        campagne = carica_campagne()
        tot_lead = sum(len(c.get("lead",[])) for c in campagne.values())
        tot_inv  = sum(c.get("stats",{}).get("inviati",0) for c in campagne.values())
        tot_opt  = sum(c.get("stats",{}).get("opt_out",0) for c in campagne.values())
        attive   = sum(1 for c in campagne.values() if c.get("stato") == "attiva")
        return {"campagne_attive": attive, "lead_totali": tot_lead,
                "email_inviate": tot_inv, "opt_out": tot_opt}
    except Exception as e:
        log.warning(f"Campagne KPI: {e}")
        return {}


# ---------------------------------------------------------------------------
# Task Tracker data
# ---------------------------------------------------------------------------
def calcola_kpi_tasks() -> dict:
    try:
        from agent_task_tracker import carica_tasks
        tasks = carica_tasks()
        aperti    = sum(1 for t in tasks if t.get("stato") in ("aperto","in_corso"))
        completati = sum(1 for t in tasks if t.get("stato") == "completato")
        scaduti   = 0
        oggi = datetime.now().date()
        for t in tasks:
            if t.get("stato") not in ("aperto","in_corso"):
                continue
            try:
                if datetime.fromisoformat(t.get("scadenza","")).date() < oggi:
                    scaduti += 1
            except Exception:
                pass
        return {"aperti": aperti, "completati": completati, "scaduti": scaduti}
    except Exception as e:
        log.warning(f"Task KPI: {e}")
        return {}


# ---------------------------------------------------------------------------
# RS Gas&Power — metriche da file manuale (aggiornato via bot)
# ---------------------------------------------------------------------------
RSGAS_FILE = "rsgas_kpi.json"

def get_kpi_rsgas() -> dict:
    if Path(RSGAS_FILE).exists():
        with open(RSGAS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"contratti_attivi": 0, "nuovi_clienti_mese": 0, "churn_mese": 0, "note": "Dati non ancora inseriti"}

def aggiorna_kpi_rsgas(dati: dict):
    existing = get_kpi_rsgas()
    existing.update(dati)
    existing["aggiornato"] = datetime.now().isoformat()
    with open(RSGAS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Metriche di conversione AIOS v2.0 — fonte, ROI, landing, PDF, cross-sell
# ---------------------------------------------------------------------------
FONTI_LEAD = ("apollo_automatico", "crosssell", "inbound_landing", "referral", "newsletter")

def _leggi_json(path: str, default):
    try:
        if Path(path).exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Lettura {path}: {e}")
    return default

def calcola_kpi_conversione(leads: list[dict]) -> dict:
    # Lead per fonte + tasso conversione (lead -> cliente) per fonte
    per_fonte = {f: {"lead": 0, "clienti": 0} for f in FONTI_LEAD}
    for l in leads:
        fonte = l.get("fonte", "")
        chiave = next((f for f in FONTI_LEAD if f in fonte), None)
        if not chiave:
            continue
        per_fonte[chiave]["lead"] += 1
        if l.get("stato") in ("cliente", "chiuso_vinto", "installato", "attivo"):
            per_fonte[chiave]["clienti"] += 1
    conversione_fonte = {
        f: {"lead": d["lead"], "clienti": d["clienti"],
            "tasso_pct": round(d["clienti"] / d["lead"] * 100, 1) if d["lead"] else 0.0}
        for f, d in per_fonte.items()
    }

    # ROI campagne email — lead generati / email inviate
    campagne = _leggi_json("campaigns.json", {})
    tot_email = sum(c.get("stats", {}).get("inviati", 0) for c in campagne.values())
    tot_lead_camp = sum(len(c.get("lead", [])) for c in campagne.values())
    roi_campagne = round(tot_lead_camp / tot_email, 3) if tot_email else 0.0

    # Landing page — visite, form, conversione
    landing = _leggi_json("landing_pages.json", [])
    landing_kpi = []
    for l in landing:
        m = l.get("metriche", {})
        visite, form = m.get("visite", 0), m.get("form_compilati", 0)
        landing_kpi.append({
            "id": l.get("id", ""), "azienda": l.get("azienda", ""), "stato": l.get("stato", ""),
            "visite": visite, "form_compilati": form,
            "tasso_conv_pct": round(form / visite * 100, 1) if visite else 0.0,
        })

    # PDF lead magnet ACM — download, lead da teaser
    leadmagnet = _leggi_json("leadmagnet_acm.json", [])
    pdf_kpi = {
        "brief_pubblicati": sum(1 for e in leadmagnet if e.get("stato") == "approvato"),
        "download_totali":  sum(e.get("metriche", {}).get("download", 0) for e in leadmagnet),
        "lead_da_teaser":   sum(e.get("metriche", {}).get("lead_da_teaser", 0) for e in leadmagnet),
    }

    # Cross-sell — opportunità identificate, accettate, convertite
    crosssell = _leggi_json("crosssell_log.json", [])
    crosssell_kpi = {
        "identificate": len(crosssell),
        "accettate":    sum(1 for e in crosssell if e.get("esito") == "accettato"),
        "convertite":   sum(1 for e in crosssell if e.get("esito") == "convertito"),
    }

    return {
        "per_fonte":  conversione_fonte,
        "roi_campagne_email": roi_campagne,
        "landing":    landing_kpi,
        "leadmagnet_acm": pdf_kpi,
        "crosssell":  crosssell_kpi,
    }


# ---------------------------------------------------------------------------
# Claude — sintesi esecutiva
# ---------------------------------------------------------------------------
PROMPT_SINTESI = """Sei l'analista di un imprenditore italiano con 3 aziende.
Analizza questi KPI settimanali e fornisci una sintesi esecutiva breve e actionable.

Dati:
{dati}

Rispondi in 3-4 righe massimo con:
- 1-2 punti positivi (cosa sta andando bene)
- 1-2 priorità urgenti (cosa richiede attenzione)
- 1 suggerimento strategico per la settimana"""

def genera_sintesi(kpi_dict: dict) -> str:
    try:
        msg = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=300,
            messages=[{"role": "user", "content": PROMPT_SINTESI.format(
                dati=json.dumps(kpi_dict, ensure_ascii=False, indent=2)
            )}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        log.warning(f"Sintesi report: {e}")
        return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> dict:
    log.info("=== AGENTE REPORT — avvio ===")
    leads  = fetch_hub_leads()
    report = {
        "data":     datetime.now().isoformat(),
        "renergy":  calcola_kpi_renergy(leads),
        "campagne": calcola_kpi_campagne(),
        "tasks":    calcola_kpi_tasks(),
        "rsgas":    get_kpi_rsgas(),
        "conversione": calcola_kpi_conversione(leads),
    }
    report["sintesi"] = genera_sintesi(report)

    # Storicizza
    storico = []
    if Path(REPORT_FILE).exists():
        with open(REPORT_FILE, encoding="utf-8") as f:
            storico = json.load(f)
    storico.append(report)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(storico[-12:], f, ensure_ascii=False, indent=2)  # ultimi 12 report

    log.info("=== AGENTE REPORT — completato ===")
    return report


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_report(report: dict) -> str:
    data   = report.get("data","")[:10]
    r      = report.get("renergy",{})
    camp   = report.get("campagne",{})
    tasks  = report.get("tasks",{})
    rsgas  = report.get("rsgas",{})
    stati  = r.get("per_stato",{})

    righe = [f"📊 *Report KPI Settimanale — {data}*\n"]

    # Renergy
    righe.append("⚡ *Renergy Project\\&Build*")
    righe.append(f"  Lead totali: {r.get('totale_lead',0)} | Nuovi \\(7gg\\): {r.get('nuovi_settimana',0)}")
    righe.append(f"  Pipeline: €{r.get('pipeline_eur',0):,.0f} | Conv: {r.get('tasso_conv_pct',0)}%")
    righe.append(f"  Preventivi: {stati.get('preventivo',0)} | Vinti: {stati.get('chiuso_vinto',0)} | Persi: {stati.get('chiuso_perso',0)}")

    # Campagne
    if camp:
        righe.append("\n📬 *Campagne Outbound*")
        righe.append(f"  Attive: {camp.get('campagne_attive',0)} | Lead: {camp.get('lead_totali',0)}")
        righe.append(f"  Email inviate: {camp.get('email_inviate',0)} | Opt\\-out: {camp.get('opt_out',0)}")

    # RS Gas&Power
    righe.append("\n⚡🔵 *RS Gas\\&Power*")
    righe.append(f"  Contratti attivi: {rsgas.get('contratti_attivi',0)}")
    righe.append(f"  Nuovi clienti mese: {rsgas.get('nuovi_clienti_mese',0)} | Churn: {rsgas.get('churn_mese',0)}")
    if rsgas.get("note"):
        righe.append(f"  📝 {rsgas['note']}")

    # Task
    if tasks:
        righe.append("\n✅ *Task Tracker*")
        scaduti_txt = f" | 🔴 Scaduti: {tasks.get('scaduti',0)}" if tasks.get('scaduti') else ""
        righe.append(f"  Aperti: {tasks.get('aperti',0)} | Completati: {tasks.get('completati',0)}{scaduti_txt}")

    # Metriche di conversione (AIOS v2.0)
    conv = report.get("conversione", {})
    if conv:
        righe.append("\n📈 *Conversione & ROI*")
        per_fonte = {f: d for f, d in conv.get("per_fonte", {}).items() if d.get("lead", 0) > 0}
        if per_fonte:
            righe.append("  Lead → cliente per fonte:")
            for fonte, d in per_fonte.items():
                righe.append(f"    • {fonte}: {d['lead']} lead → {d['clienti']} clienti ({d['tasso_pct']}%)")
        righe.append(f"  ROI campagne email: {conv.get('roi_campagne_email',0)} lead/email inviata")

        landing = conv.get("landing", [])
        attive = [l for l in landing if l.get("stato") == "pubblicata"]
        if attive:
            righe.append(f"  Landing attive: {len(attive)}")
            for l in attive[:3]:
                righe.append(f"    • {l['id']} ({l['azienda']}): {l['visite']} visite, "
                             f"{l['form_compilati']} form ({l['tasso_conv_pct']}%)")

        lm = conv.get("leadmagnet_acm", {})
        if lm.get("brief_pubblicati"):
            righe.append(f"  PDF lead magnet ACM: {lm['brief_pubblicati']} pubblicati | "
                         f"{lm['download_totali']} download | {lm['lead_da_teaser']} lead da teaser")

        cs = conv.get("crosssell", {})
        if cs.get("identificate"):
            righe.append(f"  Cross-sell: {cs['identificate']} identificate | "
                         f"{cs['accettate']} accettate | {cs['convertite']} convertite")

    # Sintesi
    if report.get("sintesi"):
        righe.append(f"\n💡 *Sintesi esecutiva:*\n_{report['sintesi']}_")

    return "\n".join(righe)
