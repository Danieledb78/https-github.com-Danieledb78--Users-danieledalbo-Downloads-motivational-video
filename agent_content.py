"""
agent_content.py — Agente Content Creator
Genera post LinkedIn e articoli blog per Renergy e ACM&Partners
partendo dalle notizie di mercato e dai trend ESG/energia.
I contenuti vengono messi in coda (content_queue.json) per revisione su Telegram.
"""

import os, json, logging, feedparser
from datetime import datetime
from pathlib import Path
import anthropic

log = logging.getLogger(__name__)
CLAUDE_KEY  = os.environ.get("CLAUDE_API_KEY")
claude      = anthropic.Anthropic(api_key=CLAUDE_KEY)
QUEUE_FILE  = "content_queue.json"

FEED_URLS = [
    "https://news.google.com/rss/search?q=fotovoltaico+incentivi+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=ESG+Italia+2026&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=energia+rinnovabile+imprese+Italia&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=efficienza+energetica+PMI+Italia&hl=it&gl=IT&ceid=IT:it",
    "https://news.google.com/rss/search?q=consulenza+aziendale+PMI+crescita+Italia&hl=it&gl=IT&ceid=IT:it",
]

MAX_ARTICOLI = 4


# ---------------------------------------------------------------------------
# Raccolta notizie
# ---------------------------------------------------------------------------
def raccogli_notizie() -> list[dict]:
    notizie, visti = [], set()
    for url in FEED_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_ARTICOLI]:
                titolo = entry.get("title","").strip()
                if titolo in visti:
                    continue
                visti.add(titolo)
                sommario = entry.get("summary", entry.get("description",""))
                sommario = sommario.replace("<b>","").replace("</b>","").replace("<br>","")
                notizie.append({"titolo": titolo, "sommario": sommario[:300]})
        except Exception as e:
            log.warning(f"Feed error: {e}")
    return notizie


# ---------------------------------------------------------------------------
# Claude — genera contenuti
# ---------------------------------------------------------------------------
PROMPT_LINKEDIN = """Sei un content creator LinkedIn esperto di business B2B in Italia.
Crea un post LinkedIn per l'azienda indicata, partendo dalla notizia fornita.

Azienda: {azienda}
Profilo azienda: {profilo}
Notizia/spunto: {spunto}

Regole:
- Lunghezza: 120-180 parole
- Tono: professionale ma diretto, non promozionale
- Inizia con un hook forte (domanda, dato o affermazione sorprendente)
- 1 insight chiave per il lettore
- Call to action finale (es. "Scrivimi se vuoi approfondire", "Commenta la tua esperienza")
- 4-6 hashtag rilevanti in fondo

Rispondi SOLO con JSON:
{{
  "titolo_interno": "titolo breve per identificare il post",
  "testo": "testo completo del post con hashtag",
  "hashtag": ["#tag1","#tag2"],
  "angolo": "risparmio | normativa | innovazione | caso_studio | trend"
}}"""

PROMPT_BLOG = """Sei un esperto SEO copywriter italiano specializzato in energia rinnovabile ed ESG.
Scrivi un articolo blog professionale per il sito dell'azienda indicata.

Azienda: {azienda}
Profilo: {profilo}
Argomento: {argomento}

Struttura richiesta:
- Titolo SEO-friendly con keyword principale
- Introduzione (60-80 parole): problema/contesto
- 3 sezioni con sottotitolo H2 (80-100 parole ciascuna)
- Conclusione con CTA (40-50 parole)
- Meta description (max 155 caratteri)

Rispondi SOLO con JSON:
{{
  "titolo": "titolo articolo",
  "meta_description": "...",
  "keyword_principale": "...",
  "testo": "articolo completo in markdown",
  "stima_parole": 350
}}"""

PROFILI = {
    "Renergy Project&Build": (
        "Installa impianti fotovoltaici industriali, realizza riqualificazioni energetiche "
        "e stazioni EV charging per PMI e industrie in Veneto e Nord Italia."
    ),
    "ACM&Partners": (
        "Consulenza manageriale per PMI italiane: strategia ESG, M&A, "
        "crescita aziendale, reporting sostenibilità."
    ),
}

def genera_post_linkedin(azienda: str, spunto: str) -> dict:
    profilo = PROFILI.get(azienda, "")
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=1200,
        messages=[{"role": "user", "content": PROMPT_LINKEDIN.format(
            azienda=azienda, profilo=profilo, spunto=spunto
        )}],
    )
    t = msg.content[0].text.strip()
    result = json.loads(t[t.find("{"):t.rfind("}")+1])
    result["tipo"]    = "linkedin"
    result["azienda"] = azienda
    result["spunto"]  = spunto[:200]
    return result

def genera_articolo_blog(azienda: str, argomento: str) -> dict:
    profilo = PROFILI.get(azienda, "")
    msg = claude.messages.create(
        model="claude-opus-4-8", max_tokens=2500,
        messages=[{"role": "user", "content": PROMPT_BLOG.format(
            azienda=azienda, profilo=profilo, argomento=argomento
        )}],
    )
    t = msg.content[0].text.strip()
    result = json.loads(t[t.find("{"):t.rfind("}")+1])
    result["tipo"]    = "blog"
    result["azienda"] = azienda
    return result


# ---------------------------------------------------------------------------
# Coda contenuti
# ---------------------------------------------------------------------------
def carica_queue() -> list[dict]:
    if Path(QUEUE_FILE).exists():
        with open(QUEUE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_queue(queue: list[dict]):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

def aggiungi_alla_queue(item: dict):
    queue = carica_queue()
    item["id"]       = f"cnt_{len(queue)+1:04d}"
    item["creato"]   = datetime.now().isoformat()
    item["stato"]    = "in_attesa"   # in_attesa | approvato | pubblicato | scartato
    queue.append(item)
    salva_queue(queue)
    return item

def get_contenuti_in_attesa() -> list[dict]:
    return [c for c in carica_queue() if c.get("stato") == "in_attesa"]

def aggiorna_stato_contenuto(cnt_id: str, nuovo_stato: str):
    queue = carica_queue()
    for c in queue:
        if c["id"] == cnt_id:
            c["stato"] = nuovo_stato
            c["aggiornato"] = datetime.now().isoformat()
    salva_queue(queue)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> list[dict]:
    """Genera contenuti settimanali e li mette in coda."""
    log.info("=== AGENTE CONTENT — avvio ===")
    notizie = raccogli_notizie()
    if not notizie:
        log.warning("Nessuna notizia raccolta")
        return []

    creati = []
    # 1 post LinkedIn per Renergy
    try:
        spunto_r = f"{notizie[0]['titolo']}: {notizie[0]['sommario']}"
        post_r   = genera_post_linkedin("Renergy Project&Build", spunto_r)
        creati.append(aggiungi_alla_queue(post_r))
    except Exception as e:
        log.warning(f"LinkedIn Renergy: {e}")

    # 1 post LinkedIn per ACM
    try:
        spunto_a = f"{notizie[min(2,len(notizie)-1)]['titolo']}: {notizie[min(2,len(notizie)-1)]['sommario']}"
        post_a   = genera_post_linkedin("ACM&Partners", spunto_a)
        creati.append(aggiungi_alla_queue(post_a))
    except Exception as e:
        log.warning(f"LinkedIn ACM: {e}")

    # 1 articolo blog Renergy
    try:
        argomento = notizie[min(1,len(notizie)-1)]["titolo"]
        blog_r    = genera_articolo_blog("Renergy Project&Build", argomento)
        creati.append(aggiungi_alla_queue(blog_r))
    except Exception as e:
        log.warning(f"Blog Renergy: {e}")

    log.info(f"=== AGENTE CONTENT — creati {len(creati)} contenuti ===")
    return creati


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
def formatta_preview_contenuto(c: dict, breve: bool = True) -> str:
    tipo_emoji = {"linkedin":"💼","blog":"📝"}.get(c.get("tipo",""),"📄")
    az_emoji   = {"Renergy Project&Build":"⚡","ACM&Partners":"🏢"}.get(c.get("azienda",""),"•")
    testo = c.get("testo","")
    preview = testo[:250] + "..." if len(testo) > 250 and breve else testo
    righe = [
        f"{tipo_emoji}{az_emoji} *[{c['id']}]* {c.get('titolo_interno', c.get('titolo',''))}",
        f"   🏢 {c.get('azienda','')} | 📅 {c.get('creato','')[:10]}",
    ]
    if not breve:
        righe.append(f"\n{preview}")
    return "\n".join(righe)
