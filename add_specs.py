import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")

def add_speculative_positions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Données basées sur tes avoirs réels constatés sur MEXC
    # Tu pourras ajuster les prix d'entrée, TP1 (x2) et Stop-Loss selon ta stratégie
    positions_to_add = [
        {
            "symbol": "KAS",
            "exchange": "MEXC",
            "price_entry": 0.03,      # Prix d'entrée estimé (ajustable)
            "amount_eur": 116.0,      # Montant réel constaté
            "tp1_price": 0.06,        # Objectif TP1 (x2)
            "tp2_price": 0.12,        # Objectif TP2 (x4)
            "sl_price": 0.02,         # Stop-Loss d'invalidation
            "sl_type": "invalidation"
        },
        {
            "symbol": "EDEL",
            "exchange": "MEXC",
            "price_entry": 0.04,      # Prix d'entrée estimé (ajustable)
            "amount_eur": 336.0,      # Montant réel constaté
            "tp1_price": 0.08,        # Objectif TP1 (x2)
            "tp2_price": 0.16,        # Objectif TP2 (x4)
            "sl_price": 0.025,         # Stop-Loss d'invalidation
            "sl_type": "invalidation"
        }
    ]

    for pos in positions_to_add:
        cursor.execute("""
            INSERT INTO speculative_trades (
                symbol, exchange, price_suggested, price_entry, amount_eur, 
                tp_price, tp1_price, tp2_price, sl_price, sl_type, status, step, taken_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 'ENTRY', ?)
        """, (
            pos["symbol"],
            pos["exchange"],
            pos["price_entry"],
            pos["price_entry"],
            pos["amount_eur"],
            pos["tp1_price"],
            pos["tp1_price"],
            pos["tp2_price"],
            pos["sl_price"],
            pos["sl_type"],
            datetime.now()
        ))
        print(f"✅ Position {pos['symbol']} ({pos['exchange']}) ajoutée en base avec succès ({pos['amount_eur']} €).")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    add_speculative_positions()
