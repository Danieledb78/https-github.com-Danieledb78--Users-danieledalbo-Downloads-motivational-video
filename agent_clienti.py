"""
agent_clienti.py — Upsell e Referral Clienti Esistenti (AIOS v2.0)
I clienti esistenti sono il canale di acquisizione più economico: il referral porta
costo di acquisizione vicino allo zero — ma va CHIESTO esplicitamente, non atteso.

Logica:
- Renergy: in base all'anzianità dell'impianto propone ampliamento/storage, NPS+referral,
  comunicazioni su nuovi incentivi, completamento copertura capannone
- RS Gas: revisione/upgrade contratti maturi, rinnovo anticipato a tariffa bloccata,
  alert su variazioni di consumo anomale

Schedulato ogni giovedì alle 09:30. Trigger manuale: /clienti
"""

import os, json, logging
from datetime import datetime, timedelta
from pathlib import Path
import anthropic

import hub_client as hub
import shared_intelligence as si
import config

log = logging.getLogger(__name__)
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
claude     = anthropic.Anthropic(api_key=CLAUDE_KEY)
UPSELL_FILE = "clienti_upsell.json"

AZIENDE_NOME = {"Renergy": "Renergy Project&Build", "RS_Gas": "RS Gas&Power"}


# ---------------------------------------------------------------------------
# Storage azioni upsell/referral per cliente (anti-duplicati)
# ---------------------------------------------------------------------------
def carica_storico() -> list[dict]:
    if Path(UPSELL_FILE).exists():
        with open(UPSELL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def salva_storico(entries: list[dict]):
    with open(UPSELL_FILE, "w", encoding="utf-8") as f:
        json.dump(entries[-500:], f, ensure_ascii=False, indent=2)

def _gia_proposto(cliente_email: str, tipo_azione: str, entro_giorni: int = 90) -> bool:
    soglia = datetime.now() - timedelta(days=entro_giorni)
    for e in carica_storico():
        if e.get("email", "").lower() == (cliente_email or "").lower() and e.get("tipo") == tipo_azione:
            try:
                if datetime.fromisoformat(e["proposta_il"]) > soglia:
                    return True
            except (KeyError, ValueError):
                return True
    return False

def registra_azione(cliente: dict, azienda: str, tipo: str, dettaglio: str) -> dict:
    entry = {
        "id":          f"up_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{abs(hash(cliente.get('email','')))%10000}",
        "email":       (cliente.get("email") or "").lower().strip(),
        "nome":        cliente.get("ragione_sociale", cliente.get("name", "")),
        "azienda":     azienda,
        "tipo":        tipo,        # ampliamento|nps_referral|nuovo_incentivo|completamento|upgrade|rinnovo|alert_consumi
        "dettaglio":   dettaglio,
        "proposta_il": datetime.now().isoformat(),
        "esito":       "proposta",  # proposta | inviata | risposta_ricevuta | convertita
    }
    entries = carica_storico()
    entries.append(entry)
    salva_storico(entries)
    return entry


# ---------------------------------------------------------------------------
# Helper — anzianità impianto/contratto in mesi
# ---------------------------------------------------------------------------
def _mesi_da(data_str: str) -> int | None:
    if not data_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            data = datetime.strptime(data_str[:len(fmt.replace('%f',''))+6] if "%f" in fmt else data_str[:10], fmt)
            return (datetime.now().year - data.year) * 12 + (datetime.now().month - data.month)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Logica clienti Renergy
# ---------------------------------------------------------------------------
def analizza_clienti_renergy(clienti: list[dict]) -> list[dict]:
    azioni = []
    for c in clienti:
        email = c.get("email", "")
        if not email:
            continue
        mesi = _mesi_da(c.get("data_installazione", c.get("data_attivazione", "")))
        copertura_parziale = str(c.get("copertura", c.get("note", ""))).lower().find("parziale") >= 0

        if mesi is not None and config.UPSELL_MONTHS_RENERGY <= mesi < config.UPSELL_MONTHS_RENERGY + 6:
            tipo = "ampliamento"
            if not _gia_proposto(email, tipo):
                azioni.append({"cliente": c, "tipo": tipo,
                               "dettaglio": f"Impianto da {mesi} mesi: proposta ampliamento o aggiunta storage."})

        elif mesi is not None and config.NPS_REQUEST_MONTHS <= mesi < config.NPS_REQUEST_MONTHS + 4:
            tipo = "nps_referral"
            if not _gia_proposto(email, tipo):
                azioni.append({"cliente": c, "tipo": tipo,
                               "dettaglio": f"Impianto da {mesi} mesi: richiesta NPS e attivazione referral."})

        if mesi is not None and mesi > 24:
            tipo = "nuovo_incentivo"
            if not _gia_proposto(email, tipo, entro_giorni=180):
                azioni.append({"cliente": c, "tipo": tipo,
                               "dettaglio": f"Impianto datato ({mesi} mesi): possibile comunicazione su novità/incentivi."})

        if copertura_parziale:
            tipo = "completamento"
            if not _gia_proposto(email, tipo, entro_giorni=180):
                azioni.append({"cliente": c, "tipo": tipo,
                               "dettaglio": "Copertura parziale del capannone: proposta completamento."})
    return azioni


# ---------------------------------------------------------------------------
# Logica clienti RS Gas
# ---------------------------------------------------------------------------
def analizza_clienti_rsgas(clienti: list[dict]) -> list[dict]:
    azioni = []
    for c in clienti:
        email = c.get("email", "")
        if not email:
            continue
        mesi = _mesi_da(c.get("data_attivazione", c.get("data_contratto", "")))

        if mesi is not None and 6 <= mesi < 12:
            tipo = "upgrade"
            if not _gia_proposto(email, tipo):
                azioni.append({"cliente": c, "tipo": tipo,
                               "dettaglio": f"Contratto attivo da {mesi} mesi: revisione e proposta upgrade/ottimizzazione."})

        elif mesi is not None and mesi >= 12:
            tipo = "rinnovo"
            if not _gia_proposto(email, tipo, entro_giorni=180):
                azioni.append({"cliente": c, "tipo": tipo,
                               "dettaglio": f"Contratto da {mesi} mesi: proposta rinnovo anticipato a tariffa bloccata."})

        variazione = c.get("variazione_consumi_percent")
        try:
            if variazione is not None and abs(float(variazione)) >= 30:
                tipo = "alert_consumi"
                if not _gia_proposto(email, tipo, entro_giorni=30):
                    azioni.append({"cliente": c, "tipo": tipo,
                                   "dettaglio": f"Variazione consumi anomala ({variazione}%): alert e contatto proattivo."})
        except (TypeError, ValueError):
            pass
    return azioni


# ---------------------------------------------------------------------------
# Claude — bozza email per ogni azione (referral attivo incluso)
# ---------------------------------------------------------------------------
PROMPT_EMAIL_UPSELL = """Scrivi una bozza email breve (90-130 parole) per un cliente esistente di {azienda}.

Tipo di azione: {tipo}
Dettaglio: {dettaglio}
Tono di voce: {tono}

Se il tipo è "nps_referral", la mail DEVE seguire questo schema:
"[Nome], sei soddisfatto del [prodotto/servizio]. Conosci altri imprenditori che potrebbero
beneficiarne? Ti bastano 30 secondi: rispondi con nome ed email del tuo contatto.
Per ogni azienda che [installa/attiva], ti offriamo {incentivo}."

Rispondi SOLO con JSON: {{"oggetto": "...", "corpo": "testo email pronto, [Nome] come placeholder"}}"""

def genera_bozza_email(azienda: str, tipo: str, dettaglio: str) -> dict:
    incentivo = config.REFERRAL_INCENTIVE_RENERGY if azienda == "Renergy" else config.REFERRAL_INCENTIVE_RSGAS
    tono = config.TONO_VOCE.get(azienda, "")
    msg = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=600, timeout=config.API_TIMEOUT_SEC,
        messages=[{"role": "user", "content": PROMPT_EMAIL_UPSELL.format(
            azienda=AZIENDE_NOME.get(azienda, azienda), tipo=tipo, dettaglio=dettaglio,
            tono=tono, incentivo=incentivo,
        )}],
    )
    t = msg.content[0].text.strip()
    return json.loads(t[t.find("{"):t.rfind("}")+1])


# ---------------------------------------------------------------------------
# Shared intelligence
# ---------------------------------------------------------------------------
def _pubblica_insight(azienda: str, azioni: list[dict]):
    if not azioni:
        return
    try:
        si.aggiungi_insight("agent_clienti", {
            "azienda":             azienda,
            "tipo":                "trend",
            "titolo":              f"{len(azioni)} opportunità di upsell/referral su clienti {AZIENDE_NOME.get(azienda, azienda)}",
            "sintesi":             f"Identificate {len(azioni)} azioni di retention/upsell basate sull'anzianità "
                                   f"e sui pattern dei clienti esistenti.",
            "urgenza":             "media",
            "impatto_commerciale": "medio",
            "azioni_suggerite":    ["contatto_diretto"],
            "target_icp":          "Clienti esistenti con anzianità o pattern idonei a upsell/referral",
        })
    except Exception as e:
        log.warning(f"Pubblicazione insight clienti fallita: {e}")


# ---------------------------------------------------------------------------
# Formattazione Telegram
# ---------------------------------------------------------------------------
TIPO_LABEL = {
    "ampliamento": "📈 Proposta ampliamento/storage", "nps_referral": "🎯 NPS + Referral attivo",
    "nuovo_incentivo": "🆕 Comunicazione nuovo incentivo", "completamento": "🏗 Completamento copertura",
    "upgrade": "⬆️ Revisione/upgrade contratto", "rinnovo": "🔒 Rinnovo anticipato a tariffa bloccata",
    "alert_consumi": "⚠️ Alert variazione consumi",
}

def formatta_riepilogo(azioni_renergy: list[dict], azioni_rsgas: list[dict]) -> str:
    totale = len(azioni_renergy) + len(azioni_rsgas)
    if totale == 0:
        return "👥 *Clienti — Upsell & Referral* — nessuna nuova azione da proporre questa settimana."
    righe = [f"👥 *Clienti — Upsell & Referral — {totale} azioni proposte*\n"]
    for nome, azioni, emoji in (("Renergy Project&Build", azioni_renergy, "⚡"),
                                ("RS Gas&Power", azioni_rsgas, "⚡🔵")):
        if not azioni:
            continue
        righe.append(f"{emoji} *{nome}*")
        for a in azioni[:5]:
            righe.append(f"   {TIPO_LABEL.get(a['tipo'], a['tipo'])} — "
                         f"{a['cliente'].get('ragione_sociale', a['cliente'].get('name','(senza nome)'))}")
            righe.append(f"      _{a['dettaglio']}_")
    righe.append("\n💡 Referral attivo: il sistema chiede esplicitamente la segnalazione, non la attende.")
    righe.append("_Usa /clienti per generare e approvare le bozze email._")
    return "\n".join(righe)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run() -> tuple[list[dict], list[dict], str]:
    log.info("=== AGENTE CLIENTI — avvio ===")
    try:
        clienti_renergy = hub.get_clienti_per_azienda(AZIENDE_NOME["Renergy"], stato="installato")
    except Exception as e:
        log.warning(f"Lettura clienti Renergy fallita: {e}")
        clienti_renergy = []
    try:
        clienti_rsgas = hub.get_clienti_per_azienda(AZIENDE_NOME["RS_Gas"], stato="attivo")
    except Exception as e:
        log.warning(f"Lettura clienti RS Gas fallita: {e}")
        clienti_rsgas = []

    azioni_renergy = analizza_clienti_renergy(clienti_renergy)
    azioni_rsgas   = analizza_clienti_rsgas(clienti_rsgas)

    for azione in azioni_renergy:
        registra_azione(azione["cliente"], "Renergy", azione["tipo"], azione["dettaglio"])
    for azione in azioni_rsgas:
        registra_azione(azione["cliente"], "RS_Gas", azione["tipo"], azione["dettaglio"])

    _pubblica_insight("Renergy", azioni_renergy)
    _pubblica_insight("RS_Gas", azioni_rsgas)

    messaggio = formatta_riepilogo(azioni_renergy, azioni_rsgas)
    log.info(f"=== AGENTE CLIENTI — {len(azioni_renergy)+len(azioni_rsgas)} azioni proposte ===")
    return azioni_renergy, azioni_rsgas, messaggio
