"""
agent_landing.py — Generatore Landing Page Automatico (AIOS v2.0)
Genera con Claude Opus una landing page HTML/CSS/JS completa, brandizzata per azienda,
e la pubblica automaticamente su Netlify dopo approvazione dell'owner.

Trigger: chiamato dall'orchestratore su approvazione owner, oppure manualmente con /landing
Output: file HTML in /tmp + deploy Netlify + URL salvato in shared_intelligence.json
"""

import os, io, json, time, zipfile, logging, re
from datetime import datetime
from pathlib import Path
import requests
import anthropic

import shared_intelligence as si
import config

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)
LANDING_FILE = "landing_pages.json"

NETLIFY_TOKEN = os.environ.get("NETLIFY_ACCESS_TOKEN")
NETLIFY_SITES = {
    "Renergy": os.environ.get("NETLIFY_SITE_ID_RENERGY"),
    "RS_Gas":  os.environ.get("NETLIFY_SITE_ID_RSGAS"),
    "ACM":     os.environ.get("NETLIFY_SITE_ID_ACM"),
}
AZIENDE_NOME = {"Renergy": "Renergy Project&Build", "RS_Gas": "RS Gas&Power", "ACM": "ACM&Partners"}


# ---------------------------------------------------------------------------
# Generazione HTML — Claude Opus
# ---------------------------------------------------------------------------
PROMPT_LANDING = """Genera una landing page HTML completa, CSS inline (dentro <style>), JS vanilla inline.
Azienda: {azienda} — Brand colors: primario {primario}, secondario {secondario} — Font: {font} (Google Fonts CDN: {google_fonts_url})

Insight/Offerta: {titolo_insight}
Target: {target}
Obiettivo: acquisire lead qualificati (nome, email, azienda, dimensione, telefono)

STRUTTURA OBBLIGATORIA:
1. Hero section: headline benefit-focused (non feature), sub-headline, CTA button
2. Pain points section: 3 problemi del target con icone SVG inline
3. Soluzione: come {azienda} risolve ciascun problema
4. Numeri/prove sociali: placeholder con [INSERISCI DATO REALE]
5. Form contatto: nome*, email*, azienda*, settore (select), n_dipendenti (select), tel, messaggio
6. Footer: P.IVA, indirizzo, link privacy policy

REQUISITI TECNICI:
- Mobile-first responsive (testare mentalmente su 375px di larghezza)
- Form POST a webhook URL placeholder: https://hooks.zapier.com/PLACEHOLDER
- Meta tag SEO: title, description, OG tags
- Google Analytics placeholder: UA-PLACEHOLDER
- Nessuna dipendenza esterna eccetto Google Fonts
- CSS custom properties per facile personalizzazione brand
- Animazione fade-in scroll per sezioni principali (CSS puro)
- Loading state sul bottone form dopo submit
- Logo: <img src="LOGO_PLACEHOLDER" alt="{azienda}">
- line-height: 1.6 minimo nel corpo testo, 1.2 nei titoli
- Icone: SVG inline semplici (mai emoji o immagini raster)
- CTA button: colore brand primario, testo bianco, border-radius 4px, padding generoso

TONO DI VOCE: {tono_voce}

Rispondi SOLO con il codice HTML completo (da <!DOCTYPE html> a </html>), senza markdown, senza commenti aggiuntivi."""

def genera_html_landing(azienda: str, titolo_insight: str, target: str) -> str:
    brand = config.BRAND.get(azienda, config.BRAND["Renergy"])
    google_fonts_url = f"https://fonts.googleapis.com/css2?family={brand['google_fonts']}&display=swap"
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=8000, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_LANDING.format(
            azienda=AZIENDE_NOME.get(azienda, azienda),
            primario=brand["primario"], secondario=brand["secondario"],
            font=f"{brand['font_titoli']} (titoli) / {brand['font_corpo']} (corpo)" if brand['font_titoli'] != brand['font_corpo'] else brand['font_titoli'],
            google_fonts_url=google_fonts_url,
            titolo_insight=titolo_insight, target=target,
            tono_voce=config.TONO_VOCE.get(azienda, ""),
        )}],
    )
    html = msg.content[0].text.strip()
    # Pulizia eventuali fence markdown residui
    html = re.sub(r"^```(?:html)?\s*", "", html)
    html = re.sub(r"```\s*$", "", html).strip()
    return html


def _slug(testo: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", testo.lower()).strip("-")
    return s[:50] or "landing"


def salva_html(azienda: str, titolo_insight: str, html: str) -> str:
    slug = _slug(f"{azienda}-{titolo_insight}")
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"/tmp/landing_{slug}_{ts}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Landing salvata: {path}")
    return path


# ---------------------------------------------------------------------------
# Deploy automatico su Netlify
# ---------------------------------------------------------------------------
def deploy_netlify(html_path: str, azienda: str) -> dict:
    """Zippa l'HTML come index.html e lo invia a Netlify per il deploy."""
    site_id = NETLIFY_SITES.get(azienda)
    if not NETLIFY_TOKEN or not site_id:
        return {"ok": False, "errore": "Netlify non configurato (token o site_id mancante)"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(html_path, arcname="index.html")
    buf.seek(0)

    try:
        resp = requests.post(
            f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
            headers={"Authorization": f"Bearer {NETLIFY_TOKEN}",
                     "Content-Type": "application/zip"},
            data=buf.read(), timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("ssl_url") or data.get("url") or data.get("deploy_ssl_url", "")
        return {"ok": True, "url": url, "deploy_id": data.get("id", "")}
    except Exception as e:
        log.warning(f"Netlify — deploy fallito: {e}")
        return {"ok": False, "errore": str(e)}


def verifica_deploy(url: str) -> bool:
    """Attende LANDING_VERIFY_DELAY_SEC secondi poi verifica che l'URL risponda."""
    if not url:
        return False
    time.sleep(config.LANDING_VERIFY_DELAY_SEC)
    try:
        r = requests.get(url, timeout=20)
        return r.status_code == 200
    except Exception as e:
        log.warning(f"Verifica deploy fallita: {e}")
        return False


# ---------------------------------------------------------------------------
# Storage registro landing
# ---------------------------------------------------------------------------
def carica_registro() -> list[dict]:
    if Path(LANDING_FILE).exists():
        with open(LANDING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def registra_landing(azienda: str, insight_id: str, titolo: str, html_path: str,
                      url: str | None = None) -> dict:
    registro = carica_registro()
    entry = {
        "id":         f"land_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "azienda":    azienda,
        "insight_id": insight_id,
        "titolo":     titolo,
        "html_path":  html_path,
        "url":        url,
        "stato":      "pubblicata" if url else "bozza",
        "creata_il":  datetime.now().isoformat(),
        "metriche":   {"visite": 0, "form_compilati": 0},
    }
    registro.append(entry)
    with open(LANDING_FILE, "w", encoding="utf-8") as f:
        json.dump(registro[-200:], f, ensure_ascii=False, indent=2)
    return entry

def aggiorna_landing(land_id: str, **campi):
    registro = carica_registro()
    for r in registro:
        if r["id"] == land_id:
            r.update(campi)
    with open(LANDING_FILE, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=2)

def get_landing(land_id: str) -> dict | None:
    return next((r for r in carica_registro() if r["id"] == land_id), None)

def get_ultima_landing_attiva(azienda: str) -> dict | None:
    attive = [r for r in carica_registro() if r.get("azienda") == azienda and r.get("stato") == "pubblicata"]
    return attive[-1] if attive else None


# ---------------------------------------------------------------------------
# Flow principale — genera bozza (il deploy avviene su approvazione owner)
# ---------------------------------------------------------------------------
def crea_bozza_landing(insight: dict) -> dict:
    """Genera l'HTML e salva la bozza, in attesa di approvazione per il deploy."""
    azienda = insight.get("azienda", "Renergy")
    if azienda == "Tutte":
        azienda = "Renergy"
    html      = genera_html_landing(azienda, insight.get("titolo", ""), insight.get("target_icp", ""))
    html_path = salva_html(azienda, insight.get("titolo", ""), html)
    entry     = registra_landing(azienda, insight.get("id", ""), insight.get("titolo", ""), html_path)
    return entry

def pubblica_landing(land_id: str) -> dict:
    """Esegue il deploy Netlify per una landing già generata e ne verifica l'esito."""
    entry = get_landing(land_id)
    if not entry:
        return {"ok": False, "errore": "Landing non trovata"}

    esito = deploy_netlify(entry["html_path"], entry["azienda"])
    if not esito.get("ok"):
        return esito

    url = esito["url"]
    aggiorna_landing(land_id, url=url, stato="pubblicata")
    if entry.get("insight_id"):
        try:
            si.segna_usato(entry["insight_id"], "landing", {"url": url, "landing_id": land_id})
        except Exception as e:
            log.warning(f"Aggiornamento shared_intelligence fallito: {e}")

    verificata = verifica_deploy(url)
    return {"ok": True, "url": url, "verificata": verificata}


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_anteprima(entry: dict) -> str:
    return (
        f"🖥 *Landing Page generata — {AZIENDE_NOME.get(entry['azienda'], entry['azienda'])}*\n"
        f"ID: `{entry['id']}`\n"
        f"Titolo: {entry.get('titolo','')}\n"
        f"File: `{entry.get('html_path','')}`\n\n"
        f"Premi *Deploy* per pubblicare su Netlify, oppure scarica il file per revisionarlo prima."
    )

def formatta_esito_deploy(esito: dict, entry: dict) -> str:
    if not esito.get("ok"):
        return f"❌ Deploy fallito: {esito.get('errore','errore sconosciuto')}"
    check = "✅ raggiungibile" if esito.get("verificata") else "⚠️ non ancora verificata (controlla tra qualche minuto)"
    return (
        f"🚀 *Landing pubblicata!*\n"
        f"🔗 {esito['url']}\n"
        f"🔍 Verifica: {check}\n\n"
        f"L'URL è stato salvato e collegato all'insight di origine."
    )
