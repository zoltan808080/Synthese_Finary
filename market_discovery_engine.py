#!/usr/bin/env python3
"""
Moteur de Découverte Dynamique du Marché & Screener Spéculatif (v4.0.1).

Scanne en temps réel les marchés liquides (OKX / Kraken), filtre automatiquement
les Majeures, la poche CORE, les Moonbags déjà ouverts en BDD, les actions tokenisées (xStocks),
les commodités et les tokens natifs d'exchange centralisés (CEX),
puis sélectionne les 15 à 20 meilleures opportunités spéculatives (Rebonds RSI & Momentum Alpha).
"""

import os
import time
import logging
import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
        " Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 1. Stablecoins, devises fiat et dérivés synthétiques / enveloppés
STABLECOINS_AND_WRAPPED = {
    "EUR", "USD", "USDT", "USDC", "DAI", "FDUSD", "USDE", "PYUSD", "TUSD",
    "BUSD", "USDD", "GUSD", "EURC", "EURL", "FRAX", "LUSD",
    "WBTC", "WETH", "STETH", "WSTETH", "CBETH", "RETH", "WEETH", "EZETH",
    "BTCB", "TBTC",
}

# 2. Commodités tokenisées (Or, métaux, etc.)
COMMODITIES_AND_SYNTHETICS = {
    "XAUT", "PAXG",
}

# 3. Actions tokenisées (xStocks OKX) et indices actions
TOKENIZED_STOCKS_BLACKLIST = {
    "XNVDA", "XMSTR", "XCRCL", "XTSLA", "XAAPL", "XAMZN", "XGOOGL", "XMETA",
    "XMSFT", "XCOIN", "XAMD", "XNFLX", "XPLTR", "XSPY", "XQQQ"
}

# 4. Jetons d'exchanges centralisés (Non éligibles Moonbag)
CEX_PLATFORM_TOKENS = {
    "OKB", "MX", "KCS", "HT", "GT", "BGB",
}

MAJORS = {"BTC", "ETH"}

CACHE_TTL_SECONDS = 900  # 15 minutes
_DISCOVERY_CACHE = {
    "timestamp": 0,
    "candidates": [],
    "symbols": []
}


def get_http_session():
    """Crée une session HTTP persistante avec pooling de connexions et retries automatiques."""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    retries = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    return session


def calculate_wilder_rsi(prices, period=14):
    """Calcul standard du RSI de Wilder sur série chronologique de clôtures."""
    if not prices or len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def get_portfolio_exclusions():
    """
    Extrait l'ensemble des symboles à exclure du radar spéculatif :
    - Majeures (BTC, ETH)
    - Piliers CORE (statut ACTIVE ou WATCHLIST_TRIM)
    - Positions Moonbags déjà ouvertes en base de données
    - Commodités (XAUT, PAXG), Stablecoins et actions tokenisées
    - Actifs classés manuellement en CORE
    """
    exclusions = set(MAJORS)
    exclusions.update(STABLECOINS_AND_WRAPPED)
    exclusions.update(COMMODITIES_AND_SYNTHETICS)
    exclusions.update(TOKENIZED_STOCKS_BLACKLIST)
    exclusions.update(CEX_PLATFORM_TOKENS)

    # 1. Extraction BDD SQLite
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            # Positions spéculatives ouvertes
            cur.execute("SELECT symbol FROM speculative_trades WHERE status = 'OPEN'")
            for row in cur.fetchall():
                if row[0]:
                    s = str(row[0]).upper().replace("USDT", "").replace("EUR", "").strip()
                    exclusions.add(s)

            # Métadonnées CORE (Thèses actives ou sous surveillance)
            try:
                cur.execute("SELECT symbol, thesis_status FROM core_assets_metadata")
                for row in cur.fetchall():
                    if row[0]:
                        s = str(row[0]).upper().strip()
                        if row[1] in ["ACTIVE", "WATCHLIST_TRIM"]:
                            exclusions.add(s)
            except sqlite3.OperationalError:
                pass

            # Trades ignorés manuellement (classés CORE)
            try:
                cur.execute("SELECT symbol FROM ignored_manual_trades")
                for row in cur.fetchall():
                    if row[0]:
                        exclusions.add(str(row[0]).upper().strip())
            except sqlite3.OperationalError:
                pass

            conn.close()
        except Exception as e:
            logging.warning(f"⚠️ Erreur lors de la lecture des exclusions BDD : {e}")

    # 2. Extraction des soldes réels détenus sur les exchanges
    try:
        from exchange_connectors import get_all_exchange_crypto_balances
        balances = get_all_exchange_crypto_balances()
        if balances:
            for asset in balances.keys():
                s = str(asset).upper().strip()
                if s not in STABLECOINS_AND_WRAPPED:
                    exclusions.add(s)
    except Exception:
        pass

    return exclusions


def fetch_okx_liquid_spot_tickers(session, min_volume_usd=4_000_000):
    """
    Récupère l'ensemble des paires Spot USDT sur OKX ayant un volume 24h suffisant (>= 4M$).
    Returns: list of dict
    """
    url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
    candidates = []
    try:
        res = session.get(url, timeout=5.0)
        if res.status_code == 200:
            data = res.json().get("data", [])
            for item in data:
                inst_id = str(item.get("instId", ""))
                if not inst_id.endswith("-USDT"):
                    continue

                base_sym = inst_id.replace("-USDT", "").upper().strip()

                # Filtre actions tokenisées (commence par X suivi de 3+ lettres d'action)
                if base_sym.startswith("X") and len(base_sym) >= 5 and base_sym in TOKENIZED_STOCKS_BLACKLIST:
                    continue

                # Volume en devise de cotation (USDT ~= USD)
                vol_usd = float(item.get("volCcy24h", 0.0) or 0.0)
                if vol_usd < min_volume_usd:
                    continue

                last_px = float(item.get("last", 0.0) or 0.0)
                open_24h = float(item.get("open24h", 0.0) or 0.0)
                pnl_24h = ((last_px - open_24h) / open_24h * 100.0) if open_24h > 0 else 0.0

                candidates.append({
                    "symbol": base_sym,
                    "inst_id": inst_id,
                    "exchange": "OKX",
                    "last_price": last_px,
                    "volume_24h_usd": vol_usd,
                    "pnl_24h_pct": pnl_24h,
                })
    except Exception as e:
        logging.error(f"❌ Erreur lors de la récupération des tickers OKX : {e}")

    # Fallback CoinGecko si OKX échoue
    if not candidates:
        candidates = fetch_coingecko_fallback_tickers(session)

    return candidates


def fetch_coingecko_fallback_tickers(session):
    """Fallback public CoinGecko : Top 100 par volume."""
    candidates = []
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    try:
        res = session.get(url, timeout=5.0)
        if res.status_code == 200:
            data = res.json()
            for item in data:
                sym = str(item.get("symbol", "")).upper().strip()
                vol = float(item.get("total_volume", 0.0) or 0.0)
                pnl_24h = float(item.get("price_change_percentage_24h", 0.0) or 0.0)
                last_px = float(item.get("current_price", 0.0) or 0.0)
                candidates.append({
                    "symbol": sym,
                    "inst_id": f"{sym}-USDT",
                    "exchange": "OKX",
                    "last_price": last_px,
                    "volume_24h_usd": vol,
                    "pnl_24h_pct": pnl_24h,
                })
    except Exception as e:
        logging.error(f"❌ Erreur fallback CoinGecko : {e}")
    return candidates


def fetch_token_tech_micro_metrics(session, candidate):
    """
    Récupère en parallèle les métriques RSI (4h et 1D) et le Funding Rate 8h pour un candidat.
    """
    sym = candidate["symbol"]
    inst_id = candidate["inst_id"]

    rsi_4h = 50.0
    rsi_1d = 50.0
    funding_8h = 0.01

    # 1. Bougies 4H pour le RSI 4h
    try:
        url_4h = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=4H&limit=30"
        r_4h = session.get(url_4h, timeout=3.5)
        if r_4h.status_code == 200:
            candles_4h = r_4h.json().get("data", [])
            if len(candles_4h) >= 15:
                closes = [float(c[4]) for c in reversed(candles_4h)]
                rsi_4h = calculate_wilder_rsi(closes)
    except Exception:
        pass

    # 2. Bougies 1D pour le RSI 1D
    try:
        url_1d = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=1D&limit=30"
        r_1d = session.get(url_1d, timeout=3.5)
        if r_1d.status_code == 200:
            candles_1d = r_1d.json().get("data", [])
            if len(candles_1d) >= 15:
                closes_1d = [float(c[4]) for c in reversed(candles_1d)]
                rsi_1d = calculate_wilder_rsi(closes_1d)
    except Exception:
        pass

    # 3. Funding Rate Dérivés (OKX Swap)
    try:
        swap_id = f"{sym}-USDT-SWAP"
        url_fr = f"https://www.okx.com/api/v5/public/funding-rate?instId={swap_id}"
        r_fr = session.get(url_fr, timeout=2.5)
        if r_fr.status_code == 200:
            fr_data = r_fr.json().get("data", [])
            if fr_data:
                funding_8h = float(fr_data[0].get("fundingRate", 0.0)) * 100.0
    except Exception:
        pass

    # Scoring d'opportunité spéculative (0 à 100)
    opportunity_type = "NEUTRE"
    score = 50.0

    if rsi_4h <= 35.0:
        opportunity_type = "🎯 REBOND SUR SUPPORT (Dip Buyer)"
        score = 85.0 + (35.0 - rsi_4h)
    elif rsi_4h <= 45.0:
        opportunity_type = "📉 REPLI TACTIQUE"
        score = 75.0 + (45.0 - rsi_4h)
    elif 50.0 <= rsi_4h <= 65.0 and candidate["pnl_24h_pct"] > 0:
        opportunity_type = "🚀 MOMENTUM HAUSSIER"
        score = 70.0 + min(20.0, candidate["pnl_24h_pct"])
    elif rsi_4h >= 75.0:
        opportunity_type = "⚠️ SURCHAUFFE (Pullback probable)"
        score = 30.0

    if funding_8h > 0.03:
        score -= 20.0
    elif funding_8h < 0.0:
        score += 5.0

    enriched = dict(candidate)
    enriched.update({
        "rsi_4h": rsi_4h,
        "rsi_1d": rsi_1d,
        "funding_8h_pct": funding_8h,
        "opportunity_type": opportunity_type,
        "speculative_score": round(score, 1)
    })
    return enriched


def get_dynamic_speculative_candidates(top_n=15, min_volume_usd=4_000_000, force_refresh=False):
    """
    Moteur principal : génère la liste dynamique des 15 à 20 meilleurs candidats spéculatifs
    du marché après application stricte des filtres doctrinaux et du scoring technique.
    Utilise un cache de 15 minutes pour préserver le CPU de la VM.
    """
    global _DISCOVERY_CACHE
    now = time.time()

    if not force_refresh and (now - _DISCOVERY_CACHE["timestamp"]) < CACHE_TTL_SECONDS:
        if _DISCOVERY_CACHE["candidates"]:
            return _DISCOVERY_CACHE["candidates"], _DISCOVERY_CACHE["symbols"]

    logging.info("🔍 Démarrage du Moteur de Découverte Dynamique du Marché...")
    session = get_http_session()

    # 1. Récupération des exclusions de portefeuille
    exclusions = get_portfolio_exclusions()
    logging.info(f"🛡️ {len(exclusions)} actif(s) exclu(s) par la doctrine (Majeures, CORE, Trades BDD, Stables, Commodités, xStocks, CEX).")

    # 2. Récupération des paires liquides (Seuil 4M$)
    raw_tickers = fetch_okx_liquid_spot_tickers(session, min_volume_usd=min_volume_usd)
    eligible_pool = [t for t in raw_tickers if t["symbol"] not in exclusions]
    logging.info(f"📊 {len(raw_tickers)} paires liquides analysées (>= {min_volume_usd/1e6:,.1f} M$) ➔ {len(eligible_pool)} candidates éligibles après filtrage.")

    if not eligible_pool:
        fallback_syms = ["APT", "SEI", "TIA", "AAVE", "INJ", "ENA", "JUP", "KAS"]
        eligible_pool = [
            {"symbol": s, "inst_id": f"{s}-USDT", "exchange": "OKX", "last_price": 0.0, "volume_24h_usd": 25e6, "pnl_24h_pct": 0.0}
            for s in fallback_syms if s not in exclusions
        ]

    # Limiter le pool à pré-screener pour ménager le CPU
    pool_to_scan = sorted(eligible_pool, key=lambda x: x["volume_24h_usd"], reverse=True)[:45]

    # 3. Calcul parallèle des métriques techniques & dérivées
    enriched_candidates = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_token_tech_micro_metrics, session, cand): cand for cand in pool_to_scan}
        for future in as_completed(futures):
            try:
                res = future.result()
                enriched_candidates.append(res)
            except Exception:
                pass

    # 4. Tri par score de pertinence spéculative
    enriched_candidates.sort(key=lambda x: x["speculative_score"], reverse=True)
    final_candidates = enriched_candidates[:top_n]
    final_symbols = [c["symbol"] for c in final_candidates]

    _DISCOVERY_CACHE["timestamp"] = now
    _DISCOVERY_CACHE["candidates"] = final_candidates
    _DISCOVERY_CACHE["symbols"] = final_symbols

    logging.info(f"✅ Découverte terminée : {len(final_candidates)} pépites sélectionnées (Top: {', '.join(final_symbols[:6])}...)")
    return final_candidates, final_symbols


if __name__ == "__main__":
    print("=" * 80)
    print("🚀 TEST DU MOTEUR DE DÉCOUVERTE DYNAMIQUE DU MARCHÉ (v4.0.1)")
    print("=" * 80)
    candidates, symbols = get_dynamic_speculative_candidates(top_n=15, min_volume_usd=4_000_000, force_refresh=True)

    print(f"\n🎯 TOP {len(candidates)} CANDIDATS SPÉCULATIFS SÉLECTIONNÉS :")
    print(f"Tickers extraits : {', '.join(symbols)}\n")
    header_line = f"{'SYMBOLE':<10} | {'PRIX ($)':<10} | {'VOL 24H ($)':<14} | {'VAR 24H':<9} | {'RSI 4H':<8} | {'RSI 1D':<8} | {'FUNDING 8H':<11} | {'PROFIL'}"
    print(header_line)
    print("-" * 115)
    for c in candidates:
        vol_str = f"{c['volume_24h_usd']/1e6:,.1f} M$"
        px_str = f"{c['last_price']:.4f} $" if c['last_price'] < 10 else f"{c['last_price']:.2f} $"
        row_str = (
            f"{c['symbol']:<10} | {px_str:<10} | {vol_str:<14} | {c['pnl_24h_pct']:+6.2f}%  | "
            f"{c['rsi_4h']:<8.1f} | {c['rsi_1d']:<8.1f} | {c['funding_8h_pct']:+8.4f}% | {c['opportunity_type']}"
        )
        print(row_str)
    print("=" * 115)