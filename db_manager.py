from datetime import datetime
import os
import sqlite3

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")


def init_db():
  """Initialise la table des trades spéculatifs et applique les migrations v1.7."""
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
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
            alert_status TEXT DEFAULT 'NONE', -- NONE, TP_ALERTED, SL_ALERTED
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            taken_at TIMESTAMP,
            closed_at TIMESTAMP
        )
    """)

  # Check des colonnes existantes pour migrations douces
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

  # Migration v1.7 (Colonnes stratégie Moonbag)
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
  """Enregistre une recommandation v1.7 avec paliers TP1 et TP2."""
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


def get_open_positions():
  """Récupère toutes les positions ouvertes avec les paliers v1.7."""
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
  print("✅ Base SQLite v1.7 (Moonbag) initialisée et vérifiée.")


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
