import pandas as pd
import requests


def fetch_okx_funding_and_oi(symbol: str) -> dict:
  """Récupère le Funding Rate et l'Open Interest pour un contrat SWAP USDT OKX."""
  clean_sym = symbol.upper().replace("USDT", "").replace("EUR", "").strip()
  inst_id = f"{clean_sym}-USDT-SWAP"

  funding_rate = None
  open_interest_usd = None

  # 1. Récupération du Funding Rate
  try:
    url_funding = (
        f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"
    )
    res = requests.get(url_funding, timeout=2.0)
    if res.status_code == 200:
      data = res.json().get("data", [])
      if data:
        funding_rate = (
            float(data[0].get("fundingRate", 0.0)) * 100.0
        )  # Conversion en %
  except Exception:
    pass

  # 2. Récupération de l'Open Interest
  try:
    url_oi = f"https://www.okx.com/api/v5/public/open-interest?instId={inst_id}"
    res = requests.get(url_oi, timeout=2.0)
    if res.status_code == 200:
      data = res.json().get("data", [])
      if data:
        open_interest_usd = float(
            data[0].get("oiCcy", 0.0)
        )  # OI exprimé en devise/USD
  except Exception:
    pass

  # Diagnostic de déséquilibre de marché (Squeeze Potential)
  if funding_rate is not None:
    if funding_rate >= 0.03:
      bias = "🚨 SURCHAUFFE LONG (Risque Chasse aux Stops)"
    elif funding_rate <= -0.03:
      bias = "⚡ ACCUMULATION SHORT (Potentiel Short Squeeze)"
    else:
      bias = "⚪ NEUTRE"
  else:
    bias = "⚪ INDISPONIBLE"

  return {
      "Actif": clean_sym,
      "Funding (%)": (
          round(funding_rate, 4) if funding_rate is not None else 0.0
      ),
      "OI ($)": open_interest_usd or 0.0,
      "Biais": bias,
  }


def build_funding_oi_dataframe(symbols: list) -> pd.DataFrame:
  """Génère un tableau récapitulatif pour une liste de symboles."""
  records = []
  for sym in symbols:
    clean_sym = sym.upper().replace("USDT", "").replace("EUR", "").strip()
    if clean_sym in ["USDT", "USDC", "EUR", "USD"]:
      continue
    records.append(fetch_okx_funding_and_oi(clean_sym))

  df = pd.DataFrame(records)
  return df
