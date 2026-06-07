"""
agent_leadmagnet_acm.py — PDF Lead Magnet per ACM&Partners (AIOS v2.0)
Genera un brief ESG professionale a 4 pagine A4 (cover, executive summary, analisi e
opportunità, CTA e contatti) a partire dall'ultima analisi di agent_esg_monitor, con
qualità tipografica da consulenza: HTML/CSS -> Puppeteer -> PDF (mai reportlab/fpdf puri).

Genera anche una versione teaser (2 pagine) per LinkedIn/sito con CTA verso la landing ACM.

Trigger: ogni venerdì dopo agent_esg_monitor, oppure /leadmagnet
Output: PDF in /pdf_storage/acm/ + registro in leadmagnet_acm.json
"""

import os, json, logging, subprocess, re
from datetime import datetime
from pathlib import Path
import anthropic

import shared_intelligence as si
import config

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)

LEADMAGNET_FILE = "leadmagnet_acm.json"
PDF_STORAGE_DIR = Path("pdf_storage/acm")
PUPPETEER_SCRIPT = "puppeteer_pdf.js"
BRAND = config.BRAND["ACM"]


# ---------------------------------------------------------------------------
# Generazione HTML brief — Claude Opus
# ---------------------------------------------------------------------------
PROMPT_BRIEF = """Genera l'HTML completo (con <style> CSS inline, layout A4 stampabile, 4 pagine fisiche
separate da <div class="page"> con page-break-after: always) per un brief ESG professionale ACM&Partners.

Brand colors: primario {primario} (verde foresta), secondario {secondario} (oro antico).
Font: {font_titoli} per i titoli (Google Fonts CDN), {font_corpo} per il corpo testo.
Carica i font con: <link href="https://fonts.googleapis.com/css2?family={google_fonts}&display=swap" rel="stylesheet">

CONTENUTO DA USARE (ultima analisi ESG disponibile):
{analisi_json}

STRUTTURA OBBLIGATORIA (4 pagine A4, margini almeno 15mm):
PAGINA 1 — COVER: logo placeholder <img src="LOGO_PLACEHOLDER" alt="ACM&Partners">, titolo
"Brief ESG — {mese_anno}", sottotitolo con il tema centrale, sfondo gradiente verde foresta -> oro antico,
data e numero edizione.
PAGINA 2 — EXECUTIVE SUMMARY: sezione "Cosa cambia" con 3 punti chiave in card colorate, timeline
normativa CSS delle scadenze CSRD, tabella impatto per settore con icone SVG inline e semaforo urgenza.
PAGINA 3 — ANALISI E OPPORTUNITÀ: checklist "Sei pronto per la CSRD?" con 10 domande sì/no e spazio
risposta, box "Sanzioni previste" evidenziato in rosso, sezione "Come ACM ti accompagna" con 3-4 servizi.
PAGINA 4 — CTA E CONTATTI: "Prossimo passo: assessment gratuito 30 minuti", bottone CTA (link landing
ACM placeholder LANDING_ACM_PLACEHOLDER), contatti placeholder [INSERIRE DATI REALI], footer legale.

REQUISITI: line-height 1.6 corpo / 1.2 titoli, SVG inline per icone (mai emoji/raster), nessun testo
a contatto col bordo pagina.

TONO DI VOCE ACM: autorevole ma accessibile mai accademico, europeo nel riferimento normativo e
italiano nella concretezza, thought leadership, urgency normativa senza allarmismo, ogni affermazione
specifica e verificabile.

Rispondi SOLO con l'HTML completo (da <!DOCTYPE html> a </html>), senza markdown."""

PROMPT_TEASER = """A partire da questo brief ESG completo (HTML), genera una versione TEASER di sole
2 pagine A4 (le prime due: cover + executive summary), identica nello stile e nei brand colors, ma con
un blocco finale aggiunto in fondo alla pagina 2 con CTA "Scarica il report completo" che rimanda alla
landing page ACM (link placeholder LANDING_ACM_PLACEHOLDER).

BRIEF COMPLETO:
{html_completo}

Rispondi SOLO con l'HTML completo del teaser (da <!DOCTYPE html> a </html>), senza markdown."""


def _pulisci_html(testo: str) -> str:
    testo = re.sub(r"^```(?:html)?\s*", "", testo.strip())
    return re.sub(r"```\s*$", "", testo).strip()


def _ultima_analisi_esg() -> dict:
    try:
        if Path("esg_monitor_data.json").exists():
            with open("esg_monitor_data.json", encoding="utf-8") as f:
                dati = json.load(f)
            analisi = dati.get("analisi", [])
            return analisi[-1] if analisi else {}
    except Exception as e:
        log.warning(f"Lettura analisi ESG fallita: {e}")
    return {}


def genera_html_brief(analisi: dict) -> str:
    google_fonts = BRAND["google_fonts"]
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=8000, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_BRIEF.format(
            primario=BRAND["primario"], secondario=BRAND["secondario"],
            font_titoli=BRAND["font_titoli"], font_corpo=BRAND["font_corpo"],
            google_fonts=google_fonts.replace(" ", "+"),
            analisi_json=json.dumps(analisi, ensure_ascii=False)[:6000],
            mese_anno=datetime.now().strftime("%B %Y"),
        )}],
    )
    return _pulisci_html(msg.content[0].text)


def genera_html_teaser(html_completo: str) -> str:
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=4000, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_TEASER.format(html_completo=html_completo[:12000])}],
    )
    return _pulisci_html(msg.content[0].text)


# ---------------------------------------------------------------------------
# Generazione PDF via Puppeteer (subprocess Node.js)
# ---------------------------------------------------------------------------
def html_to_pdf(html: str, slug: str) -> str | None:
    html_path = f"/tmp/brief_{slug}.html"
    pdf_path  = f"/tmp/brief_{slug}.pdf"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        result = subprocess.run(
            ["node", PUPPETEER_SCRIPT, html_path, pdf_path],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            log.warning(f"Puppeteer PDF — errore: {result.stderr.strip()[-500:]}")
            return None
        return pdf_path if Path(pdf_path).exists() else None
    except FileNotFoundError:
        log.warning("Node.js non disponibile — impossibile generare il PDF (richiesto puppeteer_pdf.js).")
        return None
    except subprocess.TimeoutExpired:
        log.warning("Puppeteer PDF — timeout generazione")
        return None


def archivia_pdf(pdf_path_tmp: str, slug: str) -> str:
    PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_STORAGE_DIR / f"brief_{slug}.pdf"
    Path(pdf_path_tmp).replace(dest)
    return str(dest)


# ---------------------------------------------------------------------------
# Storage registro lead magnet
# ---------------------------------------------------------------------------
def carica_registro() -> list[dict]:
    if Path(LEADMAGNET_FILE).exists():
        with open(LEADMAGNET_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def registra_brief(slug: str, titolo: str, pdf_path_tmp: str, teaser_html_path: str | None,
                   insight_id: str | None) -> dict:
    registro = carica_registro()
    entry = {
        "id":         f"lm_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "slug":       slug,
        "titolo":     titolo,
        "pdf_path":   pdf_path_tmp,    # path temporaneo, in attesa di approvazione
        "teaser_path": teaser_html_path,
        "insight_id": insight_id,
        "stato":      "in_approvazione",   # in_approvazione | approvato | scartato
        "creato_il":  datetime.now().isoformat(),
        "url_pubblico": None,
        "metriche":   {"download": 0, "lead_da_teaser": 0},
    }
    registro.append(entry)
    with open(LEADMAGNET_FILE, "w", encoding="utf-8") as f:
        json.dump(registro[-100:], f, ensure_ascii=False, indent=2)
    return entry

def approva_brief(lm_id: str) -> dict | None:
    registro = carica_registro()
    entry = next((e for e in registro if e["id"] == lm_id), None)
    if not entry:
        return None
    archiviato = archivia_pdf(entry["pdf_path"], entry["slug"])
    entry["pdf_path"]     = archiviato
    entry["stato"]        = "approvato"
    entry["url_pubblico"] = archiviato   # su Replit servito come file statico /pdf_storage/...
    with open(LEADMAGNET_FILE, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=2)
    if entry.get("insight_id"):
        try:
            si.segna_usato(entry["insight_id"], "landing", {"leadmagnet_id": lm_id, "url": archiviato})
        except Exception:
            pass
    return entry

def scarta_brief(lm_id: str) -> dict | None:
    registro = carica_registro()
    entry = next((e for e in registro if e["id"] == lm_id), None)
    if not entry:
        return None
    entry["stato"] = "scartato"
    with open(LEADMAGNET_FILE, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=2)
    return entry

def get_brief(lm_id: str) -> dict | None:
    return next((e for e in carica_registro() if e["id"] == lm_id), None)

def get_ultimo_brief_approvato() -> dict | None:
    approvati = [e for e in carica_registro() if e.get("stato") == "approvato"]
    return approvati[-1] if approvati else None


# ---------------------------------------------------------------------------
# Flow principale
# ---------------------------------------------------------------------------
def _slug() -> str:
    return f"esg_{datetime.now().strftime('%Y%m')}"

def genera_brief() -> tuple[dict | None, str]:
    log.info("=== AGENTE LEAD MAGNET ACM — avvio ===")
    analisi = _ultima_analisi_esg()
    if not analisi:
        return None, "⚠️ Nessuna analisi ESG disponibile per generare il brief. Lancia prima /esg."

    slug   = _slug()
    titolo = f"Brief ESG — {datetime.now().strftime('%B %Y')}: {analisi.get('sintesi','')[:60]}"

    try:
        html_completo = genera_html_brief(analisi)
    except Exception as e:
        log.warning(f"Lead magnet — generazione HTML fallita: {e}")
        return None, "⚠️ Errore nella generazione del contenuto del brief."

    pdf_tmp = html_to_pdf(html_completo, slug)
    if not pdf_tmp:
        return None, ("⚠️ PDF non generato (Puppeteer/Node.js non disponibile o errore). "
                      "Verifica che 'npm install puppeteer' sia stato eseguito sull'ambiente Replit.")

    teaser_path = None
    try:
        html_teaser = genera_html_teaser(html_completo)
        teaser_path = f"/tmp/brief_{slug}_teaser.html"
        with open(teaser_path, "w", encoding="utf-8") as f:
            f.write(html_teaser)
    except Exception as e:
        log.warning(f"Lead magnet — generazione teaser fallita: {e}")

    insight_id = None
    insights_esg = [i for i in si.carica().get("insights", [])
                    if i.get("fonte") == "agent_esg_monitor" and not i.get("usato_landing")]
    if insights_esg:
        insight_id = insights_esg[-1]["id"]

    entry = registra_brief(slug, titolo, pdf_tmp, teaser_path, insight_id)
    log.info(f"=== AGENTE LEAD MAGNET ACM — bozza creata: {entry['id']} ===")
    return entry, formatta_anteprima(entry)


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_anteprima(entry: dict) -> str:
    return (
        f"📄 *Brief ESG — Lead Magnet ACM&Partners*\n"
        f"ID: `{entry['id']}`\n"
        f"Titolo: {entry.get('titolo','')}\n"
        f"PDF: `{entry.get('pdf_path','')}`\n"
        f"Teaser LinkedIn: {'generato ✅' if entry.get('teaser_path') else '— non disponibile'}\n\n"
        f"Rivedi l'anteprima e premi *Approva* per archiviarlo e collegarlo a landing/newsletter/contenuti."
    )

def formatta_esito_approvazione(entry: dict) -> str:
    return (
        f"✅ *Brief approvato e archiviato*\n"
        f"📂 {entry.get('url_pubblico','')}\n\n"
        f"Verrà collegato automaticamente a:\n"
        f"• Landing page ACM (sezione \"scarica il report\")\n"
        f"• Prossima newsletter ACM ESG Insights\n"
        f"• Firma email campagne ACM"
    )
