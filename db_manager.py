#!/usr/bin/env python3
"""Gestionnaire SQLite du Cockpit Spéculatif v3.8.0 (Moonbag, Cycle de Vie CORE, Benchmark BTC, High-Water Mark & Sentinelle Actifs Zombies)."""

from datetime import datetime
import os
import sqlite3

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")


def init_core_metadata_table():
    """Initialise spécifiquement la table des métadonnées et thèses CORE (v3.6.0+)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS core_assets_metadata (
            symbol TEXT PRIMARY KEY,
            thesis_status TEXT DEFAULT 'ACTIVE', -- 'ACTIVE', 'WATCHLIST_TRIM', 'EXIT_RECOMMENDED'
            target_weight_pct REAL DEFAULT 0.0,
            notes TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def init_zombie_tracking_db():
    """Initialise la table de suivi des actifs zombies et attrition d'Alpha (v3.8.0)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS zombie_assets_tracking (
            symbol TEXT PRIMARY KEY,
            exchange TEXT,
            alpha_30d REAL,
            token_pnl_30d REAL,
            btc_pnl_30d REAL,
            current_val_eur REAL,
            rsi_1d REAL,
            severity TEXT, -- 'STANDARD' (-15% à -25%), 'CRITICAL' (<= -25%)
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_alerted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def init_db():
    """Initialise l'ensemble des tables SQLite et applique les migrations douces."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Table des positions spéculatives (Moonbag)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS speculative_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            exchange TEXT,
            price_suggested REAL,
            price_entry REAL,
            amount_eur REAL,
            tp_price REAL,
            sl_price REAL,
            status TEXT DEFAULT 'SUGGESTED', -- SUGGESTED, OPEN, IGNORED, CLOSED
            alert_status TEXT DEFAULT 'NONE', -- NONE, TP_ALERTED, SL_ALERTED, BE_SET
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            taken_at TIMESTAMP,
            closed_at TIMESTAMP
        )
    """)

    # Migrations douces pour speculative_trades
    cursor.execute("PRAGMA table_info(speculative_trades)")
    columns = [col[1] for col in cursor.fetchall()]

    if "alert_status" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN alert_status TEXT DEFAULT 'NONE'"
        )
    if "closed_at" not in columns:
        cursor.execute("ALTER TABLE speculative_trades ADD COLUMN closed_at TIMESTAMP")
    if "tp1_price" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN tp1_price REAL DEFAULT 0.0"
        )
    if "tp2_price" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN tp2_price REAL DEFAULT 0.0"
        )
    if "sl_type" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN sl_type TEXT DEFAULT 'invalidation'"
        )
    if "step" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN step TEXT DEFAULT 'ENTRY'"
        )
    if "auto_tp_sl" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN auto_tp_sl INTEGER DEFAULT 1"
        )
    if "btc_price_entry" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN btc_price_entry REAL DEFAULT 0.0"
        )
    if "highest_price_seen" not in columns:
        cursor.execute(
            "ALTER TABLE speculative_trades ADD COLUMN highest_price_seen REAL DEFAULT 0.0"
        )

    # 2. Table des trades manuels ignorés ou classés CORE
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ignored_manual_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            exchange TEXT,
            cost_eur REAL,
            price_eur REAL,
            reason TEXT DEFAULT 'CORE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Table du Journal de Trading (Historique des positions clôturées - FIFO)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            symbol TEXT NOT NULL,
            exchange TEXT,
            entry_price REAL,
            exit_price REAL,
            amount_eur REAL,
            pnl_eur REAL,
            pnl_pct REAL,
            opened_at TIMESTAMP,
            closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            close_reason TEXT DEFAULT 'MANUAL'
        )
    """)

    # 4. Table des métadonnées qualitatives et de cycle de vie des actifs CORE (v3.6.0)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS core_assets_metadata (
            symbol TEXT PRIMARY KEY,
            thesis_status TEXT DEFAULT 'ACTIVE', -- 'ACTIVE', 'WATCHLIST_TRIM', 'EXIT_RECOMMENDED'
            target_weight_pct REAL DEFAULT 0.0,
            notes TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 5. Table de suivi des actifs zombies (v3.8.0)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS zombie_assets_tracking (
            symbol TEXT PRIMARY KEY,
            exchange TEXT,
            alpha_30d REAL,
            token_pnl_30d REAL,
            btc_pnl_30d REAL,
            current_val_eur REAL,
            rsi_1d REAL,
            severity TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_alerted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ==========================================
# GESTION DES ACTIFS ZOMBIES (v3.8.0)
# ==========================================

def update_zombie_record(
    symbol: str,
    exchange: str,
    alpha_30d: float,
    token_pnl: float,
    btc_pnl: float,
    val_eur: float,
    rsi_1d: float,
    severity: str,
):
    """Enregistre ou met à jour la détection d'un actif zombie (v3.8.0)."""
    init_zombie_tracking_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_iso = datetime.now().isoformat()
    cursor.execute(
        """
        INSERT INTO zombie_assets_tracking 
        (symbol, exchange, alpha_30d, token_pnl_30d, btc_pnl_30d, current_val_eur, rsi_1d, severity, detected_at, last_alerted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            exchange = excluded.exchange,
            alpha_30d = excluded.alpha_30d,
            token_pnl_30d = excluded.token_pnl_30d,
            btc_pnl_30d = excluded.btc_pnl_30d,
            current_val_eur = excluded.current_val_eur,
            rsi_1d = excluded.rsi_1d,
            severity = excluded.severity,
            last_alerted = excluded.last_alerted
    """,
        (
            symbol.upper().strip(),
            exchange,
            alpha_30d,
            token_pnl,
            btc_pnl,
            val_eur,
            rsi_1d,
            severity,
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    conn.close()


def get_active_zombies():
    """Récupère l'inventaire des actifs zombies actuellement identifiés, triés par sous-performance."""
    init_zombie_tracking_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, exchange, alpha_30d, token_pnl_30d, btc_pnl_30d, current_val_eur, rsi_1d, severity, detected_at, last_alerted 
        FROM zombie_assets_tracking 
        ORDER BY alpha_30d ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def clear_zombie_record(symbol: str):
    """Supprime un actif de la liste zombie s'il a recouvré un Alpha normal (> -15%)."""
    init_zombie_tracking_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM zombie_assets_tracking WHERE symbol = ?",
        (symbol.upper().strip(),),
    )
    conn.commit()
    conn.close()


# ==========================================
# GESTION QUALITATIVE DES ACTIFS CORE (v3.6.0)
# ==========================================

def get_all_core_metadata() -> dict:
    """Récupère l'ensemble des statuts qualitatifs et thèses des actifs CORE sous forme de dictionnaire."""
    init_core_metadata_table()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symbol, thesis_status, target_weight_pct, notes FROM core_assets_metadata"
    )
    rows = cursor.fetchall()
    conn.close()
    return {
        row[0]: {
            "thesis_status": row[1] or "ACTIVE",
            "target_weight_pct": float(row[2] or 0.0),
            "notes": row[3] or "",
        }
        for row in rows
    }


def update_core_asset_metadata(
    symbol: str, thesis_status: str, notes: str = "", target_weight_pct: float = 0.0
):
    """Met à jour ou insère la qualification d'un actif CORE (v3.6.0)."""
    init_core_metadata_table()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO core_assets_metadata (symbol, thesis_status, notes, target_weight_pct, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(symbol) DO UPDATE SET
            thesis_status = excluded.thesis_status,
            notes = excluded.notes,
            target_weight_pct = excluded.target_weight_pct,
            updated_at = CURRENT_TIMESTAMP
    """,
        (symbol.upper().strip(), thesis_status, notes, target_weight_pct),
    )
    conn.commit()
    conn.close()


# ==========================================
# GESTION DES POSITIONS SPÉCULATIVES (MOONBAG)
# ==========================================

def toggle_auto_tp_sl(trade_id, status):
    """Active (1) ou désactive (0) la gestion automatique TP/SL par l'IA pour une position."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE speculative_trades SET auto_tp_sl = ? WHERE id = ?",
        (1 if status else 0, trade_id),
    )
    conn.commit()
    conn.close()


def get_auto_tp_sl_status():
    """Retourne un dictionnaire {trade_id: auto_tp_sl_bool} pour toutes les positions ouvertes."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, auto_tp_sl FROM speculative_trades WHERE status = 'OPEN'"
    )
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: bool(row[1]) if row[1] is not None else True for row in rows}


def add_recommendation(
    symbol,
    exchange,
    price,
    amount_eur,
    tp1_price,
    tp2_price,
    sl_price,
    sl_type="invalidation",
    btc_price_entry=0.0,
):
    """Enregistre une recommandation avec paliers TP1, TP2, prix repère du BTC et sommet initial."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO speculative_trades (
            symbol, exchange, price_suggested, amount_eur, 
            tp_price, tp1_price, tp2_price, sl_price, sl_type, status, step, auto_tp_sl, btc_price_entry, highest_price_seen
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SUGGESTED', 'ENTRY', 1, ?, ?)
    """,
        (
            symbol,
            exchange,
            price,
            amount_eur,
            tp1_price,
            tp1_price,
            tp2_price,
            sl_price,
            sl_type,
            btc_price_entry,
            price,
        ),
    )
    rec_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return rec_id


def mark_trade_taken(rec_id, real_entry_price=None, btc_entry_price=None):
    """Active une recommandation en position ouverte (enregistre le prix du BTC et initialise le High-Water Mark)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT price_suggested, btc_price_entry FROM speculative_trades WHERE id = ?",
        (rec_id,),
    )
    row = cursor.fetchone()

    if real_entry_price is None:
        real_entry_price = row[0] if row else 0.0

    if btc_entry_price is None or btc_entry_price <= 0.0:
        btc_entry_price = row[1] if (row and len(row) > 1 and row[1]) else 0.0

    cursor.execute(
        """
        UPDATE speculative_trades
        SET status = 'OPEN', price_entry = ?, taken_at = ?, step = 'ENTRY', auto_tp_sl = 1, btc_price_entry = ?, highest_price_seen = ?
        WHERE id = ?
    """,
        (real_entry_price, datetime.now(), btc_entry_price, real_entry_price, rec_id),
    )
    conn.commit()
    conn.close()


def update_highest_price_seen(rec_id, current_price):
    """Met à jour le sommet historique (High-Water Mark) si le prix actuel dépasse la valeur enregistrée."""
    if not current_price or current_price <= 0:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE speculative_trades
        SET highest_price_seen = MAX(COALESCE(highest_price_seen, 0.0), ?)
        WHERE id = ? AND status = 'OPEN'
    """,
        (current_price, rec_id),
    )
    conn.commit()
    conn.close()


def mark_trade_ignored(rec_id):
    """Marque une opportunité comme ignorée."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE speculative_trades SET status = 'IGNORED' WHERE id = ?", (rec_id,)
    )
    conn.commit()
    conn.close()


def archive_closed_trade(
    trade_id,
    symbol,
    exchange,
    entry_price,
    exit_price,
    amount_eur,
    opened_at=None,
    close_reason="MANUAL",
):
    """Calcule le PnL réalisé exact et enregistre le trade dans le journal d'historique (SQLite)."""
    entry_p = float(entry_price or 0.0)
    exit_p = float(exit_price or 0.0)
    amt_e = float(amount_eur or 0.0)

    pnl_pct = (((exit_p - entry_p) / entry_p) * 100.0) if entry_p > 0 else 0.0
    pnl_eur = amt_e * (pnl_pct / 100.0)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO closed_trades 
        (trade_id, symbol, exchange, entry_price, exit_price, amount_eur, pnl_eur, pnl_pct, opened_at, close_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            trade_id,
            symbol,
            exchange,
            entry_p,
            exit_p,
            amt_e,
            pnl_eur,
            pnl_pct,
            opened_at,
            close_reason,
        ),
    )
    conn.commit()
    conn.close()


def close_trade(rec_id, exit_price=None, close_reason="MANUAL"):
    """Marque un trade comme clôturé et l'archive automatiquement dans le journal de trading."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT symbol, exchange, price_entry, amount_eur, taken_at 
        FROM speculative_trades WHERE id = ?
    """,
        (rec_id,),
    )
    row = cursor.fetchone()

    if row:
        sym, exch, entry_p, amt_e, taken_at = row
        final_exit_p = exit_price if exit_price is not None else (entry_p or 0.0)
        archive_closed_trade(
            trade_id=rec_id,
            symbol=sym,
            exchange=exch,
            entry_price=entry_p,
            exit_price=final_exit_p,
            amount_eur=amt_e,
            opened_at=taken_at,
            close_reason=close_reason,
        )

    cursor.execute(
        """
        UPDATE speculative_trades
        SET status = 'CLOSED', step = 'CLOSED', closed_at = ?
        WHERE id = ?
    """,
        (datetime.now(), rec_id),
    )
    conn.commit()
    conn.close()


def get_closed_trades_history():
    """Récupère l'ensemble des trades clôturés pour les calculs de Win Rate et PnL cumulé."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, symbol, exchange, entry_price, exit_price, amount_eur, pnl_eur, pnl_pct, opened_at, closed_at, close_reason
        FROM closed_trades
        ORDER BY closed_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def update_alert_status(rec_id, new_alert_status):
    """Met à jour le statut d'alerte pour éviter les doublons de notification."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE speculative_trades
        SET alert_status = ?
        WHERE id = ?
    """,
        (new_alert_status, rec_id),
    )
    conn.commit()
    conn.close()


def update_trade_step(rec_id, new_step):
    """Met à jour l'étape du trade ('ENTRY', 'TP1_DONE', 'TP2_DONE', 'CLOSED')."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE speculative_trades
        SET step = ?
        WHERE id = ?
    """,
        (new_step, rec_id),
    )
    conn.commit()
    conn.close()


def update_trade_amount(rec_id, new_amount_eur):
    """Met à jour le montant engagé d'une position ouverte (synchronisation volume)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE speculative_trades
        SET amount_eur = ?
        WHERE id = ?
    """,
        (new_amount_eur, rec_id),
    )
    conn.commit()
    conn.close()


def update_trade_levels(rec_id, tp1_price=None, tp2_price=None, sl_price=None):
    """Met à jour dynamiquement les paliers TP1, TP2 et SL d'une position."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    fields = []
    params = []

    if tp1_price is not None:
        fields.append("tp1_price = ?")
        params.append(tp1_price)
    if tp2_price is not None:
        fields.append("tp2_price = ?")
        params.append(tp2_price)
    if sl_price is not None:
        fields.append("sl_price = ?")
        params.append(sl_price)

    if fields:
        params.append(rec_id)
        query = f"UPDATE speculative_trades SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        conn.commit()
    conn.close()


def set_trade_break_even(rec_id):
    """Passe le Stop-Loss d'une position exactement sur son prix d'entrée."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE speculative_trades
        SET sl_price = price_entry, alert_status = 'BE_SET'
        WHERE id = ?
    """,
        (rec_id,),
    )
    conn.commit()
    conn.close()


def merge_duplicate_trades(primary_id, secondary_id):
    """Consolide deux positions ouvertes du même symbole/exchange en une seule avec PRU, prix BTC et High-Water Mark pondérés."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT symbol, exchange, price_entry, amount_eur, tp1_price, tp2_price, sl_price, btc_price_entry, highest_price_seen
        FROM speculative_trades WHERE id = ?
    """,
        (primary_id,),
    )
    p1 = cursor.fetchone()

    cursor.execute(
        """
        SELECT symbol, exchange, price_entry, amount_eur, btc_price_entry, highest_price_seen
        FROM speculative_trades WHERE id = ?
    """,
        (secondary_id,),
    )
    p2 = cursor.fetchone()

    if not p1 or not p2:
        conn.close()
        return False

    sym1, exch1, entry1, amt1, tp1, tp2, sl1, btc_entry1, hwm1 = p1
    sym2, exch2, entry2, amt2, btc_entry2, hwm2 = p2

    qty1 = (amt1 / entry1) if entry1 > 0 else 0.0
    qty2 = (amt2 / entry2) if entry2 > 0 else 0.0

    total_qty = qty1 + qty2
    total_amt = amt1 + amt2
    weighted_entry = (total_amt / total_qty) if total_qty > 0 else entry1

    btc_e1 = float(btc_entry1 or 0.0)
    btc_e2 = float(btc_entry2 or 0.0)
    if total_amt > 0 and (btc_e1 > 0 or btc_e2 > 0):
        weighted_btc_entry = ((btc_e1 * amt1) + (btc_e2 * amt2)) / total_amt
    else:
        weighted_btc_entry = btc_e1 if btc_e1 > 0 else btc_e2

    hwm1_val = float(hwm1 or 0.0)
    hwm2_val = float(hwm2 or 0.0)
    max_hwm = max(hwm1_val, hwm2_val, weighted_entry)

    new_tp1 = (weighted_entry * 2.0) if (tp1 is None or tp1 <= 0) else tp1
    new_tp2 = (weighted_entry * 4.0) if (tp2 is None or tp2 <= 0) else tp2
    new_sl = (weighted_entry * 0.60) if (sl1 is None or sl1 <= 0) else sl1

    cursor.execute(
        """
        UPDATE speculative_trades
        SET price_entry = ?, amount_eur = ?, tp1_price = ?, tp2_price = ?, sl_price = ?, btc_price_entry = ?, highest_price_seen = ?
        WHERE id = ?
    """,
        (
            weighted_entry,
            total_amt,
            new_tp1,
            new_tp2,
            new_sl,
            weighted_btc_entry,
            max_hwm,
            primary_id,
        ),
    )

    cursor.execute(
        """
        UPDATE speculative_trades
        SET status = 'CLOSED', step = 'MERGED_INTO_' || ?, closed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """,
        (primary_id, secondary_id),
    )

    conn.commit()
    conn.close()
    return True


def mark_manual_trade_core(
    symbol, exchange, cost_eur, price_eur, reason="CORE"
):
    """Enregistre un trade manuel comme 'CORE' pour ne plus l'afficher en alerte spéculative."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ignored_manual_trades (symbol, exchange, cost_eur, price_eur, reason)
        VALUES (?, ?, ?, ?, ?)
    """,
        (
            str(symbol).upper().strip(),
            str(exchange).upper().strip(),
            cost_eur,
            price_eur,
            reason,
        ),
    )
    conn.commit()
    conn.close()


def is_manual_trade_ignored(symbol, exchange, cost_eur=None):
    """Vérifie si un trade manuel a été classé en CORE pour ce symbole et cet exchange."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    sym_clean = str(symbol).upper().strip()
    exch_clean = str(exchange).upper().strip()

    cursor.execute(
        """
        SELECT id FROM ignored_manual_trades
        WHERE UPPER(symbol) = ? AND UPPER(exchange) = ?
    """,
        (sym_clean, exch_clean),
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def get_open_positions():
    """Récupère toutes les positions ouvertes (14 colonnes, btc_price_entry en index 12, highest_price_seen en index 13)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, symbol, exchange, price_entry, amount_eur, tp1_price, tp2_price, sl_price, sl_type, step, taken_at, alert_status, btc_price_entry, highest_price_seen
        FROM speculative_trades
        WHERE status = 'OPEN'
        ORDER BY taken_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


if __name__ == "__main__":
    init_db()
    print(
        "✅ Base SQLite v3.8.0 (Moonbag, Cycle de Vie CORE, Mode Auto/IA, Benchmark BTC, High-Water Mark & Sentinelle Zombies) initialisée et vérifiée."
    )