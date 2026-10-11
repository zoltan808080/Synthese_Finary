import logging
import os
import time
import requests
from dotenv import load_dotenv
from db_manager import (
    get_open_positions,
    is_manual_trade_ignored,
)
from exchange_connectors import (
    get_kraken_crypto_balances,
    get_okx_crypto_balances,
    get_mexc_crypto_balances,
    get_all_trades_history,
)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")


def get_live_eur_usd_rate():
  """Récupère le taux EUR/USD live pour la conversion automatique des trades USD."""
  try:
    res = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=2)
    if res.status_code == 200:
      return float(res.json().get("rates", {}).get("USD", 1.12))
  except Exception:
    pass
  return 1.12


def clean_pair_symbol(pair_str):
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
  open_positions = get_open_positions()

  kraken_balances = get_kraken_crypto_balances()
  okx_balances = get_okx_crypto_balances()
  mexc_balances = get_mexc_crypto_balances()

  discrepancies = []
  db_symbols = set()

  grouped_positions = {}
  for p in open_positions:
    pos_id = p[0]
    sym = clean_pair_symbol(p[1])
    exch = str(p[2]).upper().strip()
    db_amount_eur = float(p[4]) if p[4] is not None else 0.0
    entry_price = float(p[3]) if p[3] is not None else 0.0
    db_qty = (db_amount_eur / entry_price) if entry_price > 0 else 0.0

    db_symbols.add(sym)
    clean_exch = "KRAKEN" if "KRAKEN" in exch else ("OKX" if "OKX" in exch else ("MEXC" if "MEXC" in exch else exch))
    key = (sym, clean_exch)

    if key not in grouped_positions:
      grouped_positions[key] = {"total_db_qty": 0.0, "total_db_amount_eur": 0.0, "items": []}

    grouped_positions[key]["total_db_qty"] += db_qty
    grouped_positions[key]["total_db_amount_eur"] += db_amount_eur
    grouped_positions[key]["items"].append({"id": pos_id, "entry_price": entry_price, "amount_eur": db_amount_eur, "qty": db_qty})

  for (sym, exch), data in grouped_positions.items():
    total_db_qty = data["total_db_qty"]
    total_db_amount_eur = data["total_db_amount_eur"]
    first_pos_id = data["items"][0]["id"] if data["items"] else None

    target_exchange_dict = kraken_balances if exch == "KRAKEN" else (okx_balances if exch == "OKX" else mexc_balances)
    real_asset_info = target_exchange_dict.get(sym, {})
    real_qty = float(real_asset_info.get("amount", -1.0))

    if real_qty == 0.0 or (real_qty == -1.0 and sym not in ["BTC", "ETH"]):
      discrepancies.append({
          "type": "GHOST_POSITION",
          "id": first_pos_id,
          "symbol": sym,
          "exchange": exch,
          "db_amount": total_db_amount_eur,
          "real_amount": 0.0,
          "desc": f"Ligne fantôme : {sym} ({exch}) actif en BDD ({total_db_amount_eur:,.2f} €) mais solde réel = 0.",
      })
    elif real_qty > 0.0 and total_db_qty > 0.0:
      diff_ratio = abs(total_db_qty - real_qty) / real_qty
      if diff_ratio > 0.30:
        mean_entry = total_db_amount_eur / total_db_qty if total_db_qty > 0 else 0.0
        adjusted_amount = real_qty * mean_entry
        discrepancies.append({
            "type": "VOLUME_MISMATCH",
            "id": first_pos_id,
            "symbol": sym,
            "exchange": exch,
            "db_amount": total_db_amount_eur,
            "real_qty": real_qty,
            "adjusted_amount_eur": adjusted_amount,
            "desc": f"Écart de volume sur {sym} ({exch}) : BDD = {total_db_qty:,.2f} tokens vs Réel = {real_qty:,.2f} tokens.",
        })

  # Détection des nouveaux trades manuels avec CONVERSION EUR AUTOMATIQUE
  try:
    recent_trades = get_all_trades_history()
    now_ts = time.time()
    cutoff_48h = now_ts - (48 * 3600)
    seen_manual_symbols = set()
    eur_usd_rate = get_live_eur_usd_rate()

    for trade in recent_trades:
      trade_time = float(trade.get("time", 0.0))
      if trade_time < cutoff_48h:
        continue

      tx_type = str(trade.get("type", "")).lower()
      pair = str(trade.get("pair", "")).upper()
      exch = str(trade.get("exchange", "Unknown")).upper().strip()
      price = float(trade.get("price", 0.0))
      cost = float(trade.get("cost", 0.0))

      clean_sym = clean_pair_symbol(pair)

      if (
          clean_sym in ["BTC", "ETH", "USDT", "USDC", "EUR", "USD"]
          or clean_sym in db_symbols
          or clean_sym in seen_manual_symbols
      ):
        continue

      # 1. Calcul des prix en EUR
      if any(pair.endswith(s) for s in ["USD", "USDT", "USDC", "ZUSD"]):
        cost_eur = cost / eur_usd_rate
        price_eur = price / eur_usd_rate
      else:
        cost_eur = cost
        price_eur = price

      # 2. Vérification si le trade a déjà été classé CORE / Ignoré (insensible à la casse & tolérant sur les montants)
      if is_manual_trade_ignored(clean_sym, exch, cost_eur):
        continue

      # 3. Ajout si trade d'achat >= 15€
      if tx_type == "buy" and cost_eur >= 15.0:
        seen_manual_symbols.add(clean_sym)
        discrepancies.append({
            "type": "MANUAL_TRADE_DETECTED",
            "id": None,
            "symbol": clean_sym,
            "exchange": exch,
            "price": price_eur,
            "cost": cost_eur,
            "desc": f"🎯 Nouveau trade manuel récent détecté sur {exch} : Achat de {clean_sym} pour {cost_eur:,.2f} € @ {price_eur:.4f} €.",
        })
  except Exception as e:
    logging.warning(f"⚠️ Erreur lors de la détection des trades manuels : {e}")

  return discrepancies
