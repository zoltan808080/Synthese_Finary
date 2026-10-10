import datetime
import logging
import os
import requests
from dotenv import load_dotenv

from db_manager import (
    get_open_positions,
    init_db,
    update_alert_status,
    update_trade_step,
)

load_dotenv()
init_db()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")


def get_usd_to_eur_rate():
  """Récupère le taux USD/USDC vers EUR en temps réel."""
  try:
    res = requests.get(
        "https://www.okx.com/api/v5/market/ticker?instId=USDC-EUR", timeout=3
    )
    if res.status_code == 200:
      data = res.json().get("data", [])
      if data:
        return float(data[0]["last"])
  except Exception:
    pass

  try:
    res = requests.get(
        "https://api.kraken.com/0/public/Ticker?pair=EURUSDT", timeout=3
    )
    if res.status_code == 200:
      result = res.json().get("result", {})
      if result:
        key = list(result.keys())[0]
        price = float(result[key]["c"][0])
        if price > 0:
          return 1.0 / price
  except Exception:
    pass

  return 0.883


def get_live_price(symbol, exchange):
  """Récupère le prix actuel du token converti en Euros."""
  symbol_upper = symbol.upper()
  exchange_upper = exchange.upper() if exchange else ""
  usd_eur_rate = get_usd_to_eur_rate()

  # 1. Kraken
  if "KRAKEN" in exchange_upper:
    for pair in [f"{symbol_upper}EUR", f"X{symbol_upper}ZEUR"]:
      try:
        res = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=3
        )
        if res.status_code == 200:
          result = res.json().get("result", {})
          if result:
            key = list(result.keys())[0]
            return float(result[key]["c"][0])
      except Exception:
        pass

    for pair in [f"{symbol_upper}USDT", f"{symbol_upper}USD"]:
      try:
        res = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=3
        )
        if res.status_code == 200:
          result = res.json().get("result", {})
          if result:
            key = list(result.keys())[0]
            return float(result[key]["c"][0]) * usd_eur_rate
      except Exception:
        pass

  # 2. OKX
  if "OKX" in exchange_upper:
    try:
      res = requests.get(
          f"https://www.okx.com/api/v5/market/ticker?instId={symbol_upper}-EUR",
          timeout=3,
      )
      if res.status_code == 200:
        data = res.json().get("data", [])
        if data:
          return float(data[0]["last"])
    except Exception:
      pass

    for inst_id in [f"{symbol_upper}-USDC", f"{symbol_upper}-USDT"]:
      try:
        res = requests.get(
            f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
            timeout=3,
        )
        if res.status_code == 200:
          data = res.json().get("data", [])
          if data:
            return float(data[0]["last"]) * usd_eur_rate
      except Exception:
        pass

  # 3. Fallback CoinGecko
  try:
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=eur"
    res = requests.get(url, timeout=5)
    if res.status_code == 200:
      data = res.json()
      if symbol.lower() in data:
        return float(data[symbol.lower()]["eur"])
  except Exception:
    pass

  return None


def send_alert_telegram(text, rec_id):
  """Envoie une alerte Telegram urgente avec bouton de clôture BDD."""
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  inline_keyboard = [[{
      "text": "🔒 Clôturer la position en BDD",
      "callback_data": f"trade_close_{rec_id}",
  }]]

  payload = {
      "chat_id": ALLOWED_USER_ID,
      "text": text,
      "parse_mode": "Markdown",
      "reply_markup": {"inline_keyboard": inline_keyboard},
  }

  try:
    res = requests.post(url, json=payload, timeout=10)
    if res.status_code != 200:
      payload_plain = {
          "chat_id": ALLOWED_USER_ID,
          "text": text,
          "reply_markup": {"inline_keyboard": inline_keyboard},
      }
      requests.post(url, json=payload_plain, timeout=10)
  except Exception as e:
    logging.error(f"❌ Erreur envoi alerte Telegram : {e}")


def monitor_positions():
  """Surveillance 24/7 : TP1, TP2, Stop-Loss et Time-Stop 21 jours."""
  logging.info("🔍 Surveillance 24/7 des positions ouvertes (v1.8.5)...")
  open_trades = get_open_positions()

  if not open_trades:
    logging.info("Aucune position ouverte à surveiller.")
    return

  now = datetime.datetime.now()

  for trade in open_trades:
    (
        rec_id,
        symbol,
        exchange,
        price_entry,
        amount_eur,
        tp1_price,
        tp2_price,
        sl_price,
        sl_type,
        step,
        taken_at,
        alert_status,
    ) = trade

    current_price = get_live_price(symbol, exchange)
    if not current_price or price_entry <= 0:
      logging.warning(
          f"⚠️ Impossible d'obtenir le cours en direct pour {symbol} ({exchange})"
      )
      continue

    pnl_pct = ((current_price - price_entry) / price_entry) * 100.0
    logging.info(
        f"📈 {symbol} ({exchange}) : Entrée = {price_entry:.4f} € | Actuel ="
        f" {current_price:.4f} € | PnL = {pnl_pct:+.2f}% | Étape = {step}"
    )

    # 1. Take-Profit 1 (+100% / x2) -> Free Ride
    if step == "ENTRY" and tp1_price > 0 and current_price >= tp1_price:
      msg = (
          "🚀 *ALERTE MOONBAG : TP1 ATTEINT !*\n\n"
          f"Le token *{symbol}* ({exchange}) a fait x2 !\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Objectif TP1 (x2) : {tp1_price:.4f} €\n\n"
          "💡 *Actions conseillées* :\n"
          "1. Vendre **50% de la position** pour récupérer le capital initial"
          " (Free Ride).\n"
          "2. Remonter le Stop-Loss au prix d'entrée (*"
          f"{price_entry:.4f} €*) : **Break-Even**."
      )
      send_alert_telegram(msg, rec_id)
      update_trade_step(rec_id, "TP1_DONE")
      logging.info(f"✅ Alerte TP1 envoyée pour {symbol}")

    # 2. Take-Profit 2 (+300% / x4) -> Verrouillage gains
    elif step == "TP1_DONE" and tp2_price > 0 and current_price >= tp2_price:
      msg = (
          "🎯 *ALERTE MOONBAG : TP2 ATTEINT !*\n\n"
          f"Le token *{symbol}* ({exchange}) a atteint l'objectif x4 !\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Objectif TP2 (x4) : {tp2_price:.4f} €\n\n"
          "💡 *Actions conseillées* :\n"
          "1. Vendre **25% supplémentaires** pour sécuriser des gains.\n"
          "2. Conserver les **25% restants en Moonbag** pour viser plus haut."
      )
      send_alert_telegram(msg, rec_id)
      update_trade_step(rec_id, "TP2_DONE")
      logging.info(f"✅ Alerte TP2 envoyée pour {symbol}")

    # 3. Stop-Loss / Invalidation
    elif (
        sl_price > 0
        and current_price <= sl_price
        and alert_status != "SL_ALERTED"
    ):
      msg = (
          "🛑 *ALERTE STOP-LOSS / INVALIDATION*\n\n"
          f"Le token *{symbol}* ({exchange}) a franchi son niveau"
          f" d'invalidation ({sl_type}) !\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Seuil Invalidation : {sl_price:.4f} €\n\n"
          "💡 *Action conseillée* : Couper la position immédiatement."
      )
      send_alert_telegram(msg, rec_id)
      update_alert_status(rec_id, "SL_ALERTED")
      logging.info(f"🚨 Alerte SL envoyée pour {symbol}")

    # 4. Time-Stop Automatique (Stagnation > 21 jours entre -5% et +5%)
    elif taken_at and alert_status not in ["TIMESTOP_ALERTED", "SL_ALERTED"]:
      trade_date = None
      try:
        trade_date = datetime.datetime.fromisoformat(str(taken_at))
      except Exception:
        try:
          trade_date = datetime.datetime.strptime(
              str(taken_at), "%Y-%m-%d %H:%M:%S"
          )
        except Exception:
          pass

      if trade_date:
        days_open = (now - trade_date).days
        if days_open >= 21 and abs(pnl_pct) <= 5.0:
          msg = (
              "⏱️ *ALERTE TIME-STOP (21 JOURS)*\n\n"
              f"Le token *{symbol}* ({exchange}) stagne depuis **{days_open}"
              " jours**.\n"
              f"• Prix d'entrée : {price_entry:.4f} €\n"
              f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n\n"
              "💡 *Action conseillée* : Arbitrer ou clôturer la position pour"
              " réallouer la liquidité sur une opportunité plus dynamique."
          )
          send_alert_telegram(msg, rec_id)
          update_alert_status(rec_id, "TIMESTOP_ALERTED")
          logging.info(f"⏱️ Alerte Time-Stop envoyée pour {symbol}")


if __name__ == "__main__":
  monitor_positions()
