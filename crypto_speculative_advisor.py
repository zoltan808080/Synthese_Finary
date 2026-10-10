import concurrent.futures
import datetime
import json
import logging
import os
import re
from dotenv import load_dotenv
from google import genai
import requests

from db_manager import add_recommendation, get_open_positions, init_db
from exchange_connectors import (
    get_all_exchange_crypto_balances,
    get_all_open_orders,
    get_all_trades_history,
    get_total_verified_cash,
)
from pnl_engine import calculate_pnl

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


def fetch_live_price(sym, exch="OKX"):
  clean_sym = str(sym).upper().replace("USDT", "").replace("EUR", "").strip()
  price = None
  try:
    res_okx = requests.get(
        f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-USDT",
        timeout=1.5,
    )
    if res_okx.status_code == 200:
      data_okx = res_okx.json().get("data", [])
      if data_okx:
        price_usdt = float(data_okx[0].get("last", 0))
        price = price_usdt / 1.12
  except Exception:
    pass

  if not price:
    try:
      res_k = requests.get(
          f"https://api.kraken.com/0/public/Ticker?pair={clean_sym}EUR",
          timeout=1.5,
      )
      if res_k.status_code == 200:
        result_k = res_k.json().get("result", {})
        for _, v_data in result_k.items():
          if "c" in v_data:
            price = float(v_data["c"][0])
            break
    except Exception:
      pass
  return price or 0.0


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


def calculate_unfied_crypto_categories():
  """Calcul 100% autonome et unifié avec le Dashboard v2.8 (Exchanges + Exodus + BDD SQLite)."""
  all_crypto_balances = get_all_exchange_crypto_balances()
  positions = get_open_positions()

  speculative_symbols = {
      str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
      for p in positions
  }

  val_majors = 0.0
  val_core = 0.0
  val_speculative = 0.0
  val_stables_crypto = 0.0
  
  holdings_by_asset = {}

  # 1. Valorisation des positions spéculatives actives depuis la BDD SQLite
  for p in positions:
    sym = str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
    exch = p[2]
    entry_eur = float(p[3]) if p[3] is not None else 0.0
    amount_eur = float(p[4]) if p[4] is not None else 0.0
    lp = fetch_live_price(sym, exch) or entry_eur
    qty = (amount_eur / entry_eur) if entry_eur > 0 else 0.0
    current_val = qty * lp
    val_speculative += current_val
    
    holdings_by_asset[sym] = holdings_by_asset.get(sym, 0.0) + current_val

  # 2. Valorisation de l'ensemble des avoirs sur Exchanges & Wallets froids Exodus
  for asset, info in all_crypto_balances.items():
    amt = info["amount"]
    exch = info["exchange"]

    if asset in ["USDT", "USDC"]:
      val_stables_crypto += amt
      holdings_by_asset[asset] = holdings_by_asset.get(asset, 0.0) + amt
    elif asset in ["BTC", "ETH"]:
      lp = fetch_live_price(asset, exch)
      asset_val = amt * lp
      val_majors += asset_val
      holdings_by_asset[asset] = holdings_by_asset.get(asset, 0.0) + asset_val
    elif asset in speculative_symbols:
      pass  # Déjà comptabilisé via la BDD SQLite
    else:
      lp = fetch_live_price(asset, exch)
      asset_val = amt * lp
      val_core += asset_val
      holdings_by_asset[asset] = holdings_by_asset.get(asset, 0.0) + asset_val

  cash_data = get_total_verified_cash()
  total_cash_fiat = cash_data.get("total_cash", 0.0)
  
  # Total Liquidités = Stablecoins (USDT/USDC) + Cash Fiat (EUR/USD)
  total_liquidities = val_stables_crypto + total_cash_fiat

  total_crypto = val_majors + val_core + val_speculative + total_liquidities

  if total_crypto > 0:
    pct_majors = (val_majors / total_crypto) * 100.0
    pct_core = (val_core / total_crypto) * 100.0
    pct_spec = (val_speculative / total_crypto) * 100.0
    pct_stables = (total_liquidities / total_crypto) * 100.0
  else:
    pct_majors = pct_core = pct_spec = pct_stables = 0.0

  # Mise en forme claire des plafonds d'arbitrage par actif
  holdings_lines = []
  for asset, val_eur in sorted(holdings_by_asset.items(), key=lambda x: x[1], reverse=True):
    if val_eur >= 0.5:
      holdings_lines.append(f"• {asset} : {val_eur:.2f} € MAX ARBITRABLE")

  return {
      "total_crypto": total_crypto,
      "btc_eth": val_majors,
      "btc_eth_pct": pct_majors,
      "core": val_core,
      "core_pct": pct_core,
      "speculative": val_speculative,
      "speculative_pct": pct_spec,
      "stables_cash": total_liquidities,
      "stables_cash_pct": pct_stables,
      "val_stables_crypto": val_stables_crypto,
      "total_cash_fiat": total_cash_fiat,
      "holdings_text": "\n".join(holdings_lines) if holdings_lines else "Aucun actif significatif",
      "cash_data": cash_data,
  }


def generate_crypto_speculative_report():
  logging.info("🚀 Démarrage de l'analyse spéculative Crypto v2.8 (100% Unifié Dashboard)...")

  is_crash, crash_pct = check_circuit_breaker()
  fng = fetch_fear_and_greed_index()
  trades = get_all_trades_history()
  pnl_summary = calculate_pnl(trades)

  kraken_symbols = get_kraken_spot_symbols()
  okx_symbols = get_okx_spot_symbols()

  # Moteur unifié direct-exchanges + Exodus + SQLite
  cats = calculate_unfied_crypto_categories()
  total_crypto = cats["total_crypto"]
  target_budget = total_crypto * SPECULATIVE_PCT

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
Rédige un rapport d'arbitrage quotidien complet pour Telegram en appliquant la stratégie Moonbag. Le ton doit être professionnel, structuré et rigoureux.

ETAT DU MARCHÉ & CIRCUIT BREAKER :
- Statut : {"🚨 DEFENSIVE MODE / CRASH DÉTECTÉ" if is_crash else "✅ NORMAL"}
- Var BTC 24h : {crash_pct:+.2f}%

DONNÉES CHIFFRÉES UNIFIÉES (EXCHANGES + EXODUS) :
- Valeur totale du portefeuille : {total_crypto:,.2f} €
- Enveloppe spéculative cible ({SPECULATIVE_PCT*100:.0f}%) : {target_budget:,.2f} €
- REPARTITION EXACTE DES ACTIFS :
  • Majeures (BTC & ETH - Spot + Staking + Exodus) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.2f}%)
  • Core / Convictions long terme : {cats['core']:,.2f} € ({cats['core_pct']:.2f}%)
  • Spéculatives Actives (Moonbag BDD) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.2f}%)
  • Liquidités Totales (Fiat + Stablecoins) : {cats['stables_cash']:,.2f} € ({cats['stables_cash_pct']:.2f}%)
- INDICE FEAR & GREED DU MARCHÉ : {fng['value']}/100 ({fng['classification']})
- DETAIL DES LIQUIDITÉS DISPONIBLES EN TEMPS RÉEL :
  • Stablecoins (USDT + USDC) : {cats['val_stables_crypto']:,.2f} €
  • Cash Fiat (EUR + USD) : {cats['total_cash_fiat']:,.2f} €
  • Total Liquidités Immédiates : {cats['stables_cash']:,.2f} €

PLAFONDS STRICTS D'ARBITRAGE PAR ACTIF DÉTENU (SEUIL MAXIMAL VENDABLE) :
{cats['holdings_text']}

POSITIONS ACTIVES SUIVIES EN BDD SQLITE :
{json.dumps(pnl_summary['open_positions'], indent=2, ensure_ascii=False)}

OPPORTUNITÉS PRÉ-FILTRÉES (Kraken / OKX) :
{json.dumps(verified_opportunities, indent=2, ensure_ascii=False)}

RÈGLES IMPÉRATIVES POUR L'ARBITRAGE ET LA VENTE D'ACTIFS :
1. **UTILISATION PRIORITAIRE DES LIQUIDITÉS** : Si le total des liquidités disponibles ({cats['stables_cash']:,.2f} €) permet de financer un nouvel achat, UTILISER CES LIQUIDITÉS SANS PROPOSER DE VENTE D'ACTIFS.
2. **PLAFOND STRICT DE VENTE (`sell_symbol`)** : Si une vente est proposée, il est STRICTEMENT INTERDIT de recommander la vente d'un montant supérieur à la valeur réelle détenue listée dans "PLAFONDS STRICTS D'ARBITRAGE PAR ACTIF DÉTENU" (ex: si l'utilisateur possède 1.76 € de TAO, le montant de la vente de TAO ne peut JAMAIS dépasser 1.76 €). Ne jamais inventer ou supposer un solde supérieur.
3. **SEUIL MINIMUM D'ARBITRAGE** : Ne proposer la vente d'un actif existant que si sa valeur totale disponible est d'au moins 20.00 €.

CONSIGNES DE RÉDACTION DU RAPPORT (clé "report_markdown") :
1. **Analyse de Marché & Bilan de la Poche Crypto** :
   - Rédige un paragraphe commentant l'indice Fear & Greed ({fng['value']}/100) et la tendance du BTC ({crash_pct:+.2f}%).
   - Détaille la répartition exacte de la poche (Montants en € et pourcentages).
   - Fais le point sur l'enveloppe spéculative ({cats['speculative']:,.2f} €) par rapport à la cible ({target_budget:,.2f} €).
   - Mentionne les liquidités totales disponibles ({cats['stables_cash']:,.2f} € - Fiat + Stablecoins).

2. **Opportunités & Plan d'Arbitrage Explicite (RÈGLE CASH-NEUTRAL)** :
   - Si une opportunité est recommandée, écris clairement la ligne d'arbitrage (en utilisant les liquidités ou une vente valide respectant le plafond strict).
   - Détailler pour chaque token recommandé le plan Moonbag :
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
      "sell_symbol": "USDT",
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
      logging.warning(f"⏱️ Timeout dépassé (15s) sur {mod}. Passage au modèle suivant...")
    except Exception as e:
      logging.warning(f"⚠️ Erreur sur {mod} ({e}). Passage au suivant...")

  if report_text:
    inline_keyboard = []
    for r in recs:
      price = r.get("price", 0.0)
      sell_info = (
          f" (via {r.get('sell_symbol')})" if r.get("sell_symbol") else ""
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
              "text": f"🔄 Arbitrer : {r.get('symbol')} ({r.get('amount_eur', 0):.0f}€){sell_info}",
              "callback_data": f"trade_take_{rec_id}",
          },
          {
              "text": f"❌ Ignorer {r.get('symbol')}",
              "callback_data": f"trade_ignore_{rec_id}",
          },
      ])

    header = (
        "🎯 *RAPPORT CRYPTO SPÉCULATIF (19H) — v2.8*\n"
        f"_Données du {datetime.date.today().strftime('%d/%m/%Y')} | Fear &"
        f" Greed: {fng['value']}/100_\n\n"
    )

    send_telegram_with_inline_buttons(
        header + report_text, inline_keyboard=inline_keyboard
    )
  else:
    logging.error("❌ Échec critique : tous les modèles LLM ont échoué ou fait un timeout.")


if __name__ == "__main__":
  generate_crypto_speculative_report()