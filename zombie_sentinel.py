#!/usr/bin/env python3
"""Sentinelle des Actifs Zombies & Anti-Bagholding v3.8.1.

Audit automatique hebdomadaire (dimanche 20h) contre l'attrition d'Alpha face au Bitcoin.
Intègre les 3 garde-fous doctrinaux :
1. Verrou d'ancienneté (< 21 jours) : protection absolue des positions spéculatives récentes.
2. Immunité des Piliers CORE : sanctuarisation des actifs avec thèse 'ACTIVE'.
3. Calcul d'Alpha d'exposition réelle : benchmark calculé depuis l'entrée pour les trades BDD.
"""

import argparse
import datetime
import logging
import os
import requests
from dotenv import load_dotenv

from db_manager import (
    clear_zombie_record,
    get_all_core_metadata,
    get_open_positions,
    init_db,
    update_zombie_record,
)

load_dotenv()
init_db()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")
DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")

COINGECKO_MAP = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "bnb": "binancecoin",
    "xrp": "ripple",
    "ada": "cardano",
    "trx": "tron",
    "link": "chainlink",
    "sui": "sui",
    "near": "near",
    "dot": "polkadot",
    "tao": "bittensor",
    "render": "render-token",
    "fet": "artificial-superintelligence-alliance",
    "ondo": "ondo-finance",
    "ray": "raydium",
    "kas": "kaspa",
    "hype": "hyperliquid",
    "ath": "aethir",
    "lit": "litentry",
}

STABLECOINS = {
    "EUR", "USD", "USDT", "USDC", "DAI", "FDUSD", "USDE", "PYUSD", "TUSD"
}


def get_usd_to_eur_rate():
    """Récupère le taux de conversion USD/EUR en temps réel."""
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
    return 0.883


def calculate_wilder_rsi(prices, period=14):
    """Calcul standard du RSI de Wilder sur série chronologique de clôtures journalières."""
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
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def get_30d_history_and_rsi(symbol, exchange=""):
    """Récupère le prix actuel, le prix à J-30, la variation 30j (%) et le RSI 1D."""
    symbol_u = symbol.upper().strip()
    symbol_l = symbol.lower().strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
            " Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    # 1. Source OKX (Marché Spot ou Perpetual USDT)
    for inst_id in [f"{symbol_u}-USDT", f"{symbol_u}-EUR"]:
        try:
            url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=1D&limit=35"
            r = requests.get(url, headers=headers, timeout=4)
            if r.status_code == 200:
                candles = r.json().get("data", [])
                if len(candles) >= 30:
                    current_price = float(candles[0][4])
                    price_30d = float(candles[29][4])
                    pnl_30d = ((current_price - price_30d) / price_30d) * 100.0
                    closes_chrono = [float(c[4]) for c in reversed(candles)]
                    rsi_1d = calculate_wilder_rsi(closes_chrono)
                    return current_price, price_30d, pnl_30d, rsi_1d
        except Exception:
            pass

    # 2. Source Kraken OHLC (intervalle 1440 min = 1D)
    kraken_symbols = [symbol_u]
    if symbol_u == "BTC":
        kraken_symbols.extend(["XBT", "XXBT"])
    for s in kraken_symbols:
        for pair in [f"{s}EUR", f"X{s}ZEUR", f"{s}USDT", f"{s}USD"]:
            try:
                url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440"
                r = requests.get(url, headers=headers, timeout=4)
                if r.status_code == 200:
                    res_dict = r.json().get("result", {})
                    for k, candles in res_dict.items():
                        if k != "last" and isinstance(candles, list) and len(candles) >= 30:
                            current_price = float(candles[-1][4])
                            price_30d = float(candles[-31][4])
                            pnl_30d = ((current_price - price_30d) / price_30d) * 100.0
                            closes = [float(c[4]) for c in candles[-35:]]
                            rsi_1d = calculate_wilder_rsi(closes)
                            return current_price, price_30d, pnl_30d, rsi_1d
            except Exception:
                pass

    # 3. Source CoinGecko (market_chart 30j)
    cg_id = COINGECKO_MAP.get(symbol_l, symbol_l)
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=eur&days=30"
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            if len(prices) >= 20:
                price_30d = float(prices[0][1])
                current_price = float(prices[-1][1])
                pnl_30d = ((current_price - price_30d) / price_30d) * 100.0
                step = max(1, len(prices) // 30)
                daily_closes = [p[1] for p in prices[::step]]
                rsi_1d = calculate_wilder_rsi(daily_closes)
                return current_price, price_30d, pnl_30d, rsi_1d
    except Exception:
        pass

    # 4. Source Binance (Fallback public)
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol_u}USDT&interval=1d&limit=35"
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code == 200:
            candles = r.json()
            if len(candles) >= 30:
                current_price = float(candles[-1][4])
                price_30d = float(candles[-31][4])
                pnl_30d = ((current_price - price_30d) / price_30d) * 100.0
                closes = [float(c[4]) for c in candles]
                rsi_1d = calculate_wilder_rsi(closes)
                return current_price, price_30d, pnl_30d, rsi_1d
    except Exception:
        pass

    return None, None, None, 50.0


def send_telegram_message(text):
    """Envoie le rapport de la sentinelle sur le canal Telegram configuré."""
    if not BOT_TOKEN or not ALLOWED_USER_ID:
        logging.warning("⚠️ Configuration Telegram manquante (BOT_TOKEN / USER_ID).")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ALLOWED_USER_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        logging.error(f"❌ Échec envoi Telegram : {e}")
        return False


def run_zombie_sentinel(send_telegram=True):
    """Exécute l'audit des actifs zombies avec respect strict des 3 garde-fous doctrinaux (v3.8.1)."""
    logging.info("🧟 [v3.8.1] Démarrage de la Sentinelle des Actifs Zombies (Anti-Bagholding)...")

    # 1. Calcul du Benchmark Bitcoin (30j)
    btc_now, btc_30d, btc_pnl_30d, btc_rsi = get_30d_history_and_rsi("BTC", "KRAKEN")
    if btc_pnl_30d is None or btc_now is None:
        logging.error("❌ Impossible de calculer la performance 30j du Bitcoin. Audit interrompu.")
        return []

    logging.info(
        f"₿ Benchmark BTC (30j) : {btc_pnl_30d:+.2f}% "
        f"| Prix J-30: {btc_30d:,.0f} € ➔ Actuel: {btc_now:,.0f} € | RSI 1D: {btc_rsi:.1f}"
    )

    # 2. Ingestion des soldes réels exchanges
    try:
        from exchange_connectors import get_all_exchange_crypto_balances
        balances = get_all_exchange_crypto_balances()
    except Exception as e:
        logging.error(f"❌ Impossible de charger les soldes exchanges : {e}")
        return []

    # 3. Ingestion du contexte BDD : positions ouvertes et métadonnées CORE
    open_trades = get_open_positions()
    core_metadata = get_all_core_metadata()
    now_dt = datetime.datetime.now()

    # Cartographie des positions ouvertes par symbole : {SYMBOLE: [infos_trades]}
    spec_trades_map = {}
    for t in open_trades:
        sym = str(t[1]).upper().replace("USDT", "").replace("EUR", "").strip()
        entry_p = float(t[3] or 0.0)
        taken_at_str = t[10]
        taken_dt = None
        if taken_at_str:
            try:
                taken_dt = datetime.datetime.fromisoformat(str(taken_at_str))
            except Exception:
                try:
                    taken_dt = datetime.datetime.strptime(str(taken_at_str), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

        btc_entry = float(t[12] or 0.0) if len(t) > 12 and t[12] else 0.0
        trade_dict = {
            "id": t[0],
            "entry_price": entry_p,
            "taken_dt": taken_dt,
            "btc_entry": btc_entry,
        }
        spec_trades_map.setdefault(sym, []).append(trade_dict)

    usd_eur_rate = get_usd_to_eur_rate()
    zombies_found = []
    audited_count = 0
    protected_recent_count = 0
    protected_core_count = 0

    # 4. Évaluation méthodique de chaque actif détenu
    for asset, info in balances.items():
        symbol_u = str(asset).upper().strip()

        # Exclusions doctrinales (Fiat, Stables, Benchmark BTC)
        if symbol_u in STABLECOINS or symbol_u == "BTC":
            continue

        amt = float(info.get("amount", 0.0)) if isinstance(info, dict) else float(info)
        exch = info.get("exchange", "EXCHANGE") if isinstance(info, dict) else "EXCHANGE"

        if amt <= 0:
            continue

        # Récupération cours et RSI 1D
        t_now, t_30d, t_pnl_30d, t_rsi = get_30d_history_and_rsi(symbol_u, exch)
        if t_now is None or t_pnl_30d is None:
            logging.warning(f"⚠️ Historique de cours introuvable pour {symbol_u} ({exch})")
            continue

        price_eur = t_now if "EUR" in exch.upper() or t_now > 1000 else (t_now * usd_eur_rate)
        val_eur = amt * price_eur

        # Seuil de matérialité doctrinal : ignorer les résidus < 30 €
        if val_eur < 30.0:
            continue

        audited_count += 1

        # ====================================================================
        # GARDE-FOU 1 : POSITION SPÉCULATIVE RÉCENTE (< 21 JOURS)
        # ====================================================================
        if symbol_u in spec_trades_map:
            trades = spec_trades_map[symbol_u]
            dts = [tr["taken_dt"] for tr in trades if tr["taken_dt"]]
            if dts:
                most_recent_dt = max(dts)
                days_held = (now_dt - most_recent_dt).days
                if days_held < 21:
                    protected_recent_count += 1
                    logging.info(
                        f"🛡️ [IMMUNITÉ Trade Récent] {symbol_u} ({exch}) : Détenu depuis {days_held}j (< 21j). "
                        "Couvert par la stratégie Moonbag (Stop-Loss / Trailing actif). Exclu de la sentinelle zombie."
                    )
                    clear_zombie_record(symbol_u)
                    continue

        # ====================================================================
        # GARDE-FOU 2 : GESTION DE LA POCHE CORE (IMMUNITÉ PAR DÉFAUT)
        # ====================================================================
        # Tout actif qui n'est pas un trade spéculatif fait partie de la poche CORE
        is_speculative_trade = symbol_u in spec_trades_map
        
        if not is_speculative_trade:
            meta = core_metadata.get(symbol_u, {})
            thesis = meta.get("thesis_status", "ACTIVE")
            
            # Seuls les actifs explicitement marqués en sortie sont éligibles à l'éviction
            if thesis not in ["EXIT_RECOMMENDED", "WATCHLIST_TRIM"]:
                protected_core_count += 1
                logging.info(
                    f"💎 [IMMUNITÉ CORE] {symbol_u} ({exch}) : Actif de conviction CORE (Thèse: '{thesis}'). "
                    "Immunité accordée contre la qualification zombie."
                )
                clear_zombie_record(symbol_u)
                continue
            else:
                logging.info(
                    f"⚠️ [CORE AUDITÉ] {symbol_u} ({exch}) : Tagué '{thesis}' en BDD. "
                    "Éligible à l'évaluation d'attrition d'Alpha."
                )

        # ====================================================================
        # ANALYSE D'ATTRITION RÉELLE POUR ACTIFS ÉLIGIBLES
        # (Actifs spéculatifs stagnants > 21j, CORE en 'EXIT' ou lignes orphelines)
        # ====================================================================
        is_spec_trade_old = symbol_u in spec_trades_map
        if is_spec_trade_old and spec_trades_map[symbol_u][0]["entry_price"] > 0:
            tr = spec_trades_map[symbol_u][0]
            token_pnl = ((t_now - tr["entry_price"]) / tr["entry_price"]) * 100.0
            if tr["btc_entry"] > 0 and btc_now > 0:
                btc_pnl_held = ((btc_now - tr["btc_entry"]) / tr["btc_entry"]) * 100.0
            else:
                btc_pnl_held = btc_pnl_30d
            alpha_eval = token_pnl - btc_pnl_held
            context_label = "depuis entrée trade"
        else:
            token_pnl = t_pnl_30d
            alpha_eval = t_pnl_30d - btc_pnl_30d
            context_label = "30 jours glissants"

        logging.info(
            f"🔍 [AUDIT] {symbol_u} ({exch}) : Val={val_eur:,.2f} € | PnL={token_pnl:+.2f}% "
            f"| Alpha vs BTC ({context_label})={alpha_eval:+.2f}% | RSI 1D={t_rsi:.1f}"
        )

        # Qualification d'actif zombie : Alpha <= -15.0%
        if alpha_eval <= -15.0:
            severity = "CRITICAL" if alpha_eval <= -25.0 else "STANDARD"
            update_zombie_record(
                symbol=symbol_u,
                exchange=exch,
                alpha_30d=alpha_eval,
                token_pnl=token_pnl,
                btc_pnl=btc_pnl_30d,
                val_eur=val_eur,
                rsi_1d=t_rsi,
                severity=severity,
            )
            zombies_found.append({
                "symbol": symbol_u,
                "exchange": exch,
                "amount": amt,
                "val_eur": val_eur,
                "token_pnl": token_pnl,
                "alpha_eval": alpha_eval,
                "rsi_1d": t_rsi,
                "severity": severity,
                "rsi_veto": t_rsi <= 30.0,
                "context_label": context_label,
            })
        else:
            clear_zombie_record(symbol_u)

    logging.info(
        f"✅ Fin du scan : {audited_count} actif(s) audité(s) | "
        f"{protected_recent_count} protégé(s) récents (<21j) | "
        f"{protected_core_count} pilier(s) CORE immunisé(s) | "
        f"{len(zombies_found)} actif(s) zombie(s) qualifié(s)."
    )

    if not send_telegram:
        return zombies_found

    # 5. Construction de la notification Telegram
    date_str = datetime.datetime.now().strftime("%d/%m/%Y à %Hh%M")

    if not zombies_found:
        msg = (
            "🛡️ *SENTINELLE DES ACTIFS ZOMBIES (v3.8.1)*\n"
            f"📅 _Audit hebdomadaire du {date_str}_\n\n"
            f"• **Benchmark BTC (30j)** : `{btc_pnl_30d:+.2f}%` (RSI 1D: `{btc_rsi:.1f}`)\n"
            f"• **Actifs audités** : `{audited_count}` lignes (>= 30 €)\n"
            f"• **Garde-fous actifs** : `{protected_recent_count}` trades récents protégés, `{protected_core_count}` piliers CORE sanctuarisés\n\n"
            "🟢 *SANTÉ DU PORTEFEUILLE OPTIMALE*\n"
            "Aucun actif stagnant ou orphelin ne présente d'attrition d'Alpha critique. "
            "Le capital est protégé contre l'érosion lente."
        )
        send_telegram_message(msg)
        return []

    zombies_found.sort(key=lambda x: x["alpha_eval"])

    msg = (
        "🧟 *ALERTE HEBDOMADAIRE : ACTIFS ZOMBIES DÉTECTÉS (v3.8.1)*\n"
        f"📅 _Audit du {date_str}_\n\n"
        f"• **Benchmark BTC (30j)** : `{btc_pnl_30d:+.2f}%` (RSI 1D: `{btc_rsi:.1f}`)\n"
        f"• **Lignes infectées** : `{len(zombies_found)}` sur `{audited_count}` audités\n"
        f"• **Protection** : `{protected_recent_count}` trades récents (<21j) et `{protected_core_count}` piliers CORE exclus d'office.\n\n"
        "⚠️ _Les actifs ci-dessous sont anciens ou sans thèse et accusent une sous-performance sévère face au Bitcoin :_\n\n"
    )

    ready_tickets = []
    veto_assets = []

    for z in zombies_found:
        badge_sev = "🔴 CRITIQUE" if z["severity"] == "CRITICAL" else "🟡 STANDARD"
        action_name = "LIQUIDATION TOTALE (100%)" if z["severity"] == "CRITICAL" else "ALLÈGEMENT (50%)"
        vol_to_sell = z["val_eur"] if z["severity"] == "CRITICAL" else (z["val_eur"] * 0.5)

        msg += (
            f"{badge_sev} *{z['symbol']}* ({z['exchange']}) :\n"
            f"• Valeur : *{z['val_eur']:,.2f} €* | Alpha : *{z['alpha_eval']:+.2f}%* ({z['context_label']})\n"
            f"• PnL actif : {z['token_pnl']:+.2f}%\n"
            f"• RSI 1D : *{z['rsi_1d']:.1f}* "
        )

        if z["rsi_veto"]:
            msg += "➔ ⏸️ *VETO SURVENTE (Attendre rebond RSI 50)*\n\n"
            veto_assets.append(z)
        else:
            msg += f"➔ ✂️ *{action_name} RECOMMANDÉ*\n\n"
            ready_tickets.append((z, action_name, vol_to_sell))

    if ready_tickets:
        msg += "🎯 *TICKETS D'ORDRES D'ÉVICTION PRÊTS (v3.8.1)* :\n```text\n"
        for i, (z, act, vol) in enumerate(ready_tickets, start=1):
            score_star = "⭐⭐⭐⭐ (4/5)" if z["severity"] == "STANDARD" else "⭐⭐⭐⭐⭐ (5/5)"
            vol_str = f"{vol:,.2f} €"
            alpha_str = f"{z['alpha_eval']:+.2f}%"
            msg += (
                f"┌────────────────────────────────────────────────────────┐\n"
                f"│ 🧟 TICKET D'ÉVICTION #{i} — SCORE : {score_star:<20}│\n"
                f"├────────────────────────────────────────────────────────┤\n"
                f"│ Exchange      : {z['exchange']:<39}│\n"
                f"│ Token         : {z['symbol']:<39}│\n"
                f"│ Action        : {act:<39}│\n"
                f"│ Volume à céder: {vol_str:<39}│\n"
                f"│ Type d'ordre  : SELL LIMIT (au cours spot ou rebond 4h)│\n"
                f"│ Réallocation  : 100% vers BTC (Accumulation défensive) │\n"
                f"│ Alpha vs BTC  : {alpha_str:<39}│\n"
                f"└────────────────────────────────────────────────────────┘\n\n"
            )
        msg += "```\n"

    if veto_assets:
        msg += (
            "🛡️ _Note de discipline : Les actifs sous veto RSI (RSI <= 30) ne disposent "
            "pas de ticket d'éviction immédiat pour éviter de capituler au plus bas d'un creux local._"
        )

    send_telegram_message(msg)
    return zombies_found


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentinelle Actifs Zombies v3.8.1")
    parser.add_argument("--dry-run", action="store_true", help="Scan sans envoi Telegram")
    args = parser.parse_args()

    run_zombie_sentinel(send_telegram=not args.dry_run)