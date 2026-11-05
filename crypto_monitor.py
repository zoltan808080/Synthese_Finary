#!/usr/bin/env python3
"""Sentinelle 24/7 et Moteur d'Alertes Telegram Intelligentes v3.5.0 (Conseil Flash IA Gemini SSOT Flash-Lite, Alpha vs BTC & High-Water Mark)."""

import datetime
import logging
import os
import requests
from dotenv import load_dotenv

from db_manager import (
    get_open_positions,
    init_db,
    update_alert_status,
    update_highest_price_seen,
    update_trade_step,
)
from gemini_manager import call_gemini_with_fallback
from prompt_loader import get_system_prompt

load_dotenv()
init_db()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# Table de correspondance Symbol -> ID CoinGecko
COINGECKO_MAP = {
    "kas": "kaspa",
    "hype": "hyperliquid",
    "ath": "aethir",
    "fet": "artificial-superintelligence-alliance",
    "ondo": "ondo-finance",
    "ray": "raydium",
}


def get_usd_to_eur_rate():
    """Récupère le taux USD/USDC vers EUR en temps réel."""
    try:
        res = requests.get(
            "https://www.okx.com/api/v5/market/ticker?instId=USDC-EUR", timeout=3
        )
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                return float(data[0]["last"])
    except Exception:
        pass

    try:
        res = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=EURUSDT", timeout=3
        )
        if res.status_code == 200:
            result = res.json().get("result", {})
            if result:
                key = list(result.keys())[0]
                price = float(result[key]["c"][0])
                if price > 0:
                    return 1.0 / price
    except Exception:
        pass

    return 0.883


def get_live_price(symbol, exchange):
    """Récupère le prix actuel du token converti en Euros."""
    symbol_upper = symbol.upper()
    symbol_lower = symbol.lower()
    exchange_upper = exchange.upper() if exchange else ""
    usd_eur_rate = get_usd_to_eur_rate()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
            " Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    # 1. Kraken
    if "KRAKEN" in exchange_upper:
        for pair in [f"{symbol_upper}EUR", f"X{symbol_upper}ZEUR"]:
            try:
                res = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                    headers=headers,
                    timeout=3,
                )
                if res.status_code == 200:
                    result = res.json().get("result", {})
                    if result:
                        key = list(result.keys())[0]
                        return float(result[key]["c"][0])
            except Exception:
                pass

        for pair in [f"{symbol_upper}USDT", f"{symbol_upper}USD"]:
            try:
                res = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                    headers=headers,
                    timeout=3,
                )
                if res.status_code == 200:
                    result = res.json().get("result", {})
                    if result:
                        key = list(result.keys())[0]
                        return float(result[key]["c"][0]) * usd_eur_rate
            except Exception:
                pass

    # 2. OKX
    if "OKX" in exchange_upper:
        try:
            res = requests.get(
                f"https://www.okx.com/api/v5/market/ticker?instId={symbol_upper}-EUR",
                headers=headers,
                timeout=3,
            )
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    return float(data[0]["last"])
        except Exception:
            pass

        for inst_id in [f"{symbol_upper}-USDC", f"{symbol_upper}-USDT"]:
            try:
                res = requests.get(
                    f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
                    headers=headers,
                    timeout=3,
                )
                if res.status_code == 200:
                    data = res.json().get("data", [])
                    if data:
                        return float(data[0]["last"]) * usd_eur_rate
            except Exception:
                pass

    # 3. Fallback CoinGecko (avec mapping ID)
    cg_ids = [COINGECKO_MAP.get(symbol_lower, symbol_lower), symbol_lower]
    for cg_id in dict.fromkeys(cg_ids):
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=eur"
            res = requests.get(url, headers=headers, timeout=4)
            if res.status_code == 200:
                data = res.json()
                if cg_id in data and "eur" in data[cg_id]:
                    return float(data[cg_id]["eur"])
        except Exception:
            pass

    return None


def get_flash_ai_advice(
    symbol,
    exchange,
    price_entry,
    current_price,
    pnl_pct,
    alpha_vs_btc_pct,
    event_type,
    sl_price=0.0,
):
    """Sollicite Gemini en mode Flash ultra-rapide et économe (Flash-Lite SSOT)."""
    system_prompt = get_system_prompt("flash_advice")

    user_payload = f"""SITUATION DE L'ALERTE :
- Actif : {symbol