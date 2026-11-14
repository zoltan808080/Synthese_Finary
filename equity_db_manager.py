#!/usr/bin/env python3
"""
Gestionnaire de Base de Données SQLite pour le Cockpit Actions & Patrimoine (equity_wealth.db).
Architecture calquée sur db_manager.py (v4.0.0) :
- Classification des 3 poches (LT_SALARIAL, MT_CORE_ETF, CT_SPECULATIVE) + mapping Ticker Yahoo
- Suivi des positions spéculatives ('Actions Chocs') avec PRU, TP1/TP2, SL, Trailing HWM et Alpha vs CW8
- Métadonnées des thèses Cœur MT/LT (1-Click IA)
- Sentinelle Zombies & Tax-Loss Harvesting CTO
- Journal FIFO des positions clôturées
"""

import sqlite3
import datetime
import json
import os

DB_PATH = os.getenv("EQUITY_DB_PATH", "equity_wealth.db")


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_equity_db():
    conn = get_connection()
    c = conn.cursor()

    # 1. Référentiel de classification des lignes Finary & Tickers Yahoo Finance
    c.execute("""
        CREATE TABLE IF NOT EXISTS asset_classification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            account TEXT NOT NULL,
            isin TEXT,
            yahoo_ticker TEXT,
            bucket TEXT NOT NULL DEFAULT 'MT_CORE_ETF',
            is_excluded INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Positions actives de la Poche Spéculative ('Actions Chocs / Play')
    c.execute("""
        CREATE TABLE IF NOT EXISTS speculative_equities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            yahoo_ticker TEXT,
            account TEXT NOT NULL,
            isin TEXT,
            price_entry REAL NOT NULL,
            quantity REAL NOT NULL,
            amount_eur REAL NOT NULL,
            tp1_price REAL DEFAULT 0.0,
            tp2_price REAL DEFAULT 0.0,
            sl_price REAL DEFAULT 0.0,
            sl_type TEXT DEFAULT 'invalidation',
            status TEXT DEFAULT 'OPEN',
            step TEXT DEFAULT 'ENTRY',
            auto_tp_sl INTEGER DEFAULT 1,
            cw8_price_entry REAL DEFAULT 0.0,
            highest_price_seen REAL DEFAULT 0.0,
            taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
    """)

    # 3. Journal FIFO des positions clôturées
    c.execute("""
        CREATE TABLE IF NOT EXISTS closed_equity_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            symbol TEXT NOT NULL,
            account TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            amount_eur REAL NOT NULL,
            pnl_eur REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            opened_at TIMESTAMP,
            closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            close_reason TEXT
        )
    """)

    # 4. Métadonnées & Thèses 1-Click de la Poche Cœur MT/LT (Miroir core_assets_metadata)
    c.execute("""
        CREATE TABLE IF NOT EXISTS core_etf_metadata (
            asset_key TEXT PRIMARY KEY,
            thesis_status TEXT DEFAULT 'ACTIVE',
            notes TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 5. Sentinelle Zombies & Tax-Loss Harvesting CTO
    c.execute("""
        CREATE TABLE IF NOT EXISTS equity_zombies (
            asset_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            account TEXT NOT NULL,
            alpha_vs_cw8_pct REAL,
            unrealized_pnl_eur REAL,
            current_value_eur REAL,
            rsi_1d REAL,
            severity TEXT DEFAULT 'STANDARD',
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def upsert_asset_classification(asset_key, name, account, isin="", yahoo_ticker="", bucket="MT_CORE_ETF"):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO asset_classification (asset_key, name, account, isin, yahoo_ticker, bucket, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(asset_key) DO UPDATE SET
            name = excluded.name,
            account = excluded.account,
            isin = CASE WHEN excluded.isin != '' THEN excluded.isin ELSE asset_classification.isin END,
            yahoo_ticker = CASE WHEN excluded.yahoo_ticker != '' THEN excluded.yahoo_ticker ELSE asset_classification.yahoo_ticker END,
            bucket = excluded.bucket,
            updated_at = CURRENT_TIMESTAMP
    """, (asset_key, name, account, isin, yahoo_ticker, bucket))
    conn.commit()
    conn.close()


def get_all_classifications():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT asset_key, name, account, isin, yahoo_ticker, bucket, is_excluded FROM asset_classification ORDER BY bucket, account, name")
    rows = c.fetchall()
    conn.close()
    return [
        {
            "asset_key": r[0],
            "name": r[1],
            "account": r[2],
            "isin": r[3] or "",
            "yahoo_ticker": r[4] or "",
            "bucket": r[5],
            "is_excluded": bool(r[6]),
        }
        for r in rows
    ]


def get_open_speculative_equities():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, symbol, yahoo_ticker, account, isin, price_entry, quantity, amount_eur,
               tp1_price, tp2_price, sl_price, step, auto_tp_sl, cw8_price_entry, highest_price_seen, taken_at
        FROM speculative_equities
        WHERE status = 'OPEN'
        ORDER BY amount_eur DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def add_speculative_equity(symbol, account, price_entry, quantity, amount_eur, yahoo_ticker="", isin="", tp1_price=0.0, tp2_price=0.0, sl_price=0.0, cw8_price_entry=0.0):
    conn = get_connection()
    c = conn.cursor()
    tp1 = tp1_price if tp1_price > 0 else round(price_entry * 1.35, 4)
    tp2 = tp2_price if tp2_price > 0 else round(price_entry * 1.80, 4)
    sl = sl_price if sl_price > 0 else round(price_entry * 0.85, 4)

    c.execute("""
        INSERT INTO speculative_equities (
            symbol, yahoo_ticker, account, isin, price_entry, quantity, amount_eur,
            tp1_price, tp2_price, sl_price, cw8_price_entry, highest_price_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, yahoo_ticker, account, isin, price_entry, quantity, amount_eur, tp1, tp2, sl, cw8_price_entry, price_entry))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_equity_levels(trade_id, tp1_price, tp2_price, sl_price):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE speculative_equities
        SET tp1_price = ?, tp2_price = ?, sl_price = ?
        WHERE id = ?
    """, (tp1_price, tp2_price, sl_price, trade_id))
    conn.commit()
    conn.close()


def set_equity_break_even(trade_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE speculative_equities SET sl_price = price_entry WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()


def toggle_equity_auto_tpsl(trade_id, enabled: bool):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE speculative_equities SET auto_tp_sl = ? WHERE id = ?", (1 if enabled else 0, trade_id))
    conn.commit()
    conn.close()


def close_equity_trade(trade_id, exit_price=None, reason="MANUAL_CLOSE"):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT symbol, account, price_entry, quantity, amount_eur, taken_at FROM speculative_equities WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row:
        sym, acc, entry, qty, amt, taken_at = row
        ex_p = exit_price if (exit_price and exit_price > 0) else entry
        pnl_eur = (qty * ex_p) - amt
        pnl_pct = ((ex_p - entry) / entry * 100.0) if entry > 0 else 0.0
        c.execute("""
            INSERT INTO closed_equity_trades (trade_id, symbol, account, entry_price, exit_price, amount_eur, pnl_eur, pnl_pct, opened_at, close_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, sym, acc, entry, ex_p, amt, pnl_eur, pnl_pct, taken_at, reason))
        c.execute("UPDATE speculative_equities SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()


def get_closed_equity_history():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, symbol, account, entry_price, exit_price, amount_eur, pnl_eur, pnl_pct, opened_at, closed_at, close_reason
        FROM closed_equity_trades
        ORDER BY closed_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def get_all_core_etf_metadata():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT asset_key, thesis_status, notes FROM core_etf_metadata")
    rows = c.fetchall()
    conn.close()
    return {r[0]: {"thesis_status": r[1], "notes": r[2] or ""} for r in rows}


def update_core_etf_metadata(asset_key, thesis_status="ACTIVE", notes=""):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO core_etf_metadata (asset_key, thesis_status, notes, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(asset_key) DO UPDATE SET
            thesis_status = excluded.thesis_status,
            notes = excluded.notes,
            updated_at = CURRENT_TIMESTAMP
    """, (asset_key, thesis_status, notes))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_equity_db()
    print("✅ Base de données 'equity_wealth.db' initialisée avec succès.")
