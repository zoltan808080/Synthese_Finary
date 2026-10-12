from collections import defaultdict
import logging

try:
  from db_manager import get_closed_trades_history

  DB_AVAILABLE = True
except ImportError:
  DB_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def calculate_pnl(trades=None):
  """Calcule le PnL réalisé, le Win Rate, les frais API et suit les positions ouvertes.

  Garantit une rétrocompatibilité 100% avec /audit (open_positions) et le
  journal BDD.
  """
  positions = defaultdict(lambda: {"qty": 0.0, "pru": 0.0})

  total_realized_pnl = 0.0
  total_fees = 0.0
  winning_trades = 0
  losing_trades = 0
  gross_gains = 0.0
  gross_losses = 0.0

  # 1. Traitement chronologique des trades API (Exchange history)
  if trades:
    for t in trades:
      pair = t.get("pair", "UNKNOWN")
      trade_type = t.get("type", "").lower()
      price = float(t.get("price", 0))
      vol = float(t.get("vol", 0))
      fee = float(t.get("fee", 0))

      total_fees += fee
      pos = positions[pair]

      if trade_type == "buy":
        if vol > 0 and price > 0:
          new_total_qty = pos["qty"] + vol
          if new_total_qty > 0:
            pos["pru"] = ((pos["qty"] * pos["pru"]) +
                          (vol * price)) / new_total_qty
            pos["qty"] = new_total_qty

      elif trade_type == "sell":
        if vol > 0 and price > 0:
          sell_qty = min(vol, pos["qty"]) if pos["qty"] > 0 else vol
          trade_pnl = (sell_qty * (price - pos["pru"])) - fee
          total_realized_pnl += trade_pnl

          if trade_pnl > 0:
            winning_trades += 1
            gross_gains += trade_pnl
          elif trade_pnl < 0:
            losing_trades += 1
            gross_losses += abs(trade_pnl)

          pos["qty"] = max(0.0, pos["qty"] - sell_qty)
          if pos["qty"] == 0:
            pos["pru"] = 0.0

  # 2. Récupération des positions clôturées en BDD (closed_trades)
  recent_closed = []
  if DB_AVAILABLE:
    try:
      closed_db = get_closed_trades_history()
      for t in closed_db:
        # Structure tuple: (id, symbol, exchange, entry_price, exit_price, amount_eur, pnl_eur, pnl_pct, opened_at, closed_at, close_reason)
        sym = t[1]
        exch = t[2]
        amt = float(t[5] or 0.0)
        pnl_e = float(t[6] or 0.0)
        pnl_p = float(t[7] or 0.0)
        closed_at = str(t[9])[:10] if t[9] else "N/A"

        recent_closed.append({
            "symbol": sym,
            "exchange": exch,
            "amount": amt,
            "pnl_eur": pnl_e,
            "pnl_pct": pnl_p,
            "date": closed_at,
        })

        # Si pas de liste de trades API fournie, on accumule le PnL de la BDD
        if not trades:
          total_realized_pnl += pnl_e
          if pnl_e > 0:
            winning_trades += 1
            gross_gains += pnl_e
          elif pnl_e < 0:
            losing_trades += 1
            gross_losses += abs(pnl_e)
    except Exception as e:
      logging.warning(
          f"Impossible de lire l'historique closed_trades BDD : {e}"
      )

  total_closed = winning_trades + losing_trades
  win_rate = (winning_trades / total_closed * 100.0) if total_closed > 0 else 0.0
  profit_factor = (
      (gross_gains / gross_losses)
      if gross_losses > 0
      else (gross_gains if gross_gains > 0 else 1.0)
  )

  summary = {
      # Clés d'origine (v1.5)
      "realized_pnl": round(total_realized_pnl, 2),
      "total_fees": round(total_fees, 2),
      "win_rate": round(win_rate, 1),
      "total_closed_trades": total_closed,
      "winning_trades": winning_trades,
      "losing_trades": losing_trades,
      "open_positions": {k: v for k, v in positions.items() if v["qty"] > 0},
      # Extensions v3.0
      "total_pnl_eur": round(total_realized_pnl, 2),
      "profit_factor": round(profit_factor, 2),
      "recent_closed_trades": recent_closed,
  }

  return summary


def format_pnl_telegram_message(pnl_summary):
  """Génère un bloc de texte propre prêt pour le rapport Telegram /pnl."""
  pnl_val = pnl_summary.get(
      "realized_pnl", pnl_summary.get("total_pnl_eur", 0.0)
  )
  pnl_emoji = "🟢" if pnl_val >= 0 else "🔴"
  sign = "+" if pnl_val >= 0 else ""

  wr = pnl_summary.get("win_rate", 0.0)
  total_closed = pnl_summary.get("total_closed_trades", 0)
  wins = pnl_summary.get("winning_trades", 0)
  losses = pnl_summary.get("losing_trades", 0)
  fees = pnl_summary.get("total_fees", 0.0)
  pf = pnl_summary.get("profit_factor", 1.0)

  msg = (
      "📈 *JOURNAL DE TRADING & PNL RÉALISÉ (v3.0 FIFO)*\n"
      "----------------------------------------\n"
      f"{pnl_emoji} *PnL Réalisé Cumulé* : `{sign}{pnl_val:,.2f} €`\n"
      f"🎯 *Win Rate* : `{wr:.1f}%` ({wins}W / {losses}L sur {total_closed}"
      " trades)\n"
      f"📊 *Profit Factor* : `{pf:.2f}`\n"
      f"💸 *Frais API Total* : `{fees:,.2f} €`\n"
  )

  recent_closed = pnl_summary.get("recent_closed_trades", [])
  if recent_closed:
    msg += "\n📋 *Dernières positions clôturées (BDD)* :\n"
    for t in recent_closed[:5]:
      t_icon = "✅" if t["pnl_eur"] >= 0 else "❌"
      msg += (
          f"• {t_icon} *{t['symbol']}* ({t['exchange']}) : `{t['pnl_eur']:+,.2f}"
          f" €` (`{t['pnl_pct']:+.2f}%`) — _{t['date']}_\n"
      )

  open_pos = pnl_summary.get("open_positions")
  if open_pos:
    msg += "\n📦 *Positions Ouvertes (PRU API)* :\n"
    for pair, info in open_pos.items():
      msg += f"• `{pair}` : `{info['qty']:.4f}` @ PRU `{info['pru']:.4f}`\n"

  return msg


if __name__ == "__main__":
  mock_trades = [
      {
          "pair": "BTCUSDT",
          "type": "buy",
          "price": 50000.0,
          "vol": 0.1,
          "fee": 1.0,
          "time": 1,
      },
      {
          "pair": "BTCUSDT",
          "type": "buy",
          "price": 60000.0,
          "vol": 0.1,
          "fee": 1.0,
          "time": 2,
      },
      {
          "pair": "BTCUSDT",
          "type": "sell",
          "price": 65000.0,
          "vol": 0.1,
          "fee": 1.0,
          "time": 3,
      },
  ]
  res = calculate_pnl(mock_trades)
  print(format_pnl_telegram_message(res))
