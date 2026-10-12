import pandas as pd
import requests


def fetch_okx_funding_and_oi(symbol: str) -> dict:
  """Récupère le Funding Rate et l'Open Interest pour un contrat SWAP USDT OKX."""
  clean_sym = symbol.upper().replace("USDT", "").replace("EUR", "").strip()
  inst_id = f"{clean_sym}-USDT-SWAP"

  funding_rate = None
  open_interest_usd = None

  # 1. Récupération du Funding Rate (essai eea.okx.com puis www.okx.com)
  for domain in ["eea.okx.com", "www.okx.com"]:
    try:
      url_funding = (
          f"https://{domain}/api/v5/public/funding-rate?instId={inst_id}"
      )
      res = requests.get(url_funding, timeout=2.0)
      if res.status_code == 200:
        data = res.json().get("data", [])
        if data:
          funding_rate = (
              float(data[0].get("fundingRate", 0.0)) * 100.0
          )  # Conversion en %
          break
    except Exception:
      continue

  # 2. Récupération de l'Open Interest
  for domain in ["eea.okx.com", "www.okx.com"]:
    try:
      url_oi = f"https://{domain}/api/v5/public/open-interest?instId={inst_id}"
      res = requests.get(url_oi, timeout=2.0)
      if res.status_code == 200:
        data = res.json().get("data", [])
        if data:
          open_interest_usd = float(
              data[0].get("oiCcy", 0.0)
          )  # OI exprimé en devise/USD
          if open_interest_usd == 0:
            open_interest_usd = float(data[0].get("oi", 0.0))
          break
    except Exception:
      continue

  # Diagnostic de déséquilibre de marché
  if funding_rate is not None:
    if funding_rate >= 0.03:
      bias = "🚨 SURCHAUFFE LONG (Risque Long Squeeze)"
    elif funding_rate <= -0.01:
      bias = "🚀 ACCUMULATION SHORT (Potentiel Short Squeeze)"
    else:
      bias = "🟢 NEUTRE / SAIN"
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


def get_funding_oi_summary(symbols: list = None) -> dict:
  """Adaptateur pour le Dashboard Streamlit v3.0."""
  if symbols is None:
    symbols = [
        "BTC",
        "ETH",
        "SOL",
        "NEAR",
        "ONDO",
        "PENDLE",
        "SUI",
        "AVAX",
        "STRK",
        "TAO",
    ]

  df = build_funding_oi_dataframe(symbols)

  if df.empty:
    return {
        "avg_funding_rate": 0.0,
        "total_open_interest_usd": 0.0,
        "overheated_longs_count": 0,
        "short_squeeze_count": 0,
        "pairs": [],
    }

  avg_fr = df["Funding (%)"].mean() if "Funding (%)" in df.columns else 0.0
  total_oi = df["OI ($)"].sum() if "OI ($)" in df.columns else 0.0

  overheated_longs = len(df[df["Funding (%)"] >= 0.03])
  short_squeezes = len(df[df["Funding (%)"] <= -0.01])

  pairs_data = []
  for _, row in df.iterrows():
    fr = float(row["Funding (%)"])
    pairs_data.append({
        "symbol": row["Actif"],
        "exchange": "OKX (SWAP)",
        "funding_rate_8h_pct": fr,
        "funding_rate_annual_pct": fr * 3 * 365.0,
        "open_interest_usd": float(row["OI ($)"]),
        "oi_change_24h_pct": 0.0,
        "bias_status": row["Biais"],
    })

  return {
      "avg_funding_rate": avg_fr,
      "total_open_interest_usd": total_oi,
      "overheated_longs_count": overheated_longs,
      "short_squeeze_count": short_squeezes,
      "pairs": pairs_data,
  }
