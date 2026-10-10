import logging
from db_manager import get_open_positions
from exchange_connectors import (
    get_kraken_crypto_balances,
    get_okx_crypto_balances,
    get_mexc_crypto_balances,
)

def audit_database_vs_exchanges():
  """Compare les positions 'OPEN' en BDD avec les avoirs réels de l'exchange exact indiqué en BDD."""
  open_positions = get_open_positions()
  
  # Récupération séparée par exchange pour éviter les télescopages de doublons inter-plateformes
  kraken_balances = get_kraken_crypto_balances()
  okx_balances = get_okx_crypto_balances()
  mexc_balances = get_mexc_crypto_balances()

  discrepancies = []

  for p in open_positions:
    pos_id = p[0]
    sym = str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
    exch = str(p[2]).upper().strip()
    db_amount_eur = float(p[4]) if p[4] is not None else 0.0
    entry_price = float(p[3]) if p[3] is not None else 0.0

    db_qty = (db_amount_eur / entry_price) if entry_price > 0 else 0.0

    # Sélection du dictionnaire de l'exchange correspondant à la BDD
    target_exchange_dict = {}
    if "KRAKEN" in exch:
      target_exchange_dict = kraken_balances
    elif "OKX" in exch:
      target_exchange_dict = okx_balances
    elif "MEXC" in exch:
      target_exchange_dict = mexc_balances

    # Recherche du solde réel sur CET exchange précis
    real_asset_info = target_exchange_dict.get(sym, {})
    real_qty = float(real_asset_info.get("amount", -1.0))

    # 1. Ligne fantôme (absente ou nulle sur cet exchange spécifique)
    if real_qty == 0.0 or (real_qty == -1.0 and sym not in ["BTC", "ETH"]):
      discrepancies.append({
          "type": "GHOST_POSITION",
          "id": pos_id,
          "symbol": sym,
          "exchange": exch,
          "db_amount": db_amount_eur,
          "real_amount": 0.0,
          "desc": f"Ligne fantôme détectée : {sym} ({exch}) est actif en BDD ({db_amount_eur:,.2f} €) mais le solde réel sur {exch} est de 0."
      })
    elif real_qty > 0.0 and db_qty > 0.0:
      # 2. Écart de volume sur le même exchange (> 30% d'écart)
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
            "desc": f"Écart de volume sur {sym} ({exch}) : BDD = {db_qty:,.2f} tokens vs Réel {exch} = {real_qty:,.2f} tokens ({diff_ratio*100:.1f}% d'écart)."
        })

  return discrepancies


if __name__ == "__main__":
  import logging
  logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
  
  print("🔍 Lancement de l'audit de réconciliation BDD vs Exchanges...")
  discrepancies = audit_database_vs_exchanges()
  
  if discrepancies:
    print(f"\n⚠️ {len(discrepancies)} écart(s) ou anomalie(s) détecté(s) :")
    for d in discrepancies:
      print(f"  • [{d['type']}] {d['symbol']} sur {d['exchange']} (ID BDD: {d['id']})")
      print(f"    -> {d['desc']}\n")
  else:
    print("\n✅ Parfaite synchronisation : aucun écart détecté entre la BDD et les plateformes !")
