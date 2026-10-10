import logging
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def calculate_pnl(trades):
    """Calcule le PnL réalisé, le Win Rate et le total des frais à partir d'une liste de trades.

    Args:
        trades (list): Liste de dicts normalisés [{pair, type, price, vol, cost,
          fee, ...}]

    Returns:
        dict: Statistiques de performance globales et par actif.
    """
    if not trades:
        return {
            "realized_pnl": 0.0,
            "total_fees": 0.0,
            "win_rate": 0.0,
            "total_closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "assets": {},
        }

    # Structure de suivi par paire / actif
    # positions[pair] = {"qty": float, "pru": float}
    positions = defaultdict(lambda: {"qty": 0.0, "pru": 0.0})

    total_realized_pnl = 0.0
    total_fees = 0.0
    winning_trades = 0
    losing_trades = 0

    # Analyse séquentielle chronologique
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
                # Recalcul du PRU moyen pondéré
                new_total_qty = pos["qty"] + vol
                if new_total_qty > 0:
                    pos["pru"] = (
                        (pos["qty"] * pos["pru"]) + (vol * price)
                    ) / new_total_qty
                    pos["qty"] = new_total_qty

        elif trade_type == "sell":
            if vol > 0 and price > 0:
                # Quantité réellement vendue prise en compte sur le stock
                sell_qty = min(vol, pos["qty"]) if pos["qty"] > 0 else vol

                # PnL réalisé sur cette vente
                trade_pnl = (sell_qty * (price - pos["pru"])) - fee
                total_realized_pnl += trade_pnl

                if trade_pnl > 0:
                    winning_trades += 1
                elif trade_pnl < 0:
                    losing_trades += 1

                # Mise à jour du stock restant
                pos["qty"] = max(0.0, pos["qty"] - sell_qty)
                if pos["qty"] == 0:
                    pos["pru"] = 0.0

    total_closed = winning_trades + losing_trades
    win_rate = (winning_trades / total_closed * 100) if total_closed > 0 else 0.0

    summary = {
        "realized_pnl": round(total_realized_pnl, 2),
        "total_fees": round(total_fees, 2),
        "win_rate": round(win_rate, 1),
        "total_closed_trades": total_closed,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "open_positions": {
            k: v for k, v in positions.items() if v["qty"] > 0
        },
    }

    return summary


def format_pnl_telegram_message(pnl_summary):
    """Génère un bloc de texte propre prêt pour le rapport Telegram /pnl."""
    pnl_val = pnl_summary["realized_pnl"]
    pnl_emoji = "🟢" if pnl_val >= 0 else "🔴"
    sign = "+" if pnl_val >= 0 else ""

    msg = (
        f"📊 *BILAN DE PERFORMANCE DE TRADING (v1.5)*\n"
        f"----------------------------------------\n"
        f"{pnl_emoji} *PnL Réalisé* : `{sign}{pnl_val:.2f} €`\n"
        f"🎯 *Win Rate* : `{pnl_summary['win_rate']}%` ({pnl_summary['winning_trades']}/{pnl_summary['total_closed_trades']} trades)\n"
        f"💸 *Frais API Total* : `{pnl_summary['total_fees']:.2f} €`\n"
        f"🟢 Trades Gagnants : `{pnl_summary['winning_trades']}`\n"
        f"🔴 Trades Perdants : `{pnl_summary['losing_trades']}`\n"
    )

    if pnl_summary.get("open_positions"):
        msg += "\n📦 *Positions Ouvertes (PRU)* :\n"
        for pair, info in pnl_summary["open_positions"].items():
            msg += (
                f"• `{pair}` : `{info['qty']:.4f}` @ PRU `{info['pru']:.4f}`\n"
            )

    return msg


if __name__ == "__main__":
    # Test unitaire autonome avec des données de simulation
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
        },  # PRU = 55000
        {
            "pair": "BTCUSDT",
            "type": "sell",
            "price": 65000.0,
            "vol": 0.1,
            "fee": 1.0,
            "time": 3,
        },  # Gain: 0.1*(65k-55k)-1 = +999
    ]

    res = calculate_pnl(mock_trades)
    print(format_pnl_telegram_message(res))
