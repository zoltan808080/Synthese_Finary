#!/usr/bin/env python3
"""
Sentinelle Proactive Bourse (equity_monitor.py).
Miroir de crypto_monitor.py adapté aux horaires Euronext / US :
- Met à jour le High-Water Mark (Trailing) en base SQLite
- Émet des alertes Telegram instantanées avec anti-spam 24h (TP1 atteint, SL frôlé/touché, survente RSI 1D <= 28)
"""

import os
import json
import time
import logging
import requests
from dotenv import load_dotenv
from equity_db_manager import (
    init_equity_db,
    get_connection,
    get_open_speculative_equities,
)
from equity_market_engine import fetch_bulk_equity_metrics

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

ALERT_STATE_FILE = "equity_alerts_state.json"


def send_telegram_alert(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not token or not chat_id:
        logging.warning("⚠️ Credentials Telegram manquants.")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        logging.error(f"Erreur envoi Telegram : {e}")


def load_alert_state():
    if os.path.exists(ALERT_STATE_FILE):
        try:
            with open(ALERT_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_alert_state(state):
    with open(ALERT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def run_equity_monitor():
    init_equity_db()
    positions = get_open_speculative_equities()
    if not positions:
        logging.info("Aucune position spéculative ouverte à surveiller.")
        return

    tickers = [p[2] for p in positions if p[2]]
    metrics_map = fetch_bulk_equity_metrics(tickers)
    state = load_alert_state()
    now = time.time()

    conn = get_connection()
    c = conn.cursor()

    alerts_sent = 0
    for row in positions:
        t_id, sym, ytick, acc, isin, entry_p, qty, amt_eur, tp1, tp2, sl, step, auto_flag, cw8_e, hwm, taken_at = row
        if not ytick or ytick not in metrics_map:
            continue

        m = metrics_map[ytick]
        live_p = m.get("price", 0.0)
        rsi_1d = m.get("rsi_1d", 50.0)
        if live_p <= 0:
            continue

        current_hwm = max(float(hwm or 0.0), entry_p)
        if live_p > current_hwm:
            c.execute("UPDATE speculative_equities SET highest_price_seen = ? WHERE id = ?", (live_p, t_id))

        pnl_pct = ((live_p - entry_p) / entry_p * 100.0) if entry_p > 0 else 0.0

        def can_alert(key_type):
            k = f"{t_id}_{key_type}"
            last_ts = state.get(k, 0.0)
            if (now - last_ts) > 86400:
                state[k] = now
                return True
            return False

        if tp1 > 0 and live_p >= tp1 and step == "ENTRY":
            if can_alert("TP1"):
                send_telegram_alert(
                    f"🎯 *OBJECTIF TP1 ATTEINT (ACTIONS)*\n"
                    f"• **{sym}** (`{ytick}`) sur _{acc}_\n"
                    f"• Cours Live : **{live_p:.2f} €** (PRU : {entry_p:.2f} € | **{pnl_pct:+.2f}%**)\n"
                    f"• Action recommandée : Écrêter 50% et passer le Stop-Loss au Break-Even ({entry_p:.2f} €)."
                )
                alerts_sent += 1

        if sl > 0 and live_p <= sl:
            if can_alert("SL_HIT"):
                send_telegram_alert(
                    f"🚨 *STOP-LOSS FRANCHI (ACTIONS)*\n"
                    f"• **{sym}** (`{ytick}`) sur _{acc}_\n"
                    f"• Cours Live : **{live_p:.2f} €** ≤ SL ({sl:.2f} €) | PnL : **{pnl_pct:+.2f}%**\n"
                    f"• Vérifiez l'opportunité d'éviction / Tax-Loss Harvesting sur CTO."
                )
                alerts_sent += 1
        elif sl > 0 and live_p <= (sl * 1.03):
            if can_alert("SL_NEAR"):
                send_telegram_alert(
                    f"⚠️ *STOP-LOSS FRÔLÉ (< 3%) — ACTIONS*\n"
                    f"• **{sym}** (`{ytick}`) : **{live_p:.2f} €** (SL : {sl:.2f} € | PnL : {pnl_pct:+.2f}%)"
                )
                alerts_sent += 1

        if rsi_1d <= 28.0:
            if can_alert("RSI_OVERSOLD"):
                send_telegram_alert(
                    f"🟢 *SIGNAL SURVENTE TECHNIQUE (RSI 1D = {rsi_1d:.1f})*\n"
                    f"• **{sym}** (`{ytick}`) : **{live_p:.2f} €** (Dist. SMA200 : {m.get('dist_sma200_pct', 0.0):+.1f}%)\n"
                    f"• Zone de rebond technique potentielle sur votre portefeuille."
                )
                alerts_sent += 1

    conn.commit()
    conn.close()
    save_alert_state(state)
    logging.info(f"✅ Cycle equity_monitor terminé ({len(positions)} positions vérifiées, {alerts_sent} alerte(s) envoyée(s)).")


if __name__ == "__main__":
    run_equity_monitor()
