#!/usr/bin/env python3
"""
Moteur centralisé d'indicateurs macro-économiques et sentiment de marché (SSOT Macro).
Regroupe :
- Fear & Greed Index (Alternative.me)
- Circuit Breaker BTC (OKX / Kraken - Variation 24h & Crash Guard)
- Dominance Bitcoin (BTC.D %) & Ethereum (ETH.D %) (CoinGecko Global)
- Capitalisation TOTAL3 (Altcoins hors BTC & ETH en Mds $)
- Capitalisation Globale des Stablecoins (USDT, USDC, DAI, FDUSD via DefiLlama)
"""

import requests

# Décorateur de cache hybride : compatible Streamlit et scripts CLI/Cron
try:
    import streamlit as st
    cache_300s = st.cache_data(ttl=300, show_spinner=False)
except Exception:
    def cache_300s(func):
        return func


@cache_300s
def fetch_fear_and_greed_index():
    """
    Récupère l'indice Fear & Greed Crypto depuis l'API Alternative.me.
    Returns: dict {"value": int, "classification": str}
    """
    try:
        res = requests.get("https://api.alternative.me/fng/", timeout=3.0)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                val = int(data[0].get("value", 50))
                classification = data[0].get("value_classification", "Neutral")
                return {"value": val, "classification": classification}
    except Exception:
        pass
    return {"value": 50, "classification": "Neutral"}


@cache_300s
def check_circuit_breaker(threshold_pct=-7.0):
    """
    Vérifie si le Bitcoin subit une chute brutale sur 24h (Circuit Breaker).
    Returns: tuple (is_crash: bool, btc_24h_pct: float)
    """
    btc_24h_pct = 0.0
    # Source 1: OKX Ticker
    try:
        res_okx = requests.get(
            "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
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

    # Source 2: Kraken Ticker (Fallback)
    try:
        res_k = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
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

    return False, round(btc_24h_pct, 2)


@cache_300s
def get_macro_market_indicators():
    """
    Récupère les métriques macro globales (BTC.D, TOTAL3, Stablecoins Cap).
    Returns: dict avec dominance, total3 en Mds $ et stablecoins cap en Mds $.
    """
    indicators = {
        "btc_dominance": 58.0,
        "eth_dominance": 14.5,
        "total3_usd_b": 650.0,
        "stablecoins_cap_b": 170.0,
        "status": "fallback",
    }

    # 1. CoinGecko Global Market API
    try:
        res = requests.get("https://api.coingecko.com/api/v3/global", timeout=3.0)
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
            "https://stablecoins.llama.fi/stablecoins", timeout=3.0
        )
        if res_stables.status_code == 200:
            stables_list = res_stables.json().get("peggedAssets", [])
            total_stables_usd = 0.0
            for s in stables_list:
                symbol = str(s.get("symbol", "")).upper()
                if symbol in ["USDT", "USDC", "DAI", "FDUSD"]:
                    circulating = s.get("circulating", {}).get("peggedUSD", 0.0)
                    total_stables_usd += float(circulating)

            if total_stables_usd > 0:
                indicators["stablecoins_cap_b"] = round(total_stables_usd / 1e9, 1)
    except Exception:
        pass

    return indicators


def get_complete_macro_context():
    """
    Agrège l'ensemble de la vision macro & sentiment en un seul dictionnaire.
    """
    fng = fetch_fear_and_greed_index()
    is_crash, btc_24h = check_circuit_breaker()
    metrics = get_macro_market_indicators()

    return {
        "fear_and_greed": fng,
        "circuit_breaker": {"is_crash": is_crash, "btc_24h_pct": btc_24h},
        "metrics": metrics,
    }


def format_macro_context_for_prompt():
    """
    Génère un bloc de texte Markdown prêt à être injecté dans les prompts SSOT Gemini.
    """
    macro = get_complete_macro_context()
    fng = macro["fear_and_greed"]
    cb = macro["circuit_breaker"]
    m = macro["metrics"]

    cb_status = "🚨 MODE DÉFENSIF (CRASH DETECTED)" if cb["is_crash"] else "✅ NORMAL"

    return f"""INDICATEURS MACRO-ÉCONOMIQUES ET SENTIMENT DE MARCHÉ :
- Indice Fear & Greed : {fng['value']}/100 ({fng['classification']})
- Statut Circuit Breaker BTC : {cb_status} (Var 24h : {cb['btc_24h_pct']:+.2f}%)
- Dominance Bitcoin (BTC.D) : {m['btc_dominance']:.1f}% (Seuil de référence : >55% = Concentration BTC, <50% en hausse = Altseason)
- Capitalisation TOTAL3 (Altcoins hors BTC/ETH) : {m['total3_usd_b']:,.1f} Mds $ (Santé et injection de liquidités sur les Altcoins)
- Poudre Sèche Stablecoins Globales (USDT/USDC/DAI) : {m['stablecoins_cap_b']:,.1f} Mds $ (Réserve de liquidité institutionnelle disponible)
"""


if __name__ == "__main__":
    print(format_macro_context_for_prompt())
