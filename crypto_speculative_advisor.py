#!/usr/bin/env python3
"""Moteur d'analyse et de recommandation spéculative Crypto v3.3.0 (Architecture SSOT & Contexte Macro Unifié)."""

import datetime
import json
import logging
import os
import re
from dotenv import load_dotenv
import requests

from db_manager import add_recommendation, get_open_positions, init_db
from exchange_connectors import (
    get_all_exchange_crypto_balances,
    get_all_open_orders,
    get_all_trades_history,
    get_total_verified_cash,
)
from gemini_manager import call_gemini_with_fallback
from pnl_engine import calculate_pnl
from prompt_loader import get_system_prompt

# Importation du Contexte Macro SSOT
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
                return (change_24h <= -8.0), change_24h
    except Exception as e:
        logging.warning(f"⚠️ Erreur BTC 24h CoinGecko fallback : {e}")

    try:
        res = requests.get(
            "https://api.okx.com/api/v5/market/ticker?instId=BTC-USDC", timeout=5
        )
        if res.status_code == 200:
            data = res.json().get("data", [])[0]
            last = float(data.get("last", 0))
            open24h = float(data.get("open24h", 0))
            if open24h > 0:
                change_24h = ((last - open24h) / open24h) * 100.0
                return (change_24h <= -8.0), change_24h
    except Exception as e:
        logging.warning(f"⚠️ Erreur BTC 24h OKX fallback : {e}")

    return (change_24h <= -8.0), change_24h


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


def get_kraken_spot_symbols():
    """Récupère la liste des symboles éligibles au trading Spot sur Kraken."""
    try:
        res = requests.get("https://api.kraken.com/0/public/AssetPairs", timeout=5)
        if res.status_code == 200:
            pairs = res.json().get("result", {})
            symbols = set()
            for p in pairs.values():
                wsname = p.get("wsname", "")
                if "/" in wsname:
                    base, quote = wsname.split("/")
                    if quote in ["EUR", "USDT", "USD"]:
                        symbols.add(base.upper())
            return symbols
    except Exception:
        pass
    return set()


def get_okx_spot_symbols():
    """Récupère la liste des symboles éligibles au trading Spot sur OKX."""
    try:
        res = requests.get(
            "https://www.okx.com/api/v5/public/instruments?instType=SPOT",
            timeout=5,
        )
        if res.status_code == 200:
            data = res.json().get("data", [])
            symbols = set()
            for inst in data:
                inst_id = inst.get("instId", "")
                if "-" in inst_id:
                    base, quote = inst_id.split("-")
                    if quote in ["EUR", "USDT", "USD"]:
                        symbols.add(base.upper())
            return symbols
    except Exception:
        pass
    return set()


def fetch_coingecko_trending_and_markets():
    """Récupère les jetons en tendance CoinGecko filtrés par volume et marché."""
    headers = {"accept": "application/json"}
    opportunities = []
    try:
        res_tr = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            headers=headers,
            timeout=5,
        )
        trending_coins = (
            res_tr.json().get("coins", []) if res_tr.status_code == 200 else []
        )
        coin_ids = [c["item"]["id"] for c in trending_coins[:10]]

        if coin_ids:
            url_mk = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=eur&ids={','.join(coin_ids)}&price_change_percentage=24h"
            res_mk = requests.get(url_mk, headers=headers, timeout=5)
            if res_mk.status_code == 200:
                for coin in res_mk.json():
                    opportunities.append({
                        "name": coin.get("name"),
                        "symbol": coin.get("symbol", "").upper(),
                        "price_eur": coin.get("current_price"),
                        "change_24h_pct": coin.get("price_change_percentage_24h"),
                        "market_cap_eur": coin.get("market_cap"),
                        "coingecko_id": coin.get("id"),
                    })
    except Exception as e:
        logging.warning(f"⚠️ Erreur CoinGecko : {e}")
    return opportunities


def send_telegram_with_inline_buttons(text, inline_keyboard=None):
    """Envoie un message formaté sur Telegram avec boutons d'action 1-click."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    html_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_text = re.sub(r"\*(.*?)\*", r"<b>\1</b>", html_text)
    html_text = re.sub(r"_(.*?)_", r"<i>\1</i>", html_text)
    html_text = re.sub(r"`(.*?)`", r"<code>\1</code>", html_text)

    payload = {"chat_id": ALLOWED_USER_ID, "text": html_text, "parse_mode": "HTML"}
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"❌ Erreur réseau Telegram : {e}")


def parse_llm_json(raw_text):
    """Extrait et décode proprement une structure JSON renvoyée par le LLM."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text, strict=False)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0), strict=False)
        raise


def calculate_unified_crypto_categories(eur_usd_rate=1.12):
    """Calcul 100% autonome et SSOT unifié avec le Dashboard v3.3.0 (Exchanges + Exodus + BDD SQLite)."""
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

    # 1. Valorisation des positions spéculatives actives depuis la BDD SQLite
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

    # 2. Valorisation des avoirs sur Exchanges & Wallets froids (avec filtre anti-doublon)
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
                # Anti-doublon strict : déjà comptabilisé via la BDD SQLite
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
            holdings_lines.append(f"• {asset} : {val_eur:.2f} € MAX ARBITRABLE")

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


# Aliases pour garantie de non-régression avec les versions antérieures
calculate_unfied_crypto_categories = calculate_unified_crypto_categories
calculate_exact_crypto_categories = calculate_unified_crypto_categories


def generate_crypto_speculative_report():
    """Génère le rapport quotidien spéculatif de 19h via Gemini (SSOT v3.3.0) et l'envoie sur Telegram avec boutons 1-click."""
    logging.info("🚀 Démarrage de l'analyse spéculative Crypto v3.3.0 (Prompt SSOT + Macro SSOT + Gemini)...")

    is_crash, crash_pct = check_circuit_breaker()
    fng = fetch_fear_and_greed_index()
    trades = get_all_trades_history()
    pnl_summary = calculate_pnl(trades)

    kraken_symbols = get_kraken_spot_symbols()
    okx_symbols = get_okx_spot_symbols()

    cats = calculate_unified_crypto_categories()
    total_crypto = cats["total_crypto"]
    target_budget = total_crypto * SPECULATIVE_PCT

    raw_opportunities = (
        [] if is_crash else fetch_coingecko_trending_and_markets()
    )
    verified_opportunities = []
    for item in raw_opportunities:
        sym = item["symbol"].upper()
        available = []
        if sym in kraken_symbols:
            available.append("KRAKEN")
        if sym in okx_symbols:
            available.append("OKX")
        if available:
            item["exchanges_verifies"] = available
            verified_opportunities.append(item)

    # Contexte Macro SSOT unifié avec le Dashboard Web
    macro_prompt_block = (
        macro_format_prompt()
        if MACRO_ENGINE_AVAILABLE
        else f"DONNÉES DU MARCHÉ ET CONTEXTE MACRO :\n- Statut Circuit Breaker : {'🚨 DEFENSIVE MODE' if is_crash else '✅ NORMAL'} (Var BTC 24h : {crash_pct:+.2f}%)\n- Indice Fear & Greed : {fng['value']}/100 ({fng['classification']})"
    )

    # Chargement dynamique des règles immuables et instructions de rôle (SSOT)
    system_instruction = get_system_prompt("advisor_speculative")

    user_context = f"""DONNÉES CHIFFRÉES TEMPS RÉEL (EXCHANGES + EXODUS) :
- Valeur totale du portefeuille : {total_crypto:,.2f} €
- Enveloppe spéculative cible ({SPECULATIVE_PCT*100:.0f}%) : {target_budget:,.2f} €

REPARTITION EXACTE DES ACTIFS (MÊMES DÉFINITIONS QUE LE DASHBOARD) :
• Majeures (BTC & ETH - Spot + Staking + Exodus) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) [Cible : 55%]
• Core / Convictions long terme : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) [Cible : 22%]
• Spéculatives Actives (Moonbag BDD) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) [Cible : 10% / Max : 12%]
• Liquidités Totales (Fiat + Stablecoins) : {cats['stables_cash']:,.2f} € ({cats['stables_cash_pct']:.1f}%) [Cible : 13%]

DÉTAIL DES LIQUIDITÉS MOBILISABLES :
• Stablecoins (USDT + USDC + DAI + FDUSD) : {cats['val_stables_crypto']:,.2f} €
• Cash Fiat (EUR + USD) : {cats['total_cash_fiat']:,.2f} €
• Total Liquidités Immédiates : {cats['stables_cash']:,.2f} €

PLAFONDS STRICTS D'ARBITRAGE PAR ACTIF DÉTENU (SEUIL MAXIMAL VENDABLE) :
{cats['holdings_text']}

POSITIONS ACTIVES SUIVIES EN BDD SQLITE :
{json.dumps(pnl_summary['open_positions'], indent=2, ensure_ascii=False)}

{macro_prompt_block}

OPPORTUNITÉS PRÉ-FILTRÉES (Kraken / OKX) :
{json.dumps(verified_opportunities, indent=2, ensure_ascii=False)}

GÉNÈRE LA RÉPONSE JSON STRICTE SUIVANTE :
{{
  "report_markdown": "Texte Markdown riche, structuré et professionnel...",
  "recommendations": [
    {{
      "symbol": "STRK",
      "exchange": "OKX",
      "price": 0.4500,
      "amount_eur": 100.0,
      "sell_symbol": "USDT",
      "tp1_price": 0.9000,
      "tp2_price": 1.8000,
      "sl_price": 0.2500,
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
            logging.info(f"✅ Rapport généré avec succès via le modèle Gemini centralisé [{used_model}]")
    except Exception as e:
        logging.error(f"❌ Échec critique lors de la génération par Gemini : {e}")

    if report_text:
        inline_keyboard = []
        for r in recs:
            price = r.get("price", 0.0)
            sell_info = (
                f" (via {r.get('sell_symbol')})" if r.get("sell_symbol") else ""
            )
            rec_id = add_recommendation(
                symbol=r.get("symbol", "UNKNOWN"),
                exchange=r.get("exchange", "OKX"),
                price=price,
                amount_eur=r.get("amount_eur", 0.0),
                tp1_price=r.get("tp1_price", price * 2.0),
                tp2_price=r.get("tp2_price", price * 4.0),
                sl_price=r.get("sl_price", price * 0.5),
                sl_type=r.get("sl_type", "invalidation"),
            )
            inline_keyboard.append([
                {
                    "text": f"🔄 Arbitrer : {r.get('symbol')} ({r.get('amount_eur', 0):.0f}€){sell_info}",
                    "callback_data": f"trade_take_{rec_id}",
                },
                {
                    "text": f"❌ Ignorer {r.get('symbol')}",
                    "callback_data": f"trade_ignore_{rec_id}",
                },
            ])

        header = (
            "🎯 *RAPPORT CRYPTO SPÉCULATIF (19H) — v3.3.0*\n"
            f"_Données du {datetime.date.today().strftime('%d/%m/%Y')} | Fear & Greed: {fng['value']}/100_\n"
            f"_🤖 IA : `{used_model}`_\n\n"
        )

        send_telegram_with_inline_buttons(
            header + report_text, inline_keyboard=inline_keyboard
        )
    else:
        logging.error("❌ Échec critique : la génération de rapport a échoué.")


if __name__ == "__main__":
    generate_crypto_speculative_report()