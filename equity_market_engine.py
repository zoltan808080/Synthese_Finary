#!/usr/bin/env python3
"""
Moteur Macro & Technique Bourse (equity_market_engine.py).
Miroir de macro_market_engine.py et rsi_engine.py :
- Boussole Macro Bourse : VIX (^VIX) x Tendance MSCI World (CW8.PA vs SMA 50/200) x Taux 10Y US (^TNX)
- Screener Multi-Threadé Yahoo Finance : Prix live, variation 24h, RSI (1h, 4h, 1D), distance SMA 200
- Calcul de l'Alpha vs MSCI World (CW8.PA)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from curl_cffi import requests as curl_requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
        " Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def compute_rsi_series(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    val = float(rsi.iloc[-1])
    return round(val, 1) if not pd.isna(val) else 50.0


def fetch_yahoo_ticker_metrics(ticker):
    """Récupère le cours live, variation 24h, RSI (1h, 4h, 1D) et SMA 200 pour un ticker Yahoo Finance."""
    result = {
        "ticker": ticker,
        "price": 0.0,
        "change_24h_pct": 0.0,
        "rsi_1h": 50.0,
        "rsi_4h": 50.0,
        "rsi_1d": 50.0,
        "sma_50": 0.0,
        "sma_200": 0.0,
        "dist_sma200_pct": 0.0,
    }
    if not ticker:
        return result

    session = curl_requests.Session(impersonate="chrome110")

    # 1. Données journalières (1 an -> 24h, RSI 1D, SMA 50 & SMA 200)
    try:
        url_1d = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
        r = session.get(url_1d, headers=DEFAULT_HEADERS, timeout=6.0)
        if r.status_code == 200:
            res_list = r.json().get("chart", {}).get("result", [])
            if res_list:
                meta = res_list[0].get("meta", {})
                closes = res_list[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                valid_c = [float(c) for c in closes if c is not None and c > 0]
                if valid_c:
                    last_p = float(meta.get("regularMarketPrice") or valid_c[-1])
                    prev_p = valid_c[-2] if len(valid_c) >= 2 else last_p
                    result["price"] = round(last_p, 4)
                    result["change_24h_pct"] = round(((last_p - prev_p) / prev_p * 100.0) if prev_p > 0 else 0.0, 2)
                    result["rsi_1d"] = compute_rsi_series(valid_c, 14)
                    if len(valid_c) >= 50:
                        result["sma_50"] = round(sum(valid_c[-50:]) / 50.0, 4)
                    if len(valid_c) >= 200:
                        sma200 = sum(valid_c[-200:]) / 200.0
                        result["sma_200"] = round(sma200, 4)
                        result["dist_sma200_pct"] = round(((last_p - sma200) / sma200 * 100.0), 2)
    except Exception as e:
        logging.debug(f"Yahoo 1D error {ticker}: {e}")

    # 2. Données intraday (1 mois en 1h -> RSI 1h et RSI 4h resamplé)
    try:
        url_1h = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1h"
        r_h = session.get(url_1h, headers=DEFAULT_HEADERS, timeout=6.0)
        if r_h.status_code == 200:
            res_h = r_h.json().get("chart", {}).get("result", [])
            if res_h:
                closes_h = res_h[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                valid_h = [float(c) for c in closes_h if c is not None and c > 0]
                if len(valid_h) >= 15:
                    result["rsi_1h"] = compute_rsi_series(valid_h, 14)
                    closes_4h = valid_h[::4]
                    if len(closes_4h) >= 15:
                        result["rsi_4h"] = compute_rsi_series(closes_4h, 14)
    except Exception:
        pass

    return result


def fetch_bulk_equity_metrics(tickers_list):
    """Exécution parallèle multi-threadée pour l'ensemble des tickers Bourse/ETF."""
    unique_tickers = sorted({t for t in tickers_list if t})
    metrics_map = {}
    if not unique_tickers:
        return metrics_map

    with ThreadPoolExecutor(max_workers=min(10, len(unique_tickers))) as executor:
        futures = {executor.submit(fetch_yahoo_ticker_metrics, t): t for t in unique_tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                metrics_map[t] = fut.result()
            except Exception:
                metrics_map[t] = {"ticker": t, "price": 0.0, "change_24h_pct": 0.0, "rsi_1h": 50.0, "rsi_4h": 50.0, "rsi_1d": 50.0}
    return metrics_map


def get_equity_macro_compass():
    """Calcule la Boussole Macro Bourse (VIX, Taux 10Y US, MSCI World CW8.PA, S&P 500)."""
    macro_tickers = ["^VIX", "^TNX", "CW8.PA", "^GSPC"]
    data = fetch_bulk_equity_metrics(macro_tickers)

    vix = data.get("^VIX", {}).get("price", 16.0) or 16.0
    tnx = data.get("^TNX", {}).get("price", 4.2) or 4.2
    cw8 = data.get("CW8.PA", {})
    cw8_price = cw8.get("price", 0.0)
    cw8_24h = cw8.get("change_24h_pct", 0.0)
    cw8_dist_sma200 = cw8.get("dist_sma200_pct", 0.0)
    sp500_24h = data.get("^GSPC", {}).get("change_24h_pct", 0.0)

    if vix >= 28.0 or cw8_dist_sma200 < -5.0:
        regime = "RISK_OFF_VOLATILITY"
        macro_stars = 1
    elif vix >= 21.0 or cw8_dist_sma200 < 0.0:
        regime = "CAUTIOUS_CONSOLIDATION"
        macro_stars = 3
    elif vix <= 15.5 and cw8_dist_sma200 > 2.0:
        regime = "RISK_ON_EXPANSION"
        macro_stars = 5
    else:
        regime = "BALANCED_EQUITY_TREND"
        macro_stars = 4

    return {
        "regime": regime,
        "macro_stars": macro_stars,
        "vix": round(vix, 2),
        "us_10y_yield": round(tnx, 2),
        "cw8_price": cw8_price,
        "cw8_24h_pct": cw8_24h,
        "cw8_dist_sma200_pct": cw8_dist_sma200,
        "sp500_24h_pct": sp500_24h,
    }


if __name__ == "__main__":
    compass = get_equity_macro_compass()
    print("✅ Boussole Macro Bourse :", compass)
