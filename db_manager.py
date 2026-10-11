from datetime import datetime
import os
import sqlite3

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")


def init_db():
  """Initialise les tables SQLite et applique les migrations."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()

  # Table des positions spéculatives (Moonbag)
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
        "ALTER TABLE speculative_trades ADD COLUMN alert_status TEXT DEFAULT"
        " 'NONE'"
    )
  if "closed_at" not in columns:
    cursor.execute(
        "ALTER TABLE speculative_trades ADD COLUMN closed_at TIMESTAMP"
    )
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
        "ALTER TABLE speculative_trades ADD COLUMN sl_type TEXT DEFAULT"
        " 'invalidation'"
    )
  if "step" not in columns:
    cursor.execute(
        "ALTER TABLE speculative_trades ADD COLUMN step TEXT DEFAULT 'ENTRY'"
    )

  # Table des trades manuels ignorés ou classés CORE
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

  conn.commit()
  conn.close()


def add_recommendation(
    symbol,
    exchange,
    price,
    amount_eur,
    tp1_price,
    tp2_price,
    sl_price,
    sl_type="invalidation",
):
  """Enregistre une recommandation avec paliers TP1 et TP2."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO speculative_trades (
            symbol, exchange, price_suggested, amount_eur, 
            tp_price, tp1_price, tp2_price, sl_price, sl_type, status, step
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SUGGESTED', 'ENTRY')
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
      ),
  )
  rec_id = cursor.lastrowid
  conn.commit()
  conn.close()
  return rec_id


def mark_trade_taken(rec_id, real_entry_price=None):
  """Active une recommandation en position ouverte."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
  if real_entry_price is None:
    cursor.execute(
        "SELECT price_suggested FROM speculative_trades WHERE id = ?", (rec_id,)
    )
    row = cursor.fetchone()
    real_entry_price = row[0] if row else 0.0

  cursor.execute(
      """
        UPDATE speculative_trades
        SET status = 'OPEN', price_entry = ?, taken_at = ?, step = 'ENTRY'
        WHERE id = ?
    """,
      (real_entry_price, datetime.now(), rec_id),
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


def close_trade(rec_id):
  """Marque un trade comme clôturé."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
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
  """Consolide deux positions ouvertes du même symbole/exchange en une seule avec PRU pondéré."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()

  cursor.execute(
      """
        SELECT symbol, exchange, price_entry, amount_eur, tp1_price, tp2_price, sl_price
        FROM speculative_trades WHERE id = ?
    """,
      (primary_id,),
  )
  p1 = cursor.fetchone()

  cursor.execute(
      """
        SELECT symbol, exchange, price_entry, amount_eur
        FROM speculative_trades WHERE id = ?
    """,
      (secondary_id,),
  )
  p2 = cursor.fetchone()

  if not p1 or not p2:
    conn.close()
    return False

  sym1, exch1, entry1, amt1, tp1, tp2, sl1 = p1
  sym2, exch2, entry2, amt2 = p2

  qty1 = (amt1 / entry1) if entry1 > 0 else 0.0
  qty2 = (amt2 / entry2) if entry2 > 0 else 0.0

  total_qty = qty1 + qty2
  total_amt = amt1 + amt2
  weighted_entry = (total_amt / total_qty) if total_qty > 0 else entry1

  new_tp1 = (weighted_entry * 2.0) if (tp1 is None or tp1 <= 0) else tp1
  new_tp2 = (weighted_entry * 4.0) if (tp2 is None or tp2 <= 0) else tp2
  new_sl = (weighted_entry * 0.60) if (sl1 is None or sl1 <= 0) else sl1

  cursor.execute(
      """
        UPDATE speculative_trades
        SET price_entry = ?, amount_eur = ?, tp1_price = ?, tp2_price = ?, sl_price = ?
        WHERE id = ?
    """,
      (weighted_entry, total_amt, new_tp1, new_tp2, new_sl, primary_id),
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
  """Récupère toutes les positions ouvertes."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
  cursor.execute("""
        SELECT id, symbol, exchange, price_entry, amount_eur, tp1_price, tp2_price, sl_price, sl_type, step, taken_at, alert_status
        FROM speculative_trades
        WHERE status = 'OPEN'
        ORDER BY taken_at DESC
    """)
  rows = cursor.fetchall()
  conn.close()
  return rows


if __name__ == "__main__":
  init_db()
  print("✅ Base SQLite v2.8 (Moonbag & Core) initialisée et vérifiée.")
