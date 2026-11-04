#!/usr/bin/env python3
"""Script ponctuel de backfill : Récupération du cours historique du BTC à la date d'ouverture des trades."""

from datetime import datetime
import os
import sqlite3
import requests

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")


def get_historical_btc_price(timestamp_dt):
    """Interroge l'API Kraken OHLC pour retrouver le prix du BTC/EUR au timestamp le plus proche."""
    try:
        ts = int(timestamp_dt.timestamp())
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTEUR&interval=60&since={ts - 3600}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("result", {})
            for key, ohlc in data.items():
                if key != "last" and len(ohlc) > 0:
                    # ohlc[0] est la bougie la plus proche du timestamp [time, open, high, low, close, ...]
                    close_price = float(ohlc[0][4])
                    if close_price > 0:
                        return close_price
    except Exception as e:
        print(f"⚠️ Erreur fetch historique BTC pour {timestamp_dt}: {e}")

    # Fallback CoinGecko si Kraken ne renvoie pas la donnée
    try:
        date_str = timestamp_dt.strftime("%d-%m-%Y")
        url_cg = f"https://api.coingecko.com/api/v3/coins/bitcoin/history?date={date_str}"
        res_cg = requests.get(url_cg, timeout=5)
        if res_cg.status_code == 200:
            data_cg = res_cg.json()
            price = data_cg.get("market_data", {}).get("current_price", {}).get("eur", 0.0)
            if price > 0:
                return float(price)
    except Exception as e:
        print(f"⚠️ Erreur fallback CoinGecko pour {timestamp_dt}: {e}")

    return 0.0


def run_backfill():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, symbol, taken_at 
        FROM speculative_trades 
        WHERE status = 'OPEN' AND (btc_price_entry IS NULL OR btc_price_entry <= 0)
    """)
    rows = cursor.fetchall()

    if not rows:
        print("✅ Aucune position ouverte ne nécessite de backfill BTC.")
        conn.close()
        return

    print(f"🔍 {len(rows)} position(s) ouverte(s) à réhydrater avec leur cours BTC historique...")

    for trade_id, symbol, taken_at_str in rows:
        if not taken_at_str:
            print(f"⚠️ Trade ID {trade_id} ({symbol}) sans date taken_at, ignoré.")
            continue

        try:
            taken_dt = datetime.strptime(taken_at_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                taken_dt = datetime.fromisoformat(taken_at_str)
            except Exception:
                print(f"⚠️ Format date invalide pour ID {trade_id}: {taken_at_str}")
                continue

        btc_price = get_historical_btc_price(taken_dt)

        if btc_price > 0:
            cursor.execute(
                "UPDATE speculative_trades SET btc_price_entry = ? WHERE id = ?",
                (btc_price, trade_id),
            )
            print(f"✅ Trade ID {trade_id} ({symbol}) — Date: {taken_dt} ➔ BTC Entry: {btc_price:,.2f} €")
        else:
            print(f"❌ Impossible de récupérer le cours BTC pour ID {trade_id} ({symbol})")

    conn.commit()
    conn.close()
    print("🎉 Backfill terminé avec succès !")


if __name__ == "__main__":
    run_backfill()
