#!/usr/bin/env python3
"""Sentinelle 24/7 et Moteur d'Alertes Telegram Intelligentes v3.5.0 (Conseil Flash IA Gemini SSOT, Alpha vs BTC & High-Water Mark)."""

import datetime
import logging
import os
import requests
from dotenv import load_dotenv

from db_manager import (
    get_open_positions,
    init_db,
    update_alert_status,
    update_highest_price_seen,
    update_trade_step,
)
from gemini_manager import call_gemini_with_fallback
from prompt_loader import get_system_prompt

load_dotenv()
init_db()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# Table de correspondance Symbol -> ID CoinGecko
COINGECKO_MAP = {
    "kas": "kaspa",
    "hype": "hyperliquid",
    "ath": "aethir",
    "fet": "artificial-superintelligence-alliance",
    "ondo": "ondo-finance",
    "ray": "raydium",
}


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
  symbol_lower = symbol.lower()
  exchange_upper = exchange.upper() if exchange else ""
  usd_eur_rate = get_usd_to_eur_rate()
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
          " Gecko) Chrome/124.0.0.0 Safari/537.36"
      )
  }

  # 1. Kraken
  if "KRAKEN" in exchange_upper:
    for pair in [f"{symbol_upper}EUR", f"X{symbol_upper}ZEUR"]:
      try:
        res = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair}",
            headers=headers,
            timeout=3,
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
            f"https://api.kraken.com/0/public/Ticker?pair={pair}",
            headers=headers,
            timeout=3,
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
          headers=headers,
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
            headers=headers,
            timeout=3,
        )
        if res.status_code == 200:
          data = res.json().get("data", [])
          if data:
            return float(data[0]["last"]) * usd_eur_rate
      except Exception:
        pass

  # 3. Fallback CoinGecko (avec mapping ID)
  cg_ids = [COINGECKO_MAP.get(symbol_lower, symbol_lower), symbol_lower]
  for cg_id in dict.fromkeys(cg_ids):
    try:
      url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=eur"
      res = requests.get(url, headers=headers, timeout=4)
      if res.status_code == 200:
        data = res.json()
        if cg_id in data and "eur" in data[cg_id]:
          return float(data[cg_id]["eur"])
    except Exception:
      pass

  return None


def get_flash_ai_advice(symbol, exchange, price_entry, current_price, pnl_pct, alpha_vs_btc_pct, event_type, sl_price=0.0):
  """Sollicite Gemini en mode Flash via le fichier SSOT (prompts/flash_advice.md)."""
  system_prompt = get_system_prompt("flash_advice")

  user_payload = f"""SITUATION DE L'ALERTE :
- Actif : {symbol} ({exchange})
- Prix d'entrée : {price_entry:.4f} € | Prix actuel : {current_price:.4f} €
- PnL latent : {pnl_pct:+.2f}% | Alpha vs BTC : {alpha_vs_btc_pct:+.2f}%
- Stop-Loss actif : {sl_price:.4f} €
- Événement déclencheur : {event_type}"""

  try:
    response_text, used_model = call_gemini_with_fallback(
        prompt_text=user_payload,
        system_instruction=system_prompt,
        json_mode=False,
        custom_timeout=15
    )
    return response_text.strip()
  except Exception as e:
    logging.warning(f"⚠️ Erreur lors de la génération du conseil flash IA : {e}")
    return "Consulter le dashboard Streamlit pour arbitrer la position."


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
  """Surveillance 24/7 v3.5.0 : TP1, TP2, Stop-Loss, Décrochage d'Alpha vs BTC et Time-Stop 21j avec conseils flash IA SSOT."""
  logging.info("🔍 Surveillance 24/7 des positions ouvertes (v3.5.0 SSOT)...")
  open_trades = get_open_positions()

  if not open_trades:
    logging.info("Aucune position ouverte à surveiller.")
    return

  btc_live_price = get_live_price("BTC", "KRAKEN") or get_live_price("BTC", "OKX") or 1.0
  now = datetime.datetime.now()

  for trade in open_trades:
    # Dépaquetage sécurisé des 14 champs (v3.5.0)
    rec_id = trade[0]
    symbol = trade[1]
    exchange = trade[2]
    price_entry = float(trade[3]) if trade[3] is not None else 0.0
    amount_eur = float(trade[4]) if trade[4] is not None else 0.0
    tp1_price = float(trade[5]) if trade[5] is not None else 0.0
    tp2_price = float(trade[6]) if trade[6] is not None else 0.0
    sl_price = float(trade[7]) if trade[7] is not None else 0.0
    sl_type = trade[8] if trade[8] else "invalidation"
    step = trade[9] if trade[9] else "ENTRY"
    taken_at = trade[10]
    alert_status = trade[11] if trade[11] else "NONE"
    btc_entry = float(trade[12]) if (len(trade) > 12 and trade[12] and float(trade[12]) > 0) else btc_live_price

    current_price = get_live_price(symbol, exchange)
    if not current_price or price_entry <= 0:
      logging.warning(
          f"⚠️ Impossible d'obtenir le cours en direct pour {symbol} ({exchange})"
      )
      continue

    # Enregistrement atomique du sommet local (High-Water Mark v3.5)
    update_highest_price_seen(rec_id, current_price)

    pnl_pct = ((current_price - price_entry) / price_entry) * 100.0
    btc_pnl_pct = (((btc_live_price - btc_entry) / btc_entry) * 100.0) if btc_entry > 0 else 0.0
    alpha_vs_btc_pct = pnl_pct - btc_pnl_pct

    logging.info(
        f"📈 {symbol} ({exchange}) : Entrée = {price_entry:.4f} € | Actuel ="
        f" {current_price:.4f} € | PnL = {pnl_pct:+.2f}% | Alpha vs BTC = {alpha_vs_btc_pct:+.2f}% | Étape = {step}"
    )

    # 1. Take-Profit 1 (+100% / x2) -> Free Ride
    if step == "ENTRY" and tp1_price > 0 and current_price >= tp1_price:
      ai_advice = get_flash_ai_advice(symbol, exchange, price_entry, current_price, pnl_pct, alpha_vs_btc_pct, "ATTEINTE DE TP1 (x2)", tp1_price)
      msg = (
          "🚀 *ALERTE MOONBAG : TP1 ATTEINT !*\n\n"
          f"Le token *{symbol}* ({exchange}) a fait x2 !\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Alpha vs BTC : {alpha_vs_btc_pct:+.2f}%\n"
          f"• Objectif TP1 (x2) : {tp1_price:.4f} €\n\n"
          f"🤖 *CONSEIL TACTIQUE IA* :\n{ai_advice}"
      )
      send_alert_telegram(msg, rec_id)
      update_trade_step(rec_id, "TP1_DONE")
      logging.info(f"✅ Alerte TP1 envoyée pour {symbol}")

    # 2. Take-Profit 2 (+300% / x4) -> Verrouillage gains
    elif step == "TP1_DONE" and tp2_price > 0 and current_price >= tp2_price:
      ai_advice = get_flash_ai_advice(symbol, exchange, price_entry, current_price, pnl_pct, alpha_vs_btc_pct, "ATTEINTE DE TP2 (x4)", tp2_price)
      msg = (
          "🎯 *ALERTE MOONBAG : TP2 ATTEINT !*\n\n"
          f"Le token *{symbol}* ({exchange}) a atteint l'objectif x4 !\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Alpha vs BTC : {alpha_vs_btc_pct:+.2f}%\n"
          f"• Objectif TP2 (x4) : {tp2_price:.4f} €\n\n"
          f"🤖 *CONSEIL TACTIQUE IA* :\n{ai_advice}"
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
      ai_advice = get_flash_ai_advice(symbol, exchange, price_entry, current_price, pnl_pct, alpha_vs_btc_pct, "RUPTURE STOP-LOSS", sl_price)
      msg = (
          "🛑 *ALERTE STOP-LOSS / INVALIDATION*\n\n"
          f"Le token *{symbol}* ({exchange}) a franchi son niveau"
          f" d'invalidation ({sl_type}) !\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Alpha vs BTC : {alpha_vs_btc_pct:+.2f}%\n"
          f"• Seuil Invalidation : {sl_price:.4f} €\n\n"
          f"🤖 *CONSEIL TACTIQUE IA* :\n{ai_advice}"
      )
      send_alert_telegram(msg, rec_id)
      update_alert_status(rec_id, "SL_ALERTED")
      logging.info(f"🚨 Alerte SL envoyée pour {symbol}")

    # 4. Alerte Décrochage d'Alpha vs BTC (Sous-performance <= -5.0%)
    elif (
        alpha_vs_btc_pct <= -5.0
        and alert_status not in ["ALPHA_ALERTED", "SL_ALERTED"]
    ):
      ai_advice = get_flash_ai_advice(symbol, exchange, price_entry, current_price, pnl_pct, alpha_vs_btc_pct, "DÉCROCHAGE D'ALPHA VS BTC", sl_price)
      msg = (
          "⚠️ *ALERTE DÉCROCHAGE D'ALPHA VS BTC*\n\n"
          f"Le token *{symbol}* ({exchange}) accuse un coût d'opportunité sévère face au Bitcoin !\n"
          f"• Alpha vs BTC : *{alpha_vs_btc_pct:+.2f}%* (Seuil : -5.0%)\n"
          f"• Prix d'entrée : {price_entry:.4f} €\n"
          f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
          f"• Stop-Loss Actuel : {sl_price:.4f} €\n\n"
          f"🤖 *CONSEIL TACTIQUE IA* :\n{ai_advice}"
      )
      send_alert_telegram(msg, rec_id)
      update_alert_status(rec_id, "ALPHA_ALERTED")
      logging.info(f"⚠️ Alerte Décrochage Alpha envoyée pour {symbol}")

    # 5. Time-Stop Automatique (Stagnation > 21 jours entre -5% et +5%)
    elif taken_at and alert_status not in ["TIMESTOP_ALERTED", "SL_ALERTED", "ALPHA_ALERTED"]:
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
          ai_advice = get_flash_ai_advice(symbol, exchange, price_entry, current_price, pnl_pct, alpha_vs_btc_pct, "TIME-STOP 21 JOURS", sl_price)
          msg = (
              "⏱️ *ALERTE TIME-STOP (21 JOURS)*\n\n"
              f"Le token *{symbol}* ({exchange}) stagne depuis **{days_open}"
              " jours**.\n"
              f"• Prix d'entrée : {price_entry:.4f} €\n"
              f"• Prix actuel : *{current_price:.4f} €* ({pnl_pct:+.2f}%)\n"
              f"• Alpha vs BTC : {alpha_vs_btc_pct:+.2f}%\n\n"
              f"🤖 *CONSEIL TACTIQUE IA* :\n{ai_advice}"
          )
          send_alert_telegram(msg, rec_id)
          update_alert_status(rec_id, "TIMESTOP_ALERTED")
          logging.info(f"⏱️ Alerte Time-Stop envoyée pour {symbol}")


if __name__ == "__main__":
  monitor_positions()