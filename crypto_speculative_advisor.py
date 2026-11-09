#!/usr/bin/env python3
"""Moteur d'analyse et de recommandation spéculative Crypto v4.0.0 (Découverte Dynamique de Marché & Confluence IA SSOT)."""

import datetime
import json
import logging
import os
import re
import requests
from dotenv import load_dotenv

from db_manager import add_recommendation, get_open_positions, init_db, get_all_core_metadata
from exchange_connectors import (
    get_all_exchange_crypto_balances,
    get_total_verified_cash,
)
from gemini_manager import call_gemini_with_fallback
from prompt_loader import get_system_prompt

# 1. Contexte Macro SSOT & Boussole de Flux
try:
    from macro_market_engine import (
        get_complete_macro_context,
        fetch_fear_and_greed_index as macro_fng,
        check_circuit_breaker as macro_cb,
        format_macro_context_for_prompt as macro_format_prompt,
    )
    MACRO_ENGINE_AVAILABLE = True
except ImportError:
    MACRO_ENGINE_AVAILABLE = False

# 2. Moteur de Découverte Dynamique du Marché (v4.0.0)
try:
    from market_discovery_engine import get_dynamic_speculative_candidates
    DISCOVERY_ENGINE_AVAILABLE = True
except ImportError:
    DISCOVERY_ENGINE_AVAILABLE = False

# 3. Screener RSI Multi-TF
try:
    from rsi_engine import build_rsi_heatmap_dataframe
    RSI_ENGINE_AVAILABLE = True
except ImportError:
    RSI_ENGINE_AVAILABLE = False

# 4. Microstructure (Funding Rates & Open Interest)
try:
    from funding_oi_engine import get_funding_oi_summary
    FUNDING_ENGINE_AVAILABLE = True
except ImportError:
    FUNDING_ENGINE_AVAILABLE = False

load_dotenv()
init_db()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

SPECULATIVE_PCT = float(os.getenv("CRYPTO_SPECULATIVE_PCT", "0.10"))


def check_circuit_breaker():
    """Vérifie si le marché est en mode crash via le moteur Macro SSOT ou fallback."""
    if MACRO_ENGINE_AVAILABLE:
        try:
            return macro_cb()
        except Exception as e:
            logging.warning(f"⚠️ Erreur macro_market_engine (circuit_breaker) : {e}")

    change_24h = 0.0
    try:
        res = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=eur&include_24hr_change=true",
            timeout=5,
        )
        if res.status_code == 200:
            val = res.json().get("bitcoin", {}).get("eur_24h_change")
            if val is not None and val != 0.0:
                change_24h = float(val)
                return (change_24h <= -5.0), change_24h
    except Exception as e:
        logging.warning(f"⚠️ Erreur BTC 24h CoinGecko fallback : {e}")

    return False, round(change_24h, 2)


def fetch_fear_and_greed_index():
    """Récupère l'indice Fear & Greed via le moteur Macro SSOT ou fallback."""
    if MACRO_ENGINE_AVAILABLE:
        try:
            return macro_fng()
        except Exception as e:
            logging.warning(f"⚠️ Erreur macro_market_engine (fng) : {e}")

    try:
        res = requests.get("https://api.alternative.me/fng/", timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])[0]
            return {
                "value": int(data.get("value", 50)),
                "classification": data.get("value_classification", "Neutral"),
            }
    except Exception as e:
        logging.warning(f"⚠️ Erreur Fear & Greed API fallback : {e}")
    return {"value": 50, "classification": "Neutral"}


def fetch_live_price(sym, exch="OKX", eur_usd_rate=1.12):
    """Récupère le prix spot en direct converti en EUR avec isolation stricte des exchanges."""
    clean_sym = str(sym).upper().replace("USDT", "").replace("EUR", "").strip()
    clean_exch = str(exch).upper().strip() if exch else ""
    rate = eur_usd_rate if eur_usd_rate and eur_usd_rate > 0 else 1.12

    if clean_sym in ["EUR", "USD"]:
        return 1.0 if clean_sym == "EUR" else (1.0 / rate)

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    }

    # 1. Routage prioritaire Kraken
    if "KRAKEN" in clean_exch:
        for pair in [f"{clean_sym}EUR", f"X{clean_sym}ZEUR"]:
            try:
                res = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                    headers=headers,
                    timeout=3.0,
                )
                if res.status_code == 200:
                    result = res.json().get("result", {})
                    if result:
                        key = list(result.keys())[0]
                        price = float(result[key]["c"][0])
                        if price > 0:
                            return price
            except Exception:
                pass

        for pair in [f"{clean_sym}USD", f"{clean_sym}USDT"]:
            try:
                res = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                    headers=headers,
                    timeout=3.0,
                )
                if res.status_code == 200:
                    result = res.json().get("result", {})
                    if result:
                        key = list(result.keys())[0]
                        price = float(result[key]["c"][0])
                        if price > 0:
                            return price / rate
            except Exception:
                pass

        return None

    # 2. Routage OKX
    try:
        res = requests.get(
            f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-EUR",
            headers=headers,
            timeout=2.0,
        )
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                price = float(data[0].get("last", 0))
                if price > 0:
                    return price
    except Exception:
        pass

    try:
        res = requests.get(
            f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-USDT",
            headers=headers,
            timeout=2.0,
        )
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                price = float(data[0].get("last", 0))
                if price > 0:
                    return price / rate
    except Exception:
        pass

    # 3. Fallback Kraken générique
    try:
        res_k = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={clean_sym}EUR",
            headers=headers,
            timeout=2.0,
        )
        if res_k.status_code == 200:
            result_k = res_k.json().get("result", {})
            if result_k:
                key = list(result_k.keys())[0]
                price = float(result_k[key]["c"][0])
                if price > 0:
                    return price
    except Exception:
        pass

    return 0.0


def send_telegram_with_inline_buttons(text, inline_keyboard=None):
    """
    Envoie un message formaté sur Telegram avec découpage intelligent <= 3900 caractères
    et fallback automatique en texte brut pour garantir une réception sans faille.
    """
    if not BOT_TOKEN or not ALLOWED_USER_ID:
        logging.warning("⚠️ Configuration Telegram manquante (BOT_TOKEN ou ALLOWED_USER_ID non configuré).")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    max_len = 3900
    chunks = []
    if len(text) <= max_len:
        chunks = [text]
    else:
        current_chunk = ""
        for line in text.splitlines(keepends=True):
            while len(line) > max_len:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                chunks.append(line[:max_len])
                line = line[max_len:]
            if len(current_chunk) + len(line) > max_len:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += line
        if current_chunk.strip():
            chunks.append(current_chunk)

    success = True
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        kb = inline_keyboard if is_last else None

        payload = {
            "chat_id": ALLOWED_USER_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if kb:
            payload["reply_markup"] = {"inline_keyboard": kb}

        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code != 200:
                logging.warning(
                    f"⚠️ Échec Telegram Markdown (HTTP {res.status_code} - {res.text}) : "
                    "Nouvelle tentative immédiate en texte brut..."
                )
                payload.pop("parse_mode", None)
                res_fallback = requests.post(url, json=payload, timeout=10)
                if res_fallback.status_code != 200:
                    logging.error(f"❌ Échec envoi Telegram fallback (HTTP {res_fallback.status_code}) : {res_fallback.text}")
                    success = False
                else:
                    logging.info(f"✅ Message Telegram ({i+1}/{len(chunks)}) délivré avec succès (fallback texte brut).")
            else:
                logging.info(f"✅ Message Telegram ({i+1}/{len(chunks)}) délivré avec succès.")
        except Exception as e:
            logging.error(f"❌ Erreur réseau Telegram : {e}")
            success = False

    return success


def parse_llm_json(raw_text):
    """
    Extrait et décode proprement une structure JSON renvoyée par le LLM,
    même en présence de séquences d'échappement non conformes (\$, \*, \_, etc.).
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # 1. Tentative directe
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    # 2. Nettoyage des antislashs d'échappement invalides dans les chaînes JSON
    # Répare les séquences comme \* ou \$ qui ne sont pas des échappements JSON valides
    cleaned = re.sub(r'\\([^"\\/bfnrtu])', r'\1', text)
    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    # 3. Extraction par bloc regex si du texte parasite entoure le JSON
    match = re.search(r"(\{[\s\S]*\})", text)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate, strict=False)
        except Exception:
            candidate_cleaned = re.sub(r'\\([^"\\/bfnrtu])', r'\1', candidate)
            return json.loads(candidate_cleaned, strict=False)

    raise ValueError(f"Impossible de parser la réponse JSON du LLM : {text[:200]}...")


def calculate_unified_crypto_categories(eur_usd_rate=1.12):
    """Calcul 100% autonome et SSOT unifié avec le Dashboard v3.9.1 (Exchanges + Exodus + BDD SQLite)."""
    all_crypto_balances = get_all_exchange_crypto_balances()
    positions = get_open_positions()

    speculative_symbols = {
        str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
        for p in positions
        if len(p) > 1 and p[1]
    }

    val_majors = 0.0
    val_core = 0.0
    val_speculative = 0.0
    val_real_fiat = 0.0
    val_stables_crypto = 0.0

    holdings_by_asset = {}

    # 1. Positions spéculatives actives BDD
    for p in positions:
        sym = str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
        exch = p[2] if len(p) > 2 else ""
        entry_eur = float(p[3]) if p[3] is not None else 0.0
        amount_eur = float(p[4]) if p[4] is not None else 0.0
        lp = fetch_live_price(sym, exch, eur_usd_rate=eur_usd_rate) or entry_eur
        qty = (amount_eur / entry_eur) if entry_eur > 0 else 0.0
        current_val = qty * lp
        val_speculative += current_val

        holdings_by_asset[sym] = holdings_by_asset.get(sym, 0.0) + current_val

    # 2. Soldes réels Exchanges & Wallets
    if all_crypto_balances:
        for asset, info in all_crypto_balances.items():
            amt = info.get("amount", 0.0) if isinstance(info, dict) else float(info)
            exch = info.get("exchange", "") if isinstance(info, dict) else ""
            asset_u = str(asset).upper().strip()

            if asset_u in ["EUR", "USD"]:
                val_real_fiat += amt if asset_u == "EUR" else (amt / eur_usd_rate)
            elif asset_u in ["USDT", "USDC", "DAI", "FDUSD"]:
                val_v = (amt / eur_usd_rate) if asset_u in ["USDT", "USDC"] else amt
                val_stables_crypto += val_v
                holdings_by_asset[asset_u] = holdings_by_asset.get(asset_u, 0.0) + val_v
            elif asset_u in ["BTC", "ETH"]:
                lp = fetch_live_price(asset_u, exch, eur_usd_rate=eur_usd_rate) or 0.0
                asset_val = amt * lp
                val_majors += asset_val
                holdings_by_asset[asset_u] = holdings_by_asset.get(asset_u, 0.0) + asset_val
            elif asset_u in speculative_symbols:
                pass
            else:
                lp = fetch_live_price(asset_u, exch, eur_usd_rate=eur_usd_rate) or 0.0
                asset_val = amt * lp
                val_core += asset_val
                holdings_by_asset[asset_u] = holdings_by_asset.get(asset_u, 0.0) + asset_val

    total_cash_stables = val_real_fiat + val_stables_crypto
    if total_cash_stables == 0:
        try:
            cash_data = get_total_verified_cash()
            total_cash_stables = cash_data.get("total_cash", 0.0)
            val_real_fiat = total_cash_stables
        except Exception:
            cash_data = {"total_cash": 0.0}
    else:
        cash_data = {
            "total_cash": round(total_cash_stables, 2),
            "fiat": round(val_real_fiat, 2),
            "stables": round(val_stables_crypto, 2),
        }

    total_crypto = val_majors + val_core + val_speculative + total_cash_stables

    if total_crypto > 0:
        pct_majors = (val_majors / total_crypto) * 100.0
        pct_core = (val_core / total_crypto) * 100.0
        pct_spec = (val_speculative / total_crypto) * 100.0
        pct_stables = (total_cash_stables / total_crypto) * 100.0
    else:
        pct_majors = pct_core = pct_spec = pct_stables = 0.0

    holdings_lines = []
    for asset, val_eur in sorted(holdings_by_asset.items(), key=lambda x: x[1], reverse=True):
        if val_eur >= 0.5:
            holdings_lines.append(f"• {asset} : {val_eur:.2f} €")

    return {
        "total_crypto": round(total_crypto, 2),
        "btc_eth": round(val_majors, 2),
        "btc_eth_pct": round(pct_majors, 1),
        "core": round(val_core, 2),
        "core_pct": round(pct_core, 1),
        "speculative": round(val_speculative, 2),
        "speculative_pct": round(pct_spec, 1),
        "stables_cash": round(total_cash_stables, 2),
        "stables_cash_pct": round(pct_stables, 1),
        "val_stables_crypto": round(val_stables_crypto, 2),
        "total_cash_fiat": round(val_real_fiat, 2),
        "holdings_text": "\n".join(holdings_lines) if holdings_lines else "Aucun actif significatif",
        "cash_data": cash_data,
    }


calculate_unfied_crypto_categories = calculate_unified_crypto_categories
calculate_exact_crypto_categories = calculate_unified_crypto_categories


def build_dynamic_market_context(open_pos_list, dynamic_candidates):
    """
    Construit le bloc technique (RSI Multi-TF & Microstructure Funding/OI)
    sur l'univers dynamique combiné : positions ouvertes + candidats découverts.
    """
    all_symbols = set()
    for p in open_pos_list:
        if isinstance(p, dict) and "symbole" in p:
            all_symbols.add(p["symbole"])
        elif isinstance(p, (list, tuple)) and len(p) > 1 and p[1]:
            all_symbols.add(str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip())

    for c in dynamic_candidates:
        all_symbols.add(c["symbol"])

    all_symbols.update({"BTC", "ETH", "SOL"})
    sorted_syms = sorted(list(all_symbols))

    rsi_str = "RSI Non disponible"
    if RSI_ENGINE_AVAILABLE:
        try:
            df_rsi = build_rsi_heatmap_dataframe(sorted_syms)
            if not df_rsi.empty:
                rsi_str = df_rsi.to_string(index=False)
        except Exception as e:
            logging.warning(f"⚠️ Erreur RSI engine : {e}")

    foi_str = "Microstructure Non disponible"
    if FUNDING_ENGINE_AVAILABLE:
        try:
            foi_data = get_funding_oi_summary(sorted_syms)
            if foi_data and "pairs" in foi_data and foi_data["pairs"]:
                df_foi = foi_data.get("pairs", [])
                lines = [
                    f"Funding Rate Moyen (8h) : {foi_data.get('avg_funding_rate', 0):+.4f}%",
                    f"Open Interest Total : {foi_data.get('total_open_interest_usd', 0)/1e6:,.1f} M$",
                    f"Paires en Surchauffe Longs (>+0.03%) : {foi_data.get('overheated_longs_count', 0)} | Squeeze Potentiels (<-0.01%) : {foi_data.get('short_squeeze_count', 0)}\n"
                ]
                for p_item in df_foi[:15]:
                    lines.append(f"• {p_item.get('symbol')} ({p_item.get('exchange')}) : FR 8h={p_item.get('funding_rate_8h_pct', 0):+.4f}% | OI={p_item.get('open_interest_usd', 0)/1e6:,.1f}M$ | Biais={p_item.get('bias_status')}")
                foi_str = "\n".join(lines)
        except Exception as e:
            logging.warning(f"⚠️ Erreur Funding engine : {e}")

    return f"""
---
📊 MATRICE TECHNIQUE & SCREENER RSI MULTI-TIMEFRAME (POSITIONS OUVERTES + DÉCOUVERTE DYNAMIQUE) :
{rsi_str}

🔥 MICROSTRUCTURE DE MARCHÉ & DÉRIVÉS (FUNDING RATES 8H & OPEN INTEREST) :
{foi_str}
"""


def generate_crypto_speculative_report():
    """Génère le rapport quotidien spéculatif de 19h via Gemini SSOT v4.0.0 avec découverte dynamique de marché."""
    logging.info("🚀 [v4.0.0] Démarrage du Rapport Spéculatif (Découverte Dynamique + Confluence IA)...")

    is_crash, crash_pct = check_circuit_breaker()
    fng = fetch_fear_and_greed_index()
    open_positions_db = get_open_positions()

    # Benchmark BTC Live pour calcul d'Alpha d'entrée
    btc_live_eur = fetch_live_price("BTC", "KRAKEN") or fetch_live_price("BTC", "OKX") or 1.0

    # 1. Enregistrement des positions ouvertes avec Alpha vs BTC en direct
    formatted_open_positions = []
    for p in open_positions_db:
        p_id = p[0]
        sym = str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
        exch = p[2] if len(p) > 2 else "OKX"
        entry_p = float(p[3] or 0.0)
        amt_e = float(p[4] or 0.0)
        tp1_p = float(p[5] or 0.0) if len(p) > 5 else 0.0
        sl_p = float(p[7] or 0.0) if len(p) > 7 else 0.0
        step_val = p[9] if len(p) > 9 and p[9] else "ENTRY"
        btc_entry = float(p[12] or 0.0) if len(p) > 12 and p[12] else btc_live_eur

        lp = fetch_live_price(sym, exch) or entry_p
        qty = (amt_e / entry_p) if entry_p > 0 else 0.0
        val_cur = qty * lp
        pnl_pct = ((lp - entry_p) / entry_p * 100.0) if entry_p > 0 else 0.0
        btc_pnl = ((btc_live_eur - btc_entry) / btc_entry * 100.0) if btc_entry > 0 else 0.0
        alpha_btc = pnl_pct - btc_pnl

        formatted_open_positions.append({
            "id": p_id,
            "symbole": sym,
            "exchange": exch,
            "valeur_actuelle_eur": round(val_cur, 2),
            "prix_entree": round(entry_p, 4),
            "prix_actuel": round(lp, 4),
            "pnl_pct": round(pnl_pct, 2),
            "alpha_vs_btc_pct": round(alpha_btc, 2),
            "tp1": round(tp1_p, 4),
            "sl": round(sl_p, 4),
            "etape": step_val,
        })

    # 2. Ingestion des catégories et liquidités globales
    cats = calculate_unified_crypto_categories()
    total_crypto = cats["total_crypto"]
    target_budget = total_crypto * SPECULATIVE_PCT

    # 3. Moteur de Découverte Dynamique du Marché (Top 15 pépites liquides filtrées)
    dynamic_candidates = []
    dynamic_symbols = []
    if DISCOVERY_ENGINE_AVAILABLE and not is_crash:
        try:
            dynamic_candidates, dynamic_symbols = get_dynamic_speculative_candidates(
                top_n=15, min_volume_usd=4_000_000, force_refresh=True
            )
            logging.info(f"🔍 Vivier dynamique injecté : {len(dynamic_candidates)} candidats ({', '.join(dynamic_symbols[:6])}...)")
        except Exception as e:
            logging.error(f"⚠️ Erreur lors de la découverte dynamique : {e}")

    # 4. Matrice Technique & Microstructure sur l'univers découvert
    tech_micro_block = build_dynamic_market_context(formatted_open_positions, dynamic_candidates)

    # 5. Contexte Macro SSOT & Boussole de Flux
    macro_prompt_block = (
        macro_format_prompt()
        if MACRO_ENGINE_AVAILABLE
        else f"DONNÉES DU MARCHÉ ET CONTEXTE MACRO :\n- Statut Circuit Breaker : {'🚨 DEFENSIVE MODE' if is_crash else '✅ NORMAL'} (Var BTC 24h : {crash_pct:+.2f}%)\n- Indice Fear & Greed : {fng['value']}/100 ({fng['classification']})"
    )

    # 6. Extraction des métadonnées CORE pour rappel anti-doublon formel
    core_meta_dict = {}
    try:
        core_meta_dict = get_all_core_metadata()
    except Exception:
        pass
    core_summary = ", ".join([f"{k} ({v.get('thesis_status')})" for k, v in core_meta_dict.items()]) if core_meta_dict else "SOL, BNB, LINK, SUI, XRP"

    system_instruction = get_system_prompt("advisor_speculative")

    user_context = f"""DONNÉES CHIFFRÉES TEMPS RÉEL DU PORTEFEUILLE (EXCHANGES + EXODUS) :
- Valeur totale du portefeuille : {total_crypto:,.2f} €
- Enveloppe spéculative cible ({SPECULATIVE_PCT*100:.0f}%) : {target_budget:,.2f} €

VENTILATION EXACTE DU CAPITAL (SSOT DASHBOARD) :
• Majeures (BTC & ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) [Cible : 55%]
• Poche CORE (Long Terme) : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) [Cible : 22%]
• Spéculatif Actif (Moonbag BDD) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) [Cible : 10% / Max : 12%]
• Liquidités Totales (Cash + Stables) : {cats['stables_cash']:,.2f} € ({cats['stables_cash_pct']:.1f}%) [Cible : 13%]

DÉTAIL DU CASH MOBILISABLE :
• Stablecoins (USDT/USDC/DAI/FDUSD) : {cats['val_stables_crypto']:,.2f} €
• Cash Fiat (EUR/USD) : {cats['total_cash_fiat']:,.2f} €

ACTIFS DÉTENUS EN POCHE CORE (INTERDICTION STRICTE ANTI-DOUBLON) :
{core_summary}

POSITIONS SPÉCULATIVES ACTIVES EN BDD (AVEC ALPHA VS BTC EN DIRECT) :
{json.dumps(formatted_open_positions, indent=2, ensure_ascii=False)}

{macro_prompt_block}

VIVIER DYNAMIQUE DU MARCHÉ ISSU DU SCREENER (TOP 15 LIQUIDES FILTRÉS HORS PORTEFEUILLE) :
{json.dumps(dynamic_candidates, indent=2, ensure_ascii=False)}

{tech_micro_block}

CONSIGNE FORMAT DE RÉPONSE JSON OBLIGATOIRE :
{{
  "report_markdown": "Texte Markdown riche rédigé selon le canevas strict (Synthèse Macro, Audit Positions, Screener Confluence et Tickets d'Ordres copiables)...",
  "recommendations": [
    {{
      "symbol": "SYMBOLE_DETECTE",
      "exchange": "OKX",
      "price": 0.1234,
      "amount_eur": 100.0,
      "sell_symbol": "USDT",
      "tp1_price": 0.2468,
      "tp2_price": 0.4936,
      "sl_price": 0.0864,
      "sl_type": "invalidation"
    }}
  ]
}}"""

    report_text = None
    recs = []
    used_model = "Inconnu"

    try:
        raw_response, used_model = call_gemini_with_fallback(
            prompt_text=user_context,
            system_instruction=system_instruction,
            json_mode=True,
        )
        if raw_response:
            data = parse_llm_json(raw_text=raw_response)
            report_text = data.get("report_markdown", "")
            recs = data.get("recommendations", [])
            logging.info(f"✅ Rapport généré avec succès via [{used_model}] avec {len(recs)} recommandation(s).")
    except Exception as e:
        logging.error(f"❌ Échec critique lors de la génération par Gemini : {e}")

    if report_text:
        inline_keyboard = []
        for r in recs:
            sym_rec = r.get("symbol", "UNKNOWN").upper().strip()
            price = float(r.get("price", 0.0))
            amt_eur = float(r.get("amount_eur", 100.0))
            sell_info = f" (via {r.get('sell_symbol')})" if r.get("sell_symbol") else ""

            rec_id = add_recommendation(
                symbol=sym_rec,
                exchange=r.get("exchange", "OKX"),
                price=price,
                amount_eur=amt_eur,
                tp1_price=float(r.get("tp1_price", price * 2.0)),
                tp2_price=float(r.get("tp2_price", price * 4.0)),
                sl_price=float(r.get("sl_price", price * 0.70)),
                sl_type=r.get("sl_type", "invalidation"),
            )
            inline_keyboard.append([
                {
                    "text": f"🎯 Acheter {sym_rec} ({amt_eur:.0f}€){sell_info}",
                    "callback_data": f"trade_take_{rec_id}",
                },
                {
                    "text": f"❌ Ignorer {sym_rec}",
                    "callback_data": f"trade_ignore_{rec_id}",
                },
            ])

        header = (
            "🎯 *RAPPORT CRYPTO SPÉCULATIF (19H) — v4.0.0*\n"
            f"_Données du {datetime.date.today().strftime('%d/%m/%Y')} | Fear & Greed: {fng['value']}/100_\n"
            f"_🤖 Découverte Dynamique Marché ({len(dynamic_candidates)} actifs) | Modèle : `{used_model}`_\n\n"
        )

        send_telegram_with_inline_buttons(
            header + report_text, inline_keyboard=inline_keyboard
        )
    else:
        logging.error("❌ Échec critique : le rapport n'a pas pu être transmis sur Telegram.")


if __name__ == "__main__":
    generate_crypto_speculative_report()