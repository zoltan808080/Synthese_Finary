import logging
import os
import time
import requests
from dotenv import load_dotenv
from db_manager import get_open_positions
from exchange_connectors import (
    get_kraken_crypto_balances,
    get_okx_crypto_balances,
    get_mexc_crypto_balances,
    get_all_trades_history,
)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")


def send_telegram_alert(text):
  """Envoie une alerte directe sur Telegram si le bot est configuré."""
  if not BOT_TOKEN or not ALLOWED_USER_ID:
    return
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  payload = {"chat_id": ALLOWED_USER_ID, "text": text, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    logging.error(f"❌ Erreur envoi Telegram alerte : {e}")


def clean_pair_symbol(pair_str):
  """Nettoie proprement les paires complexes (ex: XXBTZEUR -> BTC, XETHZEUR -> ETH, ADAEUR -> ADA)."""
  clean = str(pair_str).upper().strip()
  for suffix in ["ZEUR", "ZUSD", "EUR", "USD", "USDT", "USDC"]:
    if clean.endswith(suffix) and len(clean) > len(suffix):
      clean = clean[:-len(suffix)]
  
  if clean in ["XXBT", "XBT", "XXBTZ", "XBTZ"]:
    return "BTC"
  if clean in ["XETH", "XETHZ"]:
    return "ETH"
  if len(clean) == 4 and clean.startswith("X") and clean not in ["XRP", "XLM", "XMR"]:
    clean = clean[1:]
  return clean.replace("-", "").replace("_", "").replace("/", "").strip()


def audit_database_vs_exchanges():
  """Compare les positions 'OPEN' en BDD avec les avoirs réels et détecte les achats manuels RÉCENTS (< 48h)."""
  open_positions = get_open_positions()

  kraken_balances = get_kraken_crypto_balances()
  okx_balances = get_okx_crypto_balances()
  mexc_balances = get_mexc_crypto_balances()

  discrepancies = []
  db_symbols = set()

  # 1. Contrôle des positions ouvertes en BDD vs Réalité par exchange
  for p in open_positions:
    pos_id = p[0]
    sym = clean_pair_symbol(p[1])
    exch = str(p[2]).upper().strip()
    db_amount_eur = float(p[4]) if p[4] is not None else 0.0
    entry_price = float(p[3]) if p[3] is not None else 0.0

    db_symbols.add(sym)
    db_qty = (db_amount_eur / entry_price) if entry_price > 0 else 0.0

    target_exchange_dict = {}
    if "KRAKEN" in exch:
      target_exchange_dict = kraken_balances
    elif "OKX" in exch:
      target_exchange_dict = okx_balances
    elif "MEXC" in exch:
      target_exchange_dict = mexc_balances

    real_asset_info = target_exchange_dict.get(sym, {})
    real_qty = float(real_asset_info.get("amount", -1.0))

    if real_qty == 0.0 or (real_qty == -1.0 and sym not in ["BTC", "ETH"]):
      discrepancies.append({
          "type": "GHOST_POSITION",
          "id": pos_id,
          "symbol": sym,
          "exchange": exch,
          "db_amount": db_amount_eur,
          "real_amount": 0.0,
          "desc": f"Ligne fantôme détectée : {sym} ({exch}) est actif en BDD ({db_amount_eur:,.2f} €) mais le solde réel sur {exch} est de 0.",
      })
    elif real_qty > 0.0 and db_qty > 0.0:
      diff_ratio = abs(db_qty - real_qty) / real_qty
      if diff_ratio > 0.30:
        adjusted_amount = real_qty * entry_price
        discrepancies.append({
            "type": "VOLUME_MISMATCH",
            "id": pos_id,
            "symbol": sym,
            "exchange": exch,
            "db_amount": db_amount_eur,
            "real_qty": real_qty,
            "adjusted_amount_eur": adjusted_amount,
            "desc": f"Écart de volume sur {sym} ({exch}) : BDD = {db_qty:,.2f} tokens vs Réel {exch} = {real_qty:,.2f} tokens ({diff_ratio*100:.1f}% d'écart).",
        })

  # 2. Détection des nouveaux trades manuels RÉCENTS (< 48 heures uniquement)
  try:
    recent_trades = get_all_trades_history()
    now_ts = time.time()
    cutoff_48h = now_ts - (48 * 3600)  # Seuls les trades des dernières 48h
    seen_manual_symbols = set()

    for trade in recent_trades:
      trade_time = float(trade.get("time", 0.0))
      
      # Ignorer les trades de plus de 48h
      if trade_time < cutoff_48h:
        continue

      tx_type = str(trade.get("type", "")).lower()
      pair = str(trade.get("pair", ""))
      exch = trade.get("exchange", "Unknown")
      price = float(trade.get("price", 0.0))
      cost = float(trade.get("cost", 0.0))

      clean_sym = clean_pair_symbol(pair)

      # Ignorer Majeures, Stablecoins, tokens déjà en BDD ou déjà vus
      if (
          clean_sym in ["BTC", "ETH", "USDT", "USDC", "EUR", "USD"]
          or clean_sym in db_symbols
          or clean_sym in seen_manual_symbols
      ):
        continue

      if tx_type == "buy" and cost >= 15.0:
        seen_manual_symbols.add(clean_sym)
        discrepancies.append({
            "type": "MANUAL_TRADE_DETECTED",
            "id": None,
            "symbol": clean_sym,
            "exchange": exch,
            "price": price,
            "cost": cost,
            "desc": f"🎯 Nouveau trade manuel récent détecté sur {exch} : Achat de {clean_sym} pour {cost:,.2f} € @ {price:.4f} €.",
        })
  except Exception as e:
    logging.warning(f"⚠️ Erreur lors de la détection des trades manuels : {e}")

  return discrepancies


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
  discrepancies = audit_database_vs_exchanges()
  print(f"Écarts détectés : {len(discrepancies)}")
