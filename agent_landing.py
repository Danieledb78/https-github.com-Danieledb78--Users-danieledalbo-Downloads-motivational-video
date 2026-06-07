"""
agent_landing.py — Generatore Landing Page Automatico (AIOS v2.0)
Genera con Claude Opus una landing page HTML/CSS/JS completa, brandizzata per azienda,
e la pubblica automaticamente via FTPS sul sottodominio dedicato di ciascuna azienda
(landing.renergygroup.it | landing.rsgaspower.it | landing.acmpartners.it), dopo
approvazione dell'owner.

Trigger: chiamato dall'orchestratore su approvazione owner, oppure manualmente con /landing
Output: file HTML in /tmp + deploy FTPS + URL salvato in shared_intelligence.json
"""

import os, json, time, logging, re, ftplib
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

# Deploy via FTPS sul sottodominio "landing" del dominio di ciascuna azienda.
# Ogni landing viene caricata in una sottocartella <slug>/index.html, raggiungibile
# all'URL pubblico <url_base>/<slug>/
FTP_DEPLOY = {
    "Renergy": {
        "host":     os.environ.get("FTP_HOST_RENERGY"),
        "user":     os.environ.get("FTP_USER_RENERGY"),
        "password": os.environ.get("FTP_PASSWORD_RENERGY"),
        "dir":      os.environ.get("FTP_DIR_RENERGY", "/"),
        "url_base": os.environ.get("LANDING_URL_BASE_RENERGY", "https://landing.renergygroup.it"),
    },
    "RS_Gas": {
        "host":     os.environ.get("FTP_HOST_RSGAS"),
        "user":     os.environ.get("FTP_USER_RSGAS"),
        "password": os.environ.get("FTP_PASSWORD_RSGAS"),
        "dir":      os.environ.get("FTP_DIR_RSGAS", "/"),
        "url_base": os.environ.get("LANDING_URL_BASE_RSGAS", "https://landing.rsgaspower.it"),
    },
    "ACM": {
        "host":     os.environ.get("FTP_HOST_ACM"),
        "user":     os.environ.get("FTP_USER_ACM"),
        "password": os.environ.get("FTP_PASSWORD_ACM"),
        "dir":      os.environ.get("FTP_DIR_ACM", "/"),
        "url_base": os.environ.get("LANDING_URL_BASE_ACM", "https://landing.acmpartners.it"),
    },
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


def salva_html(slug: str, html: str) -> str:
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"/tmp/landing_{slug}_{ts}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Landing salvata: {path}")
    return path


# ---------------------------------------------------------------------------
# Deploy automatico via FTPS sul sottodominio "landing" del dominio aziendale
# ---------------------------------------------------------------------------
def _ftp_mkdir_ricorsivo(ftp: ftplib.FTP, percorso: str):
    """Crea ogni livello mancante della cartella remota (mkdir -p), ignorando
    l'errore se il livello esiste già."""
    corrente = ""
    for livello in (l for l in percorso.split("/") if l):
        corrente += f"/{livello}"
        try:
            ftp.mkd(corrente)
        except ftplib.error_perm:
            pass


def deploy_ftp(html_path: str, azienda: str, slug: str) -> dict:
    """Carica l'HTML come index.html in <dir>/<slug>/ via FTPS sul sottodominio
    'landing' dell'azienda e ritorna l'URL pubblico risultante."""
    cfg = FTP_DEPLOY.get(azienda, {})
    if not cfg.get("host") or not cfg.get("user") or not cfg.get("password"):
        return {"ok": False, "errore": f"FTP non configurato per {azienda} (host/user/password mancanti)"}

    cartella_remota = f"{cfg['dir'].rstrip('/')}/{slug}"
    try:
        ftp = ftplib.FTP_TLS(cfg["host"], timeout=30)
        ftp.login(cfg["user"], cfg["password"])
        ftp.prot_p()
        _ftp_mkdir_ricorsivo(ftp, cartella_remota)
        ftp.cwd(cartella_remota)
        with open(html_path, "rb") as f:
            ftp.storbinary("STOR index.html", f)
        ftp.quit()
        url = f"{cfg['url_base'].rstrip('/')}/{slug}/"
        return {"ok": True, "url": url}
    except Exception as e:
        log.warning(f"FTP — deploy fallito ({azienda}): {e}")
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
                      slug: str, url: str | None = None) -> dict:
    registro = carica_registro()
    entry = {
        "id":         f"land_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "azienda":    azienda,
        "insight_id": insight_id,
        "titolo":     titolo,
        "slug":       slug,
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
    titolo    = insight.get("titolo", "")
    slug      = _slug(f"{azienda}-{titolo}")
    html      = genera_html_landing(azienda, titolo, insight.get("target_icp", ""))
    html_path = salva_html(slug, html)
    entry     = registra_landing(azienda, insight.get("id", ""), titolo, html_path, slug)
    return entry

def pubblica_landing(land_id: str) -> dict:
    """Esegue il deploy via FTPS sul sottodominio dedicato per una landing già
    generata e ne verifica l'esito."""
    entry = get_landing(land_id)
    if not entry:
        return {"ok": False, "errore": "Landing non trovata"}

    esito = deploy_ftp(entry["html_path"], entry["azienda"], entry.get("slug") or _slug(entry.get("titolo", land_id)))
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
        f"File: `{entry.get('html_path','')}`\n"
        f"URL previsto: `{FTP_DEPLOY.get(entry['azienda'],{}).get('url_base','')}/{entry.get('slug','')}/`\n\n"
        f"Premi *Deploy* per pubblicarla sul sottodominio dedicato, oppure scarica il file per revisionarlo prima."
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
