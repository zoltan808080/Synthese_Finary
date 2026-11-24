#!/usr/bin/env python3
"""
Moteur Macro & Technique Bourse (equity_market_engine.py — v2.1.0).
Optimisations v2.1.0 :
- Gestion déterministe de la mémoire curl_cffi (context manager) & pool limité à 5 workers (anti-429 / e2-micro)
- Option include_intraday=False pour diviser par 2 les appels HTTP de la Boussole Macro
- Alignement exact du RSI 4h sur le cours live (valid_h[::-4][::-1])
- Calcul natif de l'ATR 14j (%) et distance SMA 50 sans requête supplémentaire
- Intégration de la tension des taux longs (^TNX >= 4.75%) dans la Boussole Macro
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


def compute_atr_pct(highs, lows, closes, period=14):
    """Calcule l'Average True Range (14j) exprimé en % du cours actuel."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return 0.0
    trs = []
    for i in range(1, n):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        if h is None or l is None or prev_c is None:
            continue
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    atr = float(pd.Series(trs, dtype=float).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().iloc[-1])
    last_c = closes[-1]
    return round((atr / last_c) * 100.0, 2) if last_c and last_c > 0 else 0.0


def fetch_yahoo_ticker_metrics(ticker, include_intraday=True):
    """Récupère cours live, variation 24h, RSI (1h, 4h, 1D), SMA 50/200 et ATR 14j (%) pour un ticker Yahoo."""
    result = {
        "ticker": ticker,
        "currency": "EUR",
        "price": 0.0,
        "change_24h_pct": 0.0,
        "rsi_1h": 50.0,
        "rsi_4h": 50.0,
        "rsi_1d": 50.0,
        "sma_50": 0.0,
        "dist_sma50_pct": 0.0,
        "sma_200": 0.0,
        "dist_sma200_pct": 0.0,
        "atr_14_pct": 0.0,
    }
    if not ticker:
        return result

    with curl_requests.Session(impersonate="chrome110") as session:
        # 1. Données journalières (1 an -> 24h, RSI 1D, SMA 50/200, ATR 14j)
        try:
            url_1d = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
            r = session.get(url_1d, headers=DEFAULT_HEADERS, timeout=6.0)
            if r.status_code == 200:
                res_list = r.json().get("chart", {}).get("result", [])
                if res_list:
                    meta = res_list[0].get("meta", {})
                    result["currency"] = str(meta.get("currency") or "EUR")
                    quote = res_list[0].get("indicators", {}).get("quote", [{}])[0]
                    closes = quote.get("close", [])
                    highs = quote.get("high", [])
                    lows = quote.get("low", [])

                    valid_ohlc = [
                        (float(h), float(l), float(c))
                        for h, l, c in zip(highs, lows, closes)
                        if c is not None and c > 0
                    ]
                    valid_c = [x[2] for x in valid_ohlc]

                    if valid_c:
                        last_p = float(meta.get("regularMarketPrice") or valid_c[-1])
                        prev_p = valid_c[-2] if len(valid_c) >= 2 else last_p
                        result["price"] = round(last_p, 4)
                        result["change_24h_pct"] = round(
                            ((last_p - prev_p) / prev_p * 100.0) if prev_p > 0 else 0.0, 2
                        )
                        result["rsi_1d"] = compute_rsi_series(valid_c, 14)
                        result["atr_14_pct"] = compute_atr_pct(
                            [x[0] for x in valid_ohlc],
                            [x[1] for x in valid_ohlc],
                            valid_c,
                            14,
                        )
                        if len(valid_c) >= 50:
                            sma50 = sum(valid_c[-50:]) / 50.0
                            result["sma_50"] = round(sma50, 4)
                            result["dist_sma50_pct"] = round(((last_p - sma50) / sma50 * 100.0), 2)
                        if len(valid_c) >= 200:
                            sma200 = sum(valid_c[-200:]) / 200.0
                            result["sma_200"] = round(sma200, 4)
                            result["dist_sma200_pct"] = round(((last_p - sma200) / sma200 * 100.0), 2)
        except Exception as e:
            logging.debug(f"Yahoo 1D error {ticker}: {e}")

        # 2. Données intraday (1 mois en 1h -> RSI 1h et RSI 4h aligné sur le cours live)
        if include_intraday:
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
                            closes_4h = valid_h[::-4][::-1]
                            if len(closes_4h) >= 15:
                                result["rsi_4h"] = compute_rsi_series(closes_4h, 14)
            except Exception:
                pass

    return result


def fetch_bulk_equity_metrics(tickers_list, include_intraday=True):
    """Exécution parallèle multi-threadée maîtrisée (5 workers max) pour l'ensemble des tickers Bourse/ETF."""
    unique_tickers = sorted({t for t in tickers_list if t})
    metrics_map = {}
    if not unique_tickers:
        return metrics_map

    with ThreadPoolExecutor(max_workers=min(5, len(unique_tickers))) as executor:
        futures = {
            executor.submit(fetch_yahoo_ticker_metrics, t, include_intraday): t
            for t in unique_tickers
        }
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                metrics_map[t] = fut.result()
            except Exception:
                metrics_map[t] = {
                    "ticker": t,
                    "currency": "EUR",
                    "price": 0.0,
                    "change_24h_pct": 0.0,
                    "rsi_1h": 50.0,
                    "rsi_4h": 50.0,
                    "rsi_1d": 50.0,
                    "atr_14_pct": 0.0,
                }
    return metrics_map


def get_equity_macro_compass():
    """Calcule la Boussole Macro Bourse (VIX, Taux 10Y US, MSCI World CW8.PA, S&P 500)."""
    macro_tickers = ["^VIX", "^TNX", "CW8.PA", "^GSPC"]
    data = fetch_bulk_equity_metrics(macro_tickers, include_intraday=False)

    vix = data.get("^VIX", {}).get("price", 16.0) or 16.0
    tnx = data.get("^TNX", {}).get("price", 4.2) or 4.2
    cw8 = data.get("CW8.PA", {})
    cw8_price = cw8.get("price", 0.0)
    cw8_24h = cw8.get("change_24h_pct", 0.0)
    cw8_dist_sma200 = cw8.get("dist_sma200_pct", 0.0)
    cw8_dist_sma50 = cw8.get("dist_sma50_pct", 0.0)
    sp500_24h = data.get("^GSPC", {}).get("change_24h_pct", 0.0)

    if vix >= 28.0 or cw8_dist_sma200 < -5.0:
        regime = "RISK_OFF_VOLATILITY"
        macro_stars = 1
    elif vix >= 21.0 or cw8_dist_sma200 < 0.0:
        regime = "CAUTIOUS_CONSOLIDATION"
        macro_stars = 3
    elif vix <= 15.5 and cw8_dist_sma200 > 2.0 and tnx < 4.50:
        regime = "RISK_ON_EXPANSION"
        macro_stars = 5
    else:
        regime = "BALANCED_EQUITY_TREND"
        macro_stars = 4

    # Pénalité de tension obligataire si le 10 ans US dépasse 4.75%
    if tnx >= 4.75 and macro_stars > 2:
        macro_stars -= 1
        if regime == "BALANCED_EQUITY_TREND":
            regime = "CAUTIOUS_CONSOLIDATION"

    return {
        "regime": regime,
        "macro_stars": macro_stars,
        "vix": round(vix, 2),
        "us_10y_yield": round(tnx, 2),
        "cw8_price": cw8_price,
        "cw8_24h_pct": cw8_24h,
        "cw8_dist_sma50_pct": cw8_dist_sma50,
        "cw8_dist_sma200_pct": cw8_dist_sma200,
        "sp500_24h_pct": sp500_24h,
    }


if __name__ == "__main__":
    import time
    t0 = time.time()
    compass = get_equity_macro_compass()
    print(f"✅ Boussole Macro Bourse ({time.time() - t0:.2f}s) :", compass)
