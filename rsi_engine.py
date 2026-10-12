import pandas as pd
import requests


def calculate_rsi(series: pd.Series, period: int = 14) -> float:
  """Calcule le RSI (Relative Strength Index) standard sur 14 périodes."""
  if len(series) < period + 1:
    return 50.0

  delta = series.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

  # Évite la division par zéro
  loss_val = loss.iloc[-1]
  gain_val = gain.iloc[-1]

  if loss_val == 0:
    return 100.0 if gain_val > 0 else 50.0

  rs = gain_val / loss_val
  rsi = 100 - (100 / (1 + rs))
  return round(float(rsi), 1)


def fetch_okx_klines(symbol: str, timeframe: str = "1h", limit: int = 60):
  """Récupère les bougies de prix en direct via l'API publique OKX."""
  tf_map = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
  bar = tf_map.get(timeframe, "1H")
  clean_sym = symbol.upper().replace("USDT", "").replace("EUR", "").strip()

  url = f"https://www.okx.com/api/v5/market/candles?instId={clean_sym}-USDT&bar={bar}&limit={limit}"
  try:
    res = requests.get(url, timeout=2.0)
    if res.status_code == 200:
      data = res.json().get("data", [])
      if data:
        # OKX renvoie [ts, open, high, low, close, ...] du plus récent au plus ancien
        closes = [float(row[4]) for row in reversed(data)]
        return pd.Series(closes)
  except Exception:
    pass
  return pd.Series(dtype=float)


def get_multi_tf_rsi(symbol: str) -> dict:
  """Calcule le RSI d'un token sur les 4 unités de temps (15m, 1h, 4h, 1d)."""
  timeframes = ["15m", "1h", "4h", "1d"]
  rsi_results = {}
  for tf in timeframes:
    closes = fetch_okx_klines(symbol, timeframe=tf)
    rsi_results[tf] = calculate_rsi(closes) if not closes.empty else 50.0
  return rsi_results


def build_rsi_heatmap_dataframe(symbols: list) -> pd.DataFrame:
  """Génère la matrice Heatmap complète pour une liste de symboles."""
  records = []
  for sym in symbols:
    clean_sym = sym.upper().replace("USDT", "").replace("EUR", "").strip()
    if clean_sym in ["USDT", "USDC", "EUR", "USD"]:
      continue
    data = get_multi_tf_rsi(clean_sym)
    data["Actif"] = clean_sym

    # Diagnostic rapide de survente / surachat
    rsi_15m, rsi_4h = data["15m"], data["4h"]
    if rsi_15m <= 30 and rsi_4h <= 40:
      signal = "🟢 SURVENDU (Opportunité Rebond)"
    elif rsi_15m >= 70 or rsi_4h >= 75:
      signal = "🔴 SURACHAT (Risque de Prise de Profit)"
    else:
      signal = "⚪ NEUTRE"

    data["Signal"] = signal
    records.append(data)

  df = pd.DataFrame(records)
  if not df.empty:
    cols = ["Actif", "15m", "1h", "4h", "1d", "Signal"]
    df = df[cols]
  return df
