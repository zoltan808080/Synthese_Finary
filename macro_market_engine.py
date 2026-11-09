#!/usr/bin/env python3
"""
Moteur centralisé d'indicateurs macro-économiques et Boussole de Flux (SSOT Macro v3.9.1).
Regroupe :
- Fear & Greed Index (Alternative.me) & Calculateur de Cible Cash Dynamique
- Circuit Breaker BTC (OKX / Kraken / CoinGecko - Seuil Doctrinal Crash -5.0%)
- Triplet Macro : Dominance Bitcoin (BTC.D %), TOTAL3 (Mds $) et Stablecoins Globales (Mds $)
- Boussole de Flux Macro (Market Compass) : Lecture croisée (BTC.D x Dynamique BTC) et pondération de Confluence
"""

import time
import functools
import requests


# Décorateur de cache hybride : compatible Streamlit et scripts CLI / Cron (sans avertissement ScriptRunContext)
def safe_cache_300s(func):
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            return st.cache_data(ttl=300, show_spinner=False)(func)
    except Exception:
        pass

    # Fallback cache mémoire TTL natif Python pour exécution CLI / Cron (ex: 19h)
    cache = {}

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        now = time.time()
        key = (args, tuple(sorted(kwargs.items())))
        if key in cache:
            result, timestamp = cache[key]
            if now - timestamp < 300:
                return result
        result = func(*args, **kwargs)
        cache[key] = (result, now)
        return result

    return wrapper


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
        " Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


@safe_cache_300s
def fetch_fear_and_greed_index():
    """
    Récupère l'indice Fear & Greed Crypto depuis l'API Alternative.me.
    Returns: dict {"value": int, "classification": str}
    """
    try:
        res = requests.get("https://api.alternative.me/fng/", headers=DEFAULT_HEADERS, timeout=3.0)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                val = int(data[0].get("value", 50))
                classification = data[0].get("value_classification", "Neutral")
                return {"value": val, "classification": classification}
    except Exception:
        pass
    return {"value": 50, "classification": "Neutral"}


@safe_cache_300s
def check_circuit_breaker(threshold_pct=-5.0):
    """
    Vérifie si le Bitcoin subit une chute brutale sur 24h (Circuit Breaker SSOT : seuil -5.0%).
    Returns: tuple (is_crash: bool, btc_24h_pct: float)
    """
    btc_24h_pct = 0.0

    # Source 1: OKX Ticker
    try:
        res_okx = requests.get(
            "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
            headers=DEFAULT_HEADERS,
            timeout=2.0
        )
        if res_okx.status_code == 200:
            data = res_okx.json().get("data", [])
            if data:
                open24 = float(data[0].get("open24h", 0))
                last = float(data[0].get("last", 0))
                if open24 > 0:
                    btc_24h_pct = ((last - open24) / open24) * 100.0
                    return (btc_24h_pct <= threshold_pct), round(btc_24h_pct, 2)
    except Exception:
        pass

    # Source 2: Kraken Ticker
    try:
        res_k = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
            headers=DEFAULT_HEADERS,
            timeout=2.0
        )
        if res_k.status_code == 200:
            result = res_k.json().get("result", {})
            for _, v_data in result.items():
                if "o" in v_data and "c" in v_data:
                    open_price = float(v_data["o"])
                    close_price = float(v_data["c"][0])
                    if open_price > 0:
                        btc_24h_pct = ((close_price - open_price) / open_price) * 100.0
                        return (btc_24h_pct <= threshold_pct), round(btc_24h_pct, 2)
    except Exception:
        pass

    # Source 3: CoinGecko Ticker (Fallback supplémentaire)
    try:
        res_cg = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true",
            headers=DEFAULT_HEADERS,
            timeout=3.0
        )
        if res_cg.status_code == 200:
            val = res_cg.json().get("bitcoin", {}).get("usd_24h_change")
            if val is not None and val != 0.0:
                btc_24h_pct = float(val)
                return (btc_24h_pct <= threshold_pct), round(btc_24h_pct, 2)
    except Exception:
        pass

    return False, round(btc_24h_pct, 2)


@safe_cache_300s
def get_macro_market_indicators():
    """
    Récupère les métriques macro globales (BTC.D, TOTAL3, Stablecoins Cap).
    Returns: dict avec dominance, total3 en Mds $ et stablecoins cap en Mds $.
    """
    indicators = {
        "btc_dominance": 60.0,
        "eth_dominance": 10.5,
        "total3_usd_b": 700.0,
        "stablecoins_cap_b": 250.0,
        "status": "fallback",
    }

    # 1. CoinGecko Global Market API
    try:
        res = requests.get("https://api.coingecko.com/api/v3/global", headers=DEFAULT_HEADERS, timeout=3.0)
        if res.status_code == 200:
            data = res.json().get("data", {})
            mcap_pct = data.get("market_cap_percentage", {})
            tot_mcap = data.get("total_market_cap", {})

            btc_d = float(mcap_pct.get("btc", 0.0))
            eth_d = float(mcap_pct.get("eth", 0.0))
            total_mcap_usd = float(tot_mcap.get("usd", 0.0))

            if btc_d > 0 and total_mcap_usd > 0:
                indicators["btc_dominance"] = round(btc_d, 2)
                indicators["eth_dominance"] = round(eth_d, 2)

                # TOTAL3 = Total Market Cap * (100 - BTC.D% - ETH.D%) / 100
                alts_pct = max(0.0, 100.0 - btc_d - eth_d)
                total3_val = total_mcap_usd * (alts_pct / 100.0)
                indicators["total3_usd_b"] = round(total3_val / 1e9, 1)
                indicators["status"] = "live"
    except Exception:
        pass

    # 2. DefiLlama Stablecoins API
    try:
        res_stables = requests.get(
            "https://stablecoins.llama.fi/stablecoins", headers=DEFAULT_HEADERS, timeout=3.0
        )
        if res_stables.status_code == 200:
            stables_list = res_stables.json().get("peggedAssets", [])
            total_stables_usd = 0.0
            for s in stables_list:
                symbol = str(s.get("symbol", "")).upper()
                if symbol in ["USDT", "USDC", "DAI", "FDUSD", "USDE"]:
                    circulating = s.get("circulating", {})
                    circ_val = circulating.get("peggedUSD", 0.0) if isinstance(circulating, dict) else float(circulating or 0.0)
                    total_stables_usd += float(circ_val)

            if total_stables_usd > 0:
                indicators["stablecoins_cap_b"] = round(total_stables_usd / 1e9, 1)
    except Exception:
        pass

    return indicators


def evaluate_macro_regime(
    btc_dominance: float,
    fng_value: int,
    btc_24h_pct: float = 0.0,
    is_crash: bool = False,
) -> dict:
    """
    Évalue la Boussole de Flux Macro (Market Compass v3.9.1), la pondération du Pilier Macro (30%)
    et la cible de cash dynamique selon le Fear & Greed.
    """
    # 1. Lecture croisée (BTC.D x Dynamique Prix BTC)
    if is_crash or (btc_24h_pct <= -3.0 and btc_dominance >= 60.0):
        regime = "RISK_OFF_BLEED"
        gatekeeper_locked = True
        macro_pillar_score = "1.0/5 ⭐ (Verrou Défensif)"
        gatekeeper_desc = (
            f"🚨 RÉGIME RISK_OFF_BLEED (BTC 24h: {btc_24h_pct:+.2f}%, BTC.D: {btc_dominance:.1f}%) : "
            "Saignée des Altcoins / Repli brusque. Gel défensif des nouveaux achats spéculatifs et préservation du Cash/BTC."
        )
    elif btc_dominance >= 60.0:
        regime = "BTC_LED_MARKET"
        gatekeeper_locked = False
        macro_pillar_score = "3.0/5 ⭐ (Sélectivité Alpha)"
        gatekeeper_desc = (
            f"🧭 RÉGIME BTC_LED_MARKET (BTC.D: {btc_dominance:.1f}% >= 60.0%, BTC 24h: {btc_24h_pct:+.2f}%) : "
            "Le Bitcoin mène le flux mais AUCUN blocage binaire n'est appliqué. Trading Moonbag et arbitrages CORE "
            "AUTORISÉS sur les actifs démontrant un Alpha vs BTC positif et un timing RSI sain (Confluence globale >= 4/5 ⭐)."
        )
    elif btc_dominance >= 57.5:
        regime = "BALANCED_ROTATION"
        gatekeeper_locked = False
        macro_pillar_score = "4.0/5 ⭐ (Conditions Nominales)"
        gatekeeper_desc = (
            f"⚖️ RÉGIME BALANCED_ROTATION (BTC.D: {btc_dominance:.1f}% entre 57.5% et 60.0%) : "
            "Canal d'équilibre. Conditions nominales pour les rééquilibrages intra-CORE et les setups Moonbag (>= 4/5 ⭐)."
        )
    else:
        regime = "ALT_EXPANSION"
        gatekeeper_locked = False
        macro_pillar_score = "5.0/5 ⭐ (Expansion Altseason)"
        gatekeeper_desc = (
            f"🟢 RÉGIME ALT_EXPANSION (BTC.D: {btc_dominance:.1f}% < 57.5%) : "
            "Diffusion large des liquidités vers TOTAL3. Pleine offensive autorisée vers les poches CORE et Spéculative."
        )

    # 2. Cible Cash Dynamique selon sentiment (F&G)
    if fng_value < 25:
        target_cash_pct = 9.0  # 8-10% (Peur Extrême : déploiement opportuniste)
        cash_posture = "DÉPLOIEMENT OPPORTUNISTE DU CASH (Peur Extrême < 25 : Cible 8-10%)"
    elif fng_value > 75:
        target_cash_pct = 19.0  # 18-20% (Cupidité Extrême : prises de profits défensives)
        cash_posture = "SÉCURISATION DÉFENSIVE EN CASH (Cupidité Extrême > 75 : Cible 18-20%)"
    else:
        target_cash_pct = 13.0  # Nominal
        cash_posture = "CIBLE NOMINALE CONSERVÉE (13.0%)"

    return {
        "regime": regime,
        "gatekeeper_locked": gatekeeper_locked,
        "macro_pillar_score": macro_pillar_score,
        "gatekeeper_desc": gatekeeper_desc,
        "target_cash_pct": target_cash_pct,
        "cash_posture": cash_posture,
    }


def get_complete_macro_context():
    """
    Agrège l'ensemble de la vision macro, sentiment, Boussole de Flux et cibles dynamiques.
    """
    fng = fetch_fear_and_greed_index()
    is_crash, btc_24h = check_circuit_breaker()
    metrics = get_macro_market_indicators()
    regime_info = evaluate_macro_regime(
        btc_dominance=metrics["btc_dominance"],
        fng_value=fng["value"],
        btc_24h_pct=btc_24h,
        is_crash=is_crash,
    )

    return {
        "fear_and_greed": fng,
        "circuit_breaker": {"is_crash": is_crash, "btc_24h_pct": btc_24h},
        "metrics": metrics,
        "regime": regime_info,
    }


def format_macro_context_for_prompt():
    """
    Génère un bloc Markdown enrichi prêt pour injection directe dans les prompts SSOT Gemini (v3.9.1).
    """
    macro = get_complete_macro_context()
    fng = macro["fear_and_greed"]
    cb = macro["circuit_breaker"]
    m = macro["metrics"]
    r = macro["regime"]

    cb_status = "🚨 MODE DÉFENSIF (CRASH DÉTECTÉ)" if cb["is_crash"] else "✅ NORMAL"

    return f"""INDICATEURS MACRO-ÉCONOMIQUES ET BOUSSOLE DE FLUX (SSOT v3.9.1) :
- Indice Fear & Greed : {fng['value']}/100 ({fng['classification']}) ➔ {r['cash_posture']}
- Statut Circuit Breaker BTC : {cb_status} (Var 24h : {cb['btc_24h_pct']:+.2f}%, Seuil : -5.0%)
- Dominance Bitcoin (BTC.D) : {m['btc_dominance']:.1f}% | Dominance ETH : {m['eth_dominance']:.1f}%
- Boussole de Flux Macro (Market Compass) : {r['gatekeeper_desc']}
- Note du Pilier Macro (30% de la Confluence) : {r['macro_pillar_score']}
- Capitalisation TOTAL3 (Altcoins hors BTC/ETH) : {m['total3_usd_b']:,.1f} Mds $ (Baromètre de liquidité altcoins)
- Poudre Sèche Stablecoins Globales (USDT/USDC/DAI) : {m['stablecoins_cap_b']:,.1f} Mds $ (Liquidités institutionnelles d'attente)
- Cible Cash Dynamique Recommandée : {r['target_cash_pct']:.1f}% du portefeuille global
"""


if __name__ == "__main__":
    print(format_macro_context_for_prompt())