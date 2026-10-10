import concurrent.futures
import datetime
import json
import logging
import os
import re
from dotenv import load_dotenv
from google import genai
import requests

from daily_digest_engine import build_current_snapshot, init_finary_session
from db_manager import add_recommendation, init_db
from exchange_connectors import (
    get_all_open_orders,
    get_all_trades_history,
    get_total_verified_cash,
)
from pnl_engine import calculate_pnl, format_pnl_telegram_message
from sl_audit_engine import (
    audit_positions_protection,
    format_audit_telegram_message,
)

load_dotenv()
init_db()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SPECULATIVE_PCT = float(os.getenv("CRYPTO_SPECULATIVE_PCT", "0.10"))


def check_circuit_breaker():
  change_24h = 0.0
  try:
    res = requests.get(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=eur&include_24hr_change=true",
        timeout=5,
    )
    if res.status_code == 200:
      val = res.json().get("bitcoin", {}).get("eur_24h_change")
      if val is not None and val != 0.0:
        change_24h = float(val)
        return (change_24h <= -8.0), change_24h
  except Exception as e:
    logging.warning(f"⚠️ Erreur BTC 24h CoinGecko : {e}")

  try:
    res = requests.get(
        "https://api.okx.com/api/v5/market/ticker?instId=BTC-USDC", timeout=5
    )
    if res.status_code == 200:
      data = res.json().get("data", [])[0]
      last = float(data.get("last", 0))
      open24h = float(data.get("open24h", 0))
      if open24h > 0:
        change_24h = ((last - open24h) / open24h) * 100.0
        return (change_24h <= -8.0), change_24h
  except Exception as e:
    logging.warning(f"⚠️ Erreur BTC 24h OKX : {e}")

  return (change_24h <= -8.0), change_24h


def fetch_fear_and_greed_index():
  try:
    res = requests.get("https://api.alternative.me/fng/", timeout=5)
    if res.status_code == 200:
      data = res.json().get("data", [])[0]
      return {
          "value": int(data.get("value", 50)),
          "classification": data.get("value_classification", "Neutral"),
      }
  except Exception as e:
    logging.warning(f"⚠️ Erreur Fear & Greed API : {e}")
  return {"value": 50, "classification": "Neutral"}


def get_kraken_spot_symbols():
  try:
    res = requests.get("https://api.kraken.com/0/public/AssetPairs", timeout=5)
    if res.status_code == 200:
      pairs = res.json().get("result", {})
      symbols = set()
      for p in pairs.values():
        wsname = p.get("wsname", "")
        if "/" in wsname:
          base, quote = wsname.split("/")
          if quote in ["EUR", "USDT", "USD"]:
            symbols.add(base.upper())
      return symbols
  except Exception:
    pass
  return set()


def get_okx_spot_symbols():
  try:
    res = requests.get(
        "https://www.okx.com/api/v5/public/instruments?instType=SPOT",
        timeout=5,
    )
    if res.status_code == 200:
      data = res.json().get("data", [])
      symbols = set()
      for inst in data:
        inst_id = inst.get("instId", "")
        if "-" in inst_id:
          base, quote = inst_id.split("-")
          if quote in ["EUR", "USDT", "USD"]:
            symbols.add(base.upper())
      return symbols
  except Exception:
    pass
  return set()


def fetch_coingecko_trending_and_markets():
  headers = {"accept": "application/json"}
  opportunities = []
  try:
    res_tr = requests.get(
        "https://api.coingecko.com/api/v3/search/trending",
        headers=headers,
        timeout=5,
    )
    trending_coins = (
        res_tr.json().get("coins", []) if res_tr.status_code == 200 else []
    )
    coin_ids = [c["item"]["id"] for c in trending_coins[:10]]

    if coin_ids:
      url_mk = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=eur&ids={','.join(coin_ids)}&price_change_percentage=24h"
      res_mk = requests.get(url_mk, headers=headers, timeout=5)
      if res_mk.status_code == 200:
        for coin in res_mk.json():
          opportunities.append({
              "name": coin.get("name"),
              "symbol": coin.get("symbol", "").upper(),
              "price_eur": coin.get("current_price"),
              "change_24h_pct": coin.get("price_change_percentage_24h"),
              "market_cap_eur": coin.get("market_cap"),
              "coingecko_id": coin.get("id"),
          })
  except Exception as e:
    logging.warning(f"⚠️ Erreur CoinGecko : {e}")
  return opportunities


def send_telegram_with_inline_buttons(text, inline_keyboard=None):
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  html_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
  html_text = re.sub(r"\*(.*?)\*", r"<b>\1</b>", html_text)
  html_text = re.sub(r"_(.*?)_", r"<i>\1</i>", html_text)
  html_text = re.sub(r"`(.*?)`", r"<code>\1</code>", html_text)

  payload = {"chat_id": ALLOWED_USER_ID, "text": html_text, "parse_mode": "HTML"}
  if inline_keyboard:
    payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    logging.error(f"❌ Erreur réseau Telegram : {e}")


def parse_llm_json(raw_text):
  text = raw_text.strip()
  if text.startswith("```"):
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
  try:
    return json.loads(text, strict=False)
  except Exception:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
      return json.loads(match.group(0), strict=False)
    raise


def clean_token_code(raw_code, name=""):
  c = str(raw_code).upper().strip()
  for suffix in ["ZEUR", "ZUSD", "USDT", "USDC", "EUR", "USD"]:
    if c.endswith(suffix) and len(c) > len(suffix):
      c = c[: -len(suffix)]
  if len(c) == 4 and c.startswith("XX"):
    c = c[1:]
  if len(c) == 4 and c.startswith("X") and c not in ["XRP", "XLM", "XMR"]:
    c = c[1:]
  if c == "XBT":
    c = "BTC"
  return c.replace("-", "").replace("_", "").replace("/", "").strip()


def categorize_token(symbol, name=""):
  clean_code = clean_token_code(symbol, name)
  nm = str(name).upper().strip()

  dominant_codes = {
      "BTC",
      "XBT",
      "ETH",
      "WBTC",
      "STETH",
      "CBETH",
      "RETH",
      "WETH",
  }
  if (
      clean_code in dominant_codes
      or "BITCOIN" in nm
      or "ETHEREUM" in nm
      or nm == "BTC"
      or nm == "ETH"
  ):
    return "DOMINANT"

  core_codes = {
      "SOL",
      "ADA",
      "LINK",
      "AVAX",
      "ATOM",
      "UNI",
      "NEAR",
      "DOT",
      "MATIC",
      "POL",
      "XRP",
      "LTC",
      "BCH",
      "ETC",
      "ALGO",
      "FIL",
      "TRX",
      "APT",
      "SUI",
      "RENDER",
      "RNDR",
      "TAO",
      "FET",
      "INJ",
  }
  core_names = [
      "SOLANA",
      "CARDANO",
      "CHAINLINK",
      "AVALANCHE",
      "COSMOS",
      "UNISWAP",
      "POLKADOT",
      "POLYGON",
      "RIPPLE",
      "LITECOIN",
      "ALGORAND",
      "FILECOIN",
      "TRON",
      "NEAR PROTOCOL",
  ]
  if clean_code in core_codes or any(cn in nm for cn in core_names):
    return "CORE"

  return "SPECULATIVE"


def parse_finary_crypto_data(crypto_data):
  holdings = []
  accounts = []
  if isinstance(crypto_data, dict):
    res = crypto_data.get("result") or crypto_data.get("data") or crypto_data
    if isinstance(res, list):
      accounts = res
    elif isinstance(res, dict):
      accounts = (
          res.get("accounts")
          or res.get("cryptos")
          or res.get("result")
          or []
      )
  elif isinstance(crypto_data, list):
    accounts = crypto_data

  for acc in accounts:
    if not isinstance(acc, dict):
      continue

    sub_items = (
        acc.get("account_cryptos")
        or acc.get("cryptos")
        or acc.get("bank_account_cryptos")
        or acc.get("holdings")
        or acc.get("user_cryptos")
        or []
    )

    if sub_items and isinstance(sub_items, list):
      for item in sub_items:
        if not isinstance(item, dict):
          continue
        val = 0.0
        if "current_value" in item and item["current_value"] is not None:
          val = float(item["current_value"])
        elif "value" in item and item["value"] is not None:
          val = float(item["value"])
        else:
          qty = float(item.get("quantity", 0) or 0)
          price = float(item.get("current_price") or item.get("price") or 0)
          val = qty * price

        if val <= 0:
          continue

        crypto_obj = (
            item.get("crypto") if isinstance(item.get("crypto"), dict) else {}
        )
        code = (
            crypto_obj.get("code")
            or crypto_obj.get("symbol")
            or item.get("code")
            or item.get("symbol")
            or ""
        )
        name = crypto_obj.get("name") or item.get("name") or ""
        holdings.append((code, name, val))
    else:
      val = 0.0
      if "current_value" in acc and acc["current_value"] is not None:
        val = float(acc["current_value"])
      elif "balance" in acc and acc["balance"] is not None:
        val = float(acc["balance"])

      crypto_obj = (
          acc.get("crypto") if isinstance(acc.get("crypto"), dict) else {}
      )
      code = (
          crypto_obj.get("code")
          or crypto_obj.get("symbol")
          or acc.get("code")
          or acc.get("symbol")
          or acc.get("name")
          or ""
      )
      name = crypto_obj.get("name") or acc.get("name") or ""
      if val > 0 and code:
        holdings.append((code, name, val))

  return holdings


def calculate_exact_crypto_categories(crypto_data, current_snap=None):
  total_btc_eth = 0.0
  total_core = 0.0
  total_speculative = 0.0

  snap_total = 0.0
  if current_snap and "total_crypto" in current_snap:
    snap_total = float(current_snap.get("total_crypto", 0.0) or 0.0)

  holdings = parse_finary_crypto_data(crypto_data)

  if not holdings and current_snap and "holdings" in current_snap:
    holdings_dict = current_snap["holdings"]
    if isinstance(holdings_dict, dict):
      for k, v in holdings_dict.items():
        try:
          val = float(v)
          if val > 0:
            holdings.append((str(k), str(k), val))
        except (ValueError, TypeError):
          pass

  for sym, nm, val in holdings:
    cat = categorize_token(sym, nm)
    if cat == "DOMINANT":
      total_btc_eth += val
    elif cat == "CORE":
      total_core += val
    else:
      total_speculative += val

  calculated_total = total_btc_eth + total_core + total_speculative
  final_total = snap_total if snap_total > 0 else calculated_total

  if (
      snap_total > 0
      and calculated_total > 0
      and abs(calculated_total - snap_total) / snap_total > 0.02
  ):
    scale = snap_total / calculated_total
    total_btc_eth *= scale
    total_core *= scale
    total_speculative *= scale
    final_total = snap_total

  return {
      "total_crypto": final_total,
      "btc_eth": total_btc_eth,
      "btc_eth_pct": (
          (total_btc_eth / final_total * 100) if final_total > 0 else 0.0
      ),
      "core": total_core,
      "core_pct": (
          (total_core / final_total * 100) if final_total > 0 else 0.0
      ),
      "speculative": total_speculative,
      "speculative_pct": (
          (total_speculative / final_total * 100) if final_total > 0 else 0.0
      ),
  }


def generate_crypto_speculative_report():
  logging.info(
      "🚀 Démarrage de l'analyse spéculative Crypto v1.8.10 (Anti-blocage"
      " Timeout)..."
  )

  is_crash, crash_pct = check_circuit_breaker()
  fng = fetch_fear_and_greed_index()
  cash_data = get_total_verified_cash()
  trades = get_all_trades_history()
  pnl_summary = calculate_pnl(trades)

  open_orders = get_all_open_orders()
  audit_summary = audit_positions_protection(
      pnl_summary["open_positions"], open_orders
  )
  audit_text = format_audit_telegram_message(audit_summary)

  kraken_symbols = get_kraken_spot_symbols()
  okx_symbols = get_okx_spot_symbols()

  session = init_finary_session()
  from finary_uapi.user_portfolio import (
      get_portfolio_cryptos,
      get_portfolio_investments,
  )

  invest_data = get_portfolio_investments(session)
  crypto_data = get_portfolio_cryptos(session)
  current_snap = build_current_snapshot(invest_data, crypto_data)

  cats = calculate_exact_crypto_categories(crypto_data, current_snap)
  total_crypto = cats["total_crypto"]
  target_budget = total_crypto * SPECULATIVE_PCT

  holdings_exact = parse_finary_crypto_data(crypto_data)
  holdings_summary_list = [
      f"• {sym} ({nm}) : {val:.2f} €"
      for sym, nm, val in holdings_exact
      if val > 1.0
  ]
  holdings_prompt_text = (
      "\n".join(holdings_summary_list)
      if holdings_summary_list
      else "Aucune ligne significative détectée."
  )

  raw_opportunities = (
      [] if is_crash else fetch_coingecko_trending_and_markets()
  )
  verified_opportunities = []
  for item in raw_opportunities:
    sym = item["symbol"].upper()
    available = []
    if sym in kraken_symbols:
      available.append("KRAKEN")
    if sym in okx_symbols:
      available.append("OKX")
    if available:
      item["exchanges_verifies"] = available
      verified_opportunities.append(item)

  prompt = f"""Tu es un trader crypto expert, analytique et pédagogue, spécialisé en altcoins et micro-capitalisations.
Rédige un rapport d'arbitrage quotidien complet et détaillé pour Telegram en appliquant la stratégie Moonbag et les règles v1.8. Le ton doit être professionnel, riche en analyses et argumenté.

ETAT DU MARCHÉ & CIRCUIT BREAKER :
- Statut : {"🚨 DEFENSIVE MODE / CRASH DÉTECTÉ" if is_crash else "✅ NORMAL"}
- Var BTC 24h : {crash_pct:+.2f}%

DONNÉES CHIFFRÉES EXACTES ET PRÉ-CALCULÉES :
- Valeur totale poche Crypto : {total_crypto:,.2f} €
- Enveloppe spéculative cible ({SPECULATIVE_PCT*100:.0f}%) : {target_budget:,.2f} €
- REPARTITION EXACTE DES ACTIFS :
  • Dominantes (BTC & ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.2f}%)
  • Core / Non-spéculatives (SOL, ADA, LINK, AVAX, ATOM, UNI, XRP, TAO, NEAR) : {cats['core']:,.2f} € ({cats['core_pct']:.2f}%)
  • Spéculatives Actives : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.2f}%)
- INDICE FEAR & GREED DU MARCHÉ : {fng['value']}/100 ({fng['classification']})
- LIQUIDITÉS CASH RÉELLES EN TEMPS RÉEL :
  • Total Cash : {cash_data['total_cash']:,.2f} € (ATTENTION : Cash quasi nul, TOUT ACHAT NÉCESSITE UNE VENTE ÉQUIVALENTE !)

INVENTAIRE RÉEL ET VALORISÉ DE CHAQUE LIGNE DU PORTEFEUILLE (VALEUR MAXIMALE VENDABLE PAR ACTIF) :
{holdings_prompt_text}

POSITIONS SUIVIES EN BDD :
{json.dumps(pnl_summary['open_positions'], indent=2, ensure_ascii=False)}

OPPORTUNITÉS PRÉ-FILTRÉES (Kraken / OKX) :
{json.dumps(verified_opportunities, indent=2, ensure_ascii=False)}

CONSIGNES DE RÉDACTION DU RAPPORT (clé "report_markdown") :
1. **Analyse de Marché & Bilan de la Poche Crypto** :
   - Rédige un paragraphe analytique commentant l'indice Fear & Greed ({fng['value']}/100) et la tendance du BTC ({crash_pct:+.2f}%).
   - Détaille clairement la répartition de la poche (Montants en € et pourcentages exacts).
   - Fais le point sur l'enveloppe spéculative par rapport à la cible ({target_budget:,.2f} €).

2. **Opportunités & Plan d'Arbitrage Explicite (RÈGLE CASH-NEUTRAL)** :
   - Pour chaque opportunité recommandée, **tu dois obligatoirement écrire clairement dans le texte du rapport la ligne d'arbitrage** sous la forme : 
     `🔄 Arbitrage : Vendre [Montant] € de [Nom de l'actif existant] pour financer l'achat de [Nouveau Token].`
   - **RÈGLE ABSOLUE** : Le montant de la vente ne doit jamais dépasser la valeur réelle de l'actif disponible dans l'inventaire ci-dessus.
   - Détailler pour chaque token le plan Moonbag complet :
     • **Prix d'entrée conseillé**
     • **TP1 (+100% / x2)** : Vente de 50% (Free Ride) + SL Break-Even.
     • **TP2 (+300% / x4)** : Vente de 25%.
     • **TP3 (Moonbag)** : Conservation des 25% restants.
     • **Stop Loss d'Invalidation (SL)** : Niveau technique (-40% à -50%).

RÉPONSE JSON STRICTE :
{{
  "report_markdown": "Texte Markdown riche, détaillé et structuré...",
  "recommendations": [
    {{
      "symbol": "STRK",
      "exchange": "OKX",
      "price": 0.4500,
      "amount_eur": 100.0,
      "sell_symbol": "TAO",
      "tp1_price": 0.9000,
      "tp2_price": 1.8000,
      "sl_price": 0.2500,
      "sl_type": "invalidation"
    }}
  ]
}}"""

  client = genai.Client(api_key=GEMINI_API_KEY)
  modeles = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"]

  report_text = None
  recs = []

  # Fonction d'appel LLM protégée par un ThreadPoolExecutor (timeout strict 15s)
  def call_llm(model_name):
    res = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return res.text

  for mod in modeles:
    try:
      logging.info(f"🔄 Tentative appel LLM avec {mod} (Timeout 15s)...")
      with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(call_llm, mod)
        raw_response = future.result(timeout=15)

      if raw_response:
        data = parse_llm_json(raw_text=raw_response)
        report_text = data.get("report_markdown", "")
        recs = data.get("recommendations", [])
        logging.info(f"✅ Rapport généré avec succès via {mod}")
        break
    except concurrent.futures.TimeoutError:
      logging.warning(
          f"⏱️ Timeout dépassé (15s) sur {mod}. Passage au modèle suivant..."
      )
    except Exception as e:
      logging.warning(f"⚠️ Erreur sur {mod} ({e}). Passage au suivant...")

  if report_text:
    inline_keyboard = []
    for r in recs:
      price = r.get("price", 0.0)
      sell_info = (
          f" (via vente {r.get('sell_symbol')})" if r.get("sell_symbol") else ""
      )
      rec_id = add_recommendation(
          symbol=r.get("symbol", "UNKNOWN"),
          exchange=r.get("exchange", "OKX"),
          price=price,
          amount_eur=r.get("amount_eur", 0.0),
          tp1_price=r.get("tp1_price", price * 2.0),
          tp2_price=r.get("tp2_price", price * 4.0),
          sl_price=r.get("sl_price", price * 0.5),
          sl_type=r.get("sl_type", "invalidation"),
      )
      inline_keyboard.append([
          {
              "text": (
                  f"🔄 Arbitrer : {r.get('symbol')} ({r.get('amount_eur', 0):.0f}€){sell_info}"
              ),
              "callback_data": f"trade_take_{rec_id}",
          },
          {
              "text": f"❌ Ignorer {r.get('symbol')}",
              "callback_data": f"trade_ignore_{rec_id}",
          },
      ])

    header = (
        "🎯 *RAPPORT CRYPTO SPÉCULATIF (19H) — v1.8.10*\n"
        f"_Données du {datetime.date.today().strftime('%d/%m/%Y')} | Fear &"
        f" Greed: {fng['value']}/100_\n\n"
    )

    send_telegram_with_inline_buttons(
        header + report_text, inline_keyboard=inline_keyboard
    )
    send_telegram_with_inline_buttons(audit_text)
  else:
    logging.error(
        "❌ Échec critique : tous les modèles LLM ont échoué ou fait un timeout."
    )


if __name__ == "__main__":
  generate_crypto_speculative_report()
