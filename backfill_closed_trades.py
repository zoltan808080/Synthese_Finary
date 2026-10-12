import sqlite3
from db_manager import DB_PATH, archive_closed_trade, init_db


def backfill_from_sqlite():
  """Rattrape les trades fermés dans speculative_trades et les archive dans closed_trades."""
  init_db()
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()

  # 1. Récupération des trades fermés dans la table des recommandations / moonbags
  cursor.execute("""
        SELECT id, symbol, exchange, price_entry, amount_eur, created_at, taken_at, closed_at
        FROM speculative_trades
        WHERE status = 'CLOSED'
    """)
  closed_speculative = cursor.fetchall()

  # 2. Récupération des trade_id déjà présents pour éviter tout doublon
  cursor.execute(
      "SELECT trade_id FROM closed_trades WHERE trade_id IS NOT NULL"
  )
  archived_ids = {row[0] for row in cursor.fetchall() if row[0] is not None}

  added_count = 0
  for row in closed_speculative:
    trade_id, symbol, exchange, entry_price, amount_eur, created_at, taken_at, closed_at = (
        row
    )

    if trade_id in archived_ids:
      continue

    entry_p = float(entry_price or 0.0)
    amt_e = float(amount_eur or 0.0)
    opened_date = taken_at or created_at

    # Si le prix de sortie n'est pas renseigné, on se cale par défaut sur le prix d'entrée
    exit_p = entry_p
    try:
      from crypto_monitor import get_live_price

      live_p = get_live_price(symbol, exchange)
      if live_p:
        exit_p = live_p
    except Exception:
      pass

    archive_closed_trade(
        trade_id=trade_id,
        symbol=symbol,
        exchange=exchange,
        entry_price=entry_p,
        exit_price=exit_p,
        amount_eur=amt_e,
        opened_at=opened_date,
        close_reason="RATTRAPAGE_RETROACTIF",
    )
    added_count += 1

  conn.close()
  print(
      f"✅ Rattrapage terminé : {added_count} trade(s) rétroactif(s) archivé(s)"
      " dans closed_trades."
  )


if __name__ == "__main__":
  backfill_from_sqlite()
