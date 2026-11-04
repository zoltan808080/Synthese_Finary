import base64
import datetime
import hashlib
import hmac
import logging
import os
import re
import time
import urllib.parse
from dotenv import load_dotenv
import requests

load_dotenv()


# --- EXODUS WALLETS (Cold Wallets) ---

def get_exodus_wallet_balances():
  """Récupère les soldes BTC et ETH des wallets froids Exodus via des RPC haute disponibilité."""
  balances = {}
  btc_address = os.getenv("EXODUS_BTC_ADDRESS")
  eth_address = os.getenv("EXODUS_ETH_ADDRESS")
  headers = {"User-Agent": "Mozilla/5.0"}

  # 1. Solde BTC via Mempool.space (avec fallback Blockstream)
  if btc_address:
    clean_btc = btc_address.strip()
    for api_url in [
        f"https://mempool.space/api/address/{clean_btc}",
        f"https://blockstream.info/api/address/{clean_btc}",
    ]:
      try:
        res = requests.get(api_url, headers=headers, timeout=4)
        if res.status_code == 200:
          stats = res.json().get("chain_stats", {})
          sats = stats.get("funded_txo_sum", 0) - stats.get("spent_txo_sum", 0)
          btc_amt = sats / 1e8
          if btc_amt > 0:
            balances["BTC"] = {"exchange": "Exodus", "amount": btc_amt}
            break
      except Exception:
        continue

  # 2. Solde ETH via RPC Ethereum direct (Cloudflare RPC + PublicNode)
  if eth_address:
    clean_eth = eth_address.strip()
    rpc_endpoints = [
        "https://cloudflare-eth.com",
        "https://ethereum-rpc.publicnode.com",
    ]
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [clean_eth, "latest"],
        "id": 1,
    }

    for rpc_url in rpc_endpoints:
      try:
        res = requests.post(rpc_url, json=payload, timeout=4)
        if res.status_code == 200:
          hex_bal = res.json().get("result")
          if hex_bal and str(hex_bal).startswith("0x"):
            wei = int(hex_bal, 16)
            eth_amt = wei / 1e18
            if eth_amt > 0:
              balances["ETH"] = {"exchange": "Exodus", "amount": eth_amt}
              break
      except Exception:
        continue

  return balances


# --- KRAKEN HELPER ---

def _get_kraken_signature(url_path, data, secret):
  """Génère la signature HMAC-SHA512 exigée par Kraken."""
  postdata = urllib.parse.urlencode(data)
  encoded = (str(data["nonce"]) + postdata).encode()
  message = url_path.encode() + hashlib.sha256(encoded).digest()
  mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
  return base64.b64encode(mac.digest()).decode()


# --- KRAKEN API (Read-Only) ---

def get_kraken_open_orders():
  """Récupère les ordres ouverts (Limit, Stop-Loss, Take-Profit) sur Kraken."""
  api_key = os.getenv("KRAKEN_API_KEY")
  api_secret = os.getenv("KRAKEN_API_SECRET")

  if not api_key or not api_secret:
    return []

  url_path = "/0/private/OpenOrders"
  data = {"nonce": str(int(time.time() * 1000)), "trades": True}
  headers = {
      "API-Key": api_key,
      "API-Sign": _get_kraken_signature(url_path, data, api_secret),
  }

  orders = []
  try:
    res = requests.post(
        f"https://api.kraken.com{url_path}",
        data=data,
        headers=headers,
        timeout=8,
    )
    if res.status_code == 200:
      raw_orders = res.json().get("result", {}).get("open", {})
      for order_id, o in raw_orders.items():
        descr = o.get("descr", {})
        orders.append({
            "order_id": order_id,
            "exchange": "Kraken",
            "pair": descr.get("pair"),
            "type": descr.get("type"),
            "ordertype": descr.get("ordertype"),
            "price": float(descr.get("price", 0)),
            "vol": float(o.get("vol", 0)),
            "status": o.get("status"),
        })
  except Exception as e:
    logging.warning(f"⚠️ Erreur ordres ouverts Kraken : {e}")

  return orders


def get_kraken_cash_and_trades():
  """Récupère les soldes fiat/stablecoins sur Kraken."""
  api_key = os.getenv("KRAKEN_API_KEY")
  api_secret = os.getenv("KRAKEN_API_SECRET")

  if not api_key or not api_secret:
    return 0.0

  url_path = "/0/private/Balance"
  data = {"nonce": str(int(time.time() * 1000))}
  headers = {
      "API-Key": api_key,
      "API-Sign": _get_kraken_signature(url_path, data, api_secret),
  }

  cash_eur = 0.0
  try:
    res = requests.post(
        f"https://api.kraken.com{url_path}",
        data=data,
        headers=headers,
        timeout=8,
    )
    if res.status_code == 200:
      balances = res.json().get("result", {})
      for asset, val in balances.items():
        if asset in ["ZEUR", "EUR", "ZUSD", "USD", "USDT"]:
          cash_eur += float(val)
  except Exception as e:
    logging.warning(f"⚠️ Erreur solde Kraken : {e}")

  return cash_eur


def get_kraken_trades_history(start_timestamp=None):
  """Récupère l'historique des exécutions (achats/ventes) sur Kraken."""
  api_key = os.getenv("KRAKEN_API_KEY")
  api_secret = os.getenv("KRAKEN_API_SECRET")

  if not api_key or not api_secret:
    return []

  url_path = "/0/private/TradesHistory"
  data = {"nonce": str(int(time.time() * 1000)), "trades": True}
  if start_timestamp:
    data["start"] = int(start_timestamp)

  headers = {
      "API-Key": api_key,
      "API-Sign": _get_kraken_signature(url_path, data, api_secret),
  }

  trades = []
  try:
    res = requests.post(
        f"https://api.kraken.com{url_path}",
        data=data,
        headers=headers,
        timeout=8,
    )
    if res.status_code == 200:
      raw_trades = res.json().get("result", {}).get("trades", {})
      for txid, t in raw_trades.items():
        trades.append({
            "txid": txid,
            "exchange": "Kraken",
            "pair": t.get("pair"),
            "type": t.get("type"),
            "price": float(t.get("price", 0)),
            "vol": float(t.get("vol", 0)),
            "cost": float(t.get("cost", 0)),
            "fee": float(t.get("fee", 0)),
            "time": float(t.get("time", 0)),
        })
  except Exception as e:
    logging.warning(f"⚠️ Erreur récupération trades Kraken : {e}")

  return trades


# --- OKX API (Read-Only) ---

def get_okx_open_orders():
  """Récupère les ordres Spot classiques ET les ordres Algo (TP/SL/OCO/Trigger) en attente sur OKX."""
  api_key = os.getenv("OKX_API_KEY")
  api_secret = os.getenv("OKX_API_SECRET")
  passphrase = os.getenv("OKX_PASSPHRASE")

  if not api_key or not api_secret or not passphrase:
    return []

  orders = []
  request_paths = [
      "/api/v5/trade/orders-pending?instType=SPOT",
      "/api/v5/trade/orders-algo-pending?ordType=conditional&instType=SPOT",
      "/api/v5/trade/orders-algo-pending?ordType=oco&instType=SPOT",
      "/api/v5/trade/orders-algo-pending?ordType=trigger&instType=SPOT",
  ]

  for request_path in request_paths:
    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    method = "GET"
    message = timestamp + method + request_path

    mac = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    sign = base64.b64encode(mac.digest()).decode("utf-8")

    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }

    try:
      url = f"https://okx-proxy-eu.vercel.app{request_path}"
      res = requests.get(url, headers=headers, timeout=8)
      if res.status_code == 200:
        for o in res.json().get("data", []):
          order_id = o.get("algoId") or o.get("ordId")
          ord_type = (
              o.get("ordType") or o.get("algoType") or "conditional"
          ).lower()
          price = (
              float(o.get("px", 0))
              or float(o.get("tpTriggerPx", 0))
              or float(o.get("slTriggerPx", 0))
              or float(o.get("triggerPx", 0))
          )
          orders.append({
              "order_id": str(order_id),
              "exchange": "OKX",
              "pair": o.get("instId"),
              "type": o.get("side", "sell").lower(),
              "ordertype": ord_type,
              "price": price,
              "vol": float(o.get("sz", 0)),
              "status": o.get("state"),
          })
    except Exception as e:
      logging.warning(f"⚠️ Erreur ordres ouverts OKX ({request_path}) : {e}")

  return orders


def get_okx_trades_history(limit=100):
  """Récupère l'historique des exécutions (achats/ventes) Spot sur OKX via le proxy Vercel."""
  api_key = os.getenv("OKX_API_KEY")
  api_secret = os.getenv("OKX_API_SECRET")
  passphrase = os.getenv("OKX_PASSPHRASE")

  if not api_key or not api_secret or not passphrase:
    return []

  now = datetime.datetime.now(datetime.timezone.utc)
  timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
  request_path = f"/api/v5/trade/fills-history?instType=SPOT&limit={limit}"
  method = "GET"
  message = timestamp + method + request_path

  mac = hmac.new(
      api_secret.encode("utf-8"),
      message.encode("utf-8"),
      digestmod=hashlib.sha256,
  )
  sign = base64.b64encode(mac.digest()).decode("utf-8")

  headers = {
      "OK-ACCESS-KEY": api_key,
      "OK-ACCESS-SIGN": sign,
      "OK-ACCESS-TIMESTAMP": timestamp,
      "OK-ACCESS-PASSPHRASE": passphrase,
      "Content-Type": "application/json",
  }

  trades = []
  try:
    url = f"https://okx-proxy-eu.vercel.app{request_path}"
    res = requests.get(url, headers=headers, timeout=8)
    if res.status_code == 200:
      raw_fills = res.json().get("data", [])
      for f in raw_fills:
        price = float(f.get("fillPx", 0))
        vol = float(f.get("fillSz", 0))
        trades.append({
            "txid": f.get("fillId"),
            "exchange": "OKX",
            "pair": f.get("instId"),
            "type": f.get("side"),
            "price": price,
            "vol": vol,
            "cost": price * vol,
            "fee": abs(float(f.get("fee", 0))),
            "time": float(f.get("ts", 0)) / 1000.0 if f.get("ts") else 0,
        })
  except Exception as e:
    logging.warning(f"⚠️ Erreur récupération trades OKX : {e}")

  return trades


def get_okx_cash_and_trades():
  """Récupère les soldes cash et l'historique récent sur OKX."""
  api_key = os.getenv("OKX_API_KEY")
  api_secret = os.getenv("OKX_API_SECRET")
  passphrase = os.getenv("OKX_PASSPHRASE")

  if not api_key or not api_secret or not passphrase:
    return 0.0

  timestamp = (
      str(time.time()).split(".")[0] + "." + str(time.time()).split(".")[1][:3]
  )
  message = f"{timestamp}GET/api/v5/account/balance"
  mac = hmac.new(
      api_secret.encode("utf-8"),
      message.encode("utf-8"),
      digestmod=hashlib.sha256,
  )
  sign = base64.b64encode(mac.digest()).decode("utf-8")

  headers = {
      "OK-ACCESS-KEY": api_key,
      "OK-ACCESS-SIGN": sign,
      "OK-ACCESS-TIMESTAMP": timestamp,
      "OK-ACCESS-PASSPHRASE": passphrase,
      "Content-Type": "application/json",
  }

  cash_eur = 0.0
  try:
    res = requests.get(
        "https://okx-proxy-eu.vercel.app/api/v5/account/balance",
        headers=headers,
        timeout=8,
    )
    if res.status_code == 200:
      details = res.json().get("data", [{}])[0].get("details", [])
      for item in details:
        if item.get("ccy") in ["EUR", "USDT", "USDC"]:
          cash_eur += float(item.get("eqUsdt", item.get("eq", 0)))
  except Exception as e:
    logging.warning(f"⚠️ Erreur solde OKX : {e}")

  return cash_eur


# --- SYNTHÈSE GLOBALE EXCHANGES & WALLETS ---

def get_all_open_orders():
  """Rassemble tous les ordres en attente sur Kraken et OKX."""
  kraken = get_kraken_open_orders()
  okx = get_okx_open_orders()

  all_orders = kraken + okx
  logging.info(
      f"📋 Ordres en attente — Kraken: {len(kraken)} | OKX: {len(okx)} | Total: {len(all_orders)}"
  )
  return all_orders


def get_all_trades_history():
  """Rassemble l'historique complet des exécutions (Kraken, OKX)."""
  logging.info("🔄 Récupération de l'historique global des trades (v3.3.1)...")

  kraken_trades = get_kraken_trades_history()
  okx_trades = get_okx_trades_history()

  all_trades = kraken_trades + okx_trades
  all_trades.sort(key=lambda x: x.get("time", 0))

  logging.info(
      f"📊 Trades récupérés — Kraken: {len(kraken_trades)} | OKX: {len(okx_trades)} | Total: {len(all_trades)}"
  )
  return all_trades


def get_total_verified_cash():
  """Synthétise les liquidités réelles disponibles sur les comptes Kraken et OKX."""
  kraken_cash = get_kraken_cash_and_trades()
  okx_cash = get_okx_cash_and_trades()

  total = kraken_cash + okx_cash
  logging.info(
      f"💰 Cash réel vérifié — Kraken: {kraken_cash:.2f}€ | OKX: {okx_cash:.2f}€ | Total: {total:.2f}€"
  )
  return {
      "total_cash": total,
      "kraken_cash": kraken_cash,
      "okx_cash": okx_cash,
  }


# --- SOLDES CRYPTO RÉELS ---

KRAKEN_ASSET_MAP = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XXBT.M": "BTC",
    "XBT.S": "BTC",
    "XETH": "ETH",
    "ETH": "ETH",
    "ETH2.S": "ETH",
    "ETH2": "ETH",
    "ETH.S": "ETH",
    "XETH.S": "ETH",
    "ZEUR": "EUR",
    "ZUSD": "USD",
    "XXRP": "XRP",
    "XXLM": "XLM",
    "XXMR": "XMR",
    "XSOL": "SOL",
    "SOL.S": "SOL",
    "SOL21.S": "SOL",
    "SOL.M": "SOL",
    "SOL.P": "SOL",
    "XSOL.S": "SOL",
    "DOT.S": "DOT",
    "ADA.S": "ADA",
    "ATOM.S": "ATOM",
    "NEAR.S": "NEAR",
    "AVAX.S": "AVAX",
}


def get_kraken_crypto_balances():
  """Récupère l'ensemble des soldes crypto réels sur Kraken en consolidant le Staking (ETH2.S, SOL.S, SOL21.S, etc.)."""
  api_key = os.getenv("KRAKEN_API_KEY")
  api_secret = os.getenv("KRAKEN_API_SECRET")
  if not api_key or not api_secret:
    return {}

  url_path = "/0/private/Balance"
  data = {"nonce": str(int(time.time() * 1000))}
  headers = {
      "API-Key": api_key,
      "API-Sign": _get_kraken_signature(url_path, data, api_secret),
  }

  balances = {}
  try:
    res = requests.post(
        f"https://api.kraken.com{url_path}",
        data=data,
        headers=headers,
        timeout=8,
    )
    if res.status_code == 200:
      raw = res.json().get("result", {})
      for asset, val in raw.items():
        amount = float(val)
        if amount > 0.000001:
          clean_asset = KRAKEN_ASSET_MAP.get(asset)

          if not clean_asset:
            temp = re.sub(r"(\.S|\.M|\.P|\d+\.S)$", "", asset)

            if temp.startswith("ETH"):
              clean_asset = "ETH"
            elif temp.startswith("XBT") or temp.startswith("XXBT"):
              clean_asset = "BTC"
            elif (
                len(temp) == 4
                and temp.startswith("X")
                and temp not in ["XRP", "XLM", "XMR"]
            ):
              clean_asset = temp[1:]
            elif len(temp) == 4 and temp.startswith("Z"):
              clean_asset = temp[1:]
            else:
              clean_asset = temp

          clean_asset = clean_asset.upper()

          if clean_asset in balances:
            balances[clean_asset]["amount"] += amount
          else:
            balances[clean_asset] = {"exchange": "Kraken", "amount": amount}
  except Exception as e:
    logging.warning(f"⚠️ Erreur soldes crypto Kraken : {e}")
  return balances


def get_okx_crypto_balances():
  """Récupère l'ensemble des soldes crypto et cash sur OKX."""
  api_key = os.getenv("OKX_API_KEY")
  api_secret = os.getenv("OKX_API_SECRET")
  passphrase = os.getenv("OKX_PASSPHRASE")
  if not api_key or not api_secret or not passphrase:
    return {}

  timestamp = (
      datetime.datetime.now(datetime.timezone.utc).strftime(
          "%Y-%m-%dT%H:%M:%S.%f"
      )[:-3]
      + "Z"
  )
  request_path = "/api/v5/account/balance"
  message = timestamp + "GET" + request_path

  mac = hmac.new(
      api_secret.encode("utf-8"),
      message.encode("utf-8"),
      digestmod=hashlib.sha256,
  )
  sign = base64.b64encode(mac.digest()).decode("utf-8")

  headers = {
      "OK-ACCESS-KEY": api_key,
      "OK-ACCESS-SIGN": sign,
      "OK-ACCESS-TIMESTAMP": timestamp,
      "OK-ACCESS-PASSPHRASE": passphrase,
      "Content-Type": "application/json",
  }

  balances = {}
  try:
    url = f"https://okx-proxy-eu.vercel.app{request_path}"
    res = requests.get(url, headers=headers, timeout=8)
    if res.status_code == 200:
      data = res.json().get("data", [{}])
      if data:
        details = data[0].get("details", [])
        for item in details:
          ccy = item.get("ccy")
          cash_bal = float(item.get("cashBal", 0) or item.get("eq", 0))
          if cash_bal > 0.000001:
            balances[ccy.upper()] = {"exchange": "OKX", "amount": cash_bal}
  except Exception as e:
    logging.warning(f"⚠️ Erreur soldes crypto OKX : {e}")
  return balances


def get_all_exchange_crypto_balances():
  """Rassemble tous les soldes crypto des exchanges ET des wallets froids (Exodus)."""
  kraken = get_kraken_crypto_balances()
  okx = get_okx_crypto_balances()
  exodus = get_exodus_wallet_balances()

  combined = {}
  for d in [kraken, okx, exodus]:
    for asset, info in d.items():
      if asset in combined:
        combined[asset]["amount"] += info["amount"]
      else:
        combined[asset] = {
            "exchange": info["exchange"],
            "amount": info["amount"],
        }
  return combined