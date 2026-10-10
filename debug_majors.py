import os
import requests
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv(os.path.expanduser("~/Synthese_finary/.env"))

from exchange_connectors import (
    get_exodus_wallet_balances,
    get_kraken_crypto_balances,
    get_okx_crypto_balances,
    get_mexc_crypto_balances,
)

eur_usd_rate = 1.12  # Taux de référence du test


def fetch_live_price_debug(sym):
  """Vérifie l'extraction du prix live pour un symbole (BTC / ETH)."""
  clean_sym = str(sym).upper().replace("USDT", "").replace("EUR", "").strip()
  price = None

  # 1. Test via OKX
  try:
    res = requests.get(
        f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-USDT",
        timeout=3,
    )
    if res.status_code == 200:
      data = res.json().get("data", [])
      if data:
        price_usdt = float(data[0].get("last", 0))
        price = price_usdt / eur_usd_rate
        return price, "OKX (USD/EUR)"
  except Exception as e:
    print(f"  [Prix Warning] Échec OKX pour {sym}: {e}")

  # 2. Secours via Kraken Public
  try:
    pair_kraken = f"X{clean_sym}ZEUR" if clean_sym in ["BTC", "ETH"] else f"{clean_sym}EUR"
    res_k = requests.get(
        f"https://api.kraken.com/0/public/Ticker?pair={pair_kraken}", timeout=3
    )
    if res_k.status_code == 200:
      result_k = res_k.json().get("result", {})
      for _, v_data in result_k.items():
        if "c" in v_data:
          price = float(v_data["c"][0])
          return price, "Kraken (EUR Direct)"
  except Exception as e:
    print(f"  [Prix Warning] Échec Kraken pour {sym}: {e}")

  return price, "ERREUR PRIX"


def run_majors_audit():
  print("=" * 70)
  print("🔍 AUDIT DÉTAILLÉ DES CRYPTOS MAJEURES (BTC & ETH)")
  print("=" * 70)

  # 1. Vérification des adresses Exodus dans .env
  btc_addr = os.getenv("EXODUS_BTC_ADDRESS")
  eth_addr = os.getenv("EXODUS_ETH_ADDRESS")

  print("\n--- 1. CONFIGURATION EXODUS WALLETS (.env) ---")
  print(f"• EXODUS_BTC_ADDRESS : {btc_addr if btc_addr else '❌ NON CONFIGURÉE'}")
  print(f"• EXODUS_ETH_ADDRESS : {eth_addr if eth_addr else '❌ NON CONFIGURÉE'}")

  # 2. Interrogation individuelle des sources
  print("\n--- 2. INTERROGATION DES CONNECTEURS EN DIRECT ---")

  exodus_bal = get_exodus_wallet_balances()
  kraken_bal = get_kraken_crypto_balances()
  okx_bal = get_okx_crypto_balances()
  mexc_bal = get_mexc_crypto_balances()

  sources = [
      ("Exodus Cold Wallet", exodus_bal),
      ("Kraken Exchange", kraken_bal),
      ("OKX Exchange", okx_bal),
      ("MEXC Exchange", mexc_bal),
  ]

  detailed_rows = []
  total_majors_eur = 0.0

  # Récupération des prix actuels de référence
  btc_price, btc_src = fetch_live_price_debug("BTC")
  eth_price, eth_src = fetch_live_price_debug("ETH")

  print(f"\n📈 Cours Live de Référence (Taux EUR/USD = {eur_usd_rate}) :")
  print(f"  • BTC : {btc_price:,.2f} € (Source: {btc_src})")
  print(f"  • ETH : {eth_price:,.2f} € (Source: {eth_src})")

  print("\n--- 3. DÉTAIL DES LIGNES BTC / ETH PAR SOURCE ---")

  for source_name, bal_dict in sources:
    for asset in ["BTC", "ETH"]:
      info = bal_dict.get(asset)
      if info:
        qty = float(info.get("amount", 0.0))
        unit_price = btc_price if asset == "BTC" else eth_price
        total_val = qty * (unit_price or 0.0)
        total_majors_eur += total_val

        detailed_rows.append({
            "Source": source_name,
            "Actif": asset,
            "Quantité": qty,
            "Prix Unitaire": unit_price,
            "Valeur (€)": total_val,
        })
        print(
            f"  [OK] {source_name:20s} | {asset:3s} | Qte: {qty:10.6f} |"
            f" Val: {total_val:10.2f} €"
        )
      else:
        print(f"  [  ] {source_name:20s} | {asset:3s} | Qte: 0.000000 | Val:"
              " 0.00 €")

  print("\n" + "=" * 70)
  print(f"💰 SOMME TOTALE CALCULÉE (MAJEURES BTC + ETH) : {total_majors_eur:,.2f} €")
  print("=" * 70)


if __name__ == "__main__":
  run_majors_audit()
