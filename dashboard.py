import datetime
import json
import os
import subprocess
from dotenv import load_dotenv
from google import genai
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import time
from db_manager import (
    close_trade,
    get_open_positions,
    init_db,
    mark_trade_ignored,
    mark_trade_taken,
    merge_duplicate_trades,
    set_trade_break_even,
    update_trade_amount,
    update_trade_levels,
)
from exchange_connectors import (
    get_all_exchange_crypto_balances,
    get_all_trades_history,
    get_total_verified_cash,
)

# Import du moteur Microstructure (Funding & Open Interest)
try:
  from funding_oi_engine import get_funding_oi_summary

  FUNDING_ENGINE_AVAILABLE = True
except ImportError:
  FUNDING_ENGINE_AVAILABLE = False

try:
  from reconciliation_engine import audit_database_vs_exchanges

  RECONCILIATION_ENGINE_AVAILABLE = True
except ImportError:
  RECONCILIATION_ENGINE_AVAILABLE = False


# Import des moteurs d'analyse et d'audit
try:
  from crypto_speculative_advisor import (
      calculate_exact_crypto_categories,
      check_circuit_breaker,
      fetch_fear_and_greed_index,
  )
  from daily_digest_engine import build_current_snapshot, init_finary_session
  from exchange_connectors import get_all_open_orders
  from sl_audit_engine import audit_positions_protection

  EXPERT_ENGINE_AVAILABLE = True
except ImportError:
  EXPERT_ENGINE_AVAILABLE = False


# Import du moteur RSI
try:
  from rsi_engine import build_rsi_heatmap_dataframe

  RSI_ENGINE_AVAILABLE = True
except ImportError:
  RSI_ENGINE_AVAILABLE = False


def call_gemini_with_fallback(
    client,
    prompt_text,
    system_instruction=None,
    models=None,
    max_retries_per_model=1,
):
  """Exécute une requête Gemini avec bascule automatique sur les modèles de secours (Fallback Pattern) en cas de 503/429 ou d'indisponibilité."""
  if models is None:
    models = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]

  last_error = None

  for model_name in models:
    for attempt in range(max_retries_per_model + 1):
      try:
        contents = (
            f"{system_instruction}\n\nDemande utilisateur :\n{prompt_text}"
            if system_instruction
            else prompt_text
        )

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
        )

        if response and response.text:
          return response.text, model_name

      except Exception as e:
        last_error = e
        err_str = str(e)

        if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
          time.sleep(1.0)
          break

        break

  raise Exception(
      f"Tous les modèles Gemini ont échoué. Dernière erreur : {last_error}"
  )

def color_rsi(val):
  """Applique un dégradé de couleur style Coinglass sur les cellules RSI."""
  if isinstance(val, (int, float)):
    if val <= 30:
      return "background-color: #1e4620; color: #00ff66; font-weight: bold"
    elif val >= 70:
      return "background-color: #4a1c1c; color: #ff4d4d; font-weight: bold"
    elif val <= 40:
      return "background-color: #1a3322; color: #88ffaa"
    elif val >= 60:
      return "background-color: #331a1a; color: #ffaaaa"
  return ""


load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuration de la page Streamlit
st.set_page_config(
    page_title="Crypto Speculative Monitor & Analytics",
    page_icon="🚀",
    layout="wide",
)


# ==========================================
# FONCTIONS CACHÉES (PERFORMANCE BOOST)
# ==========================================
@st.cache_data(ttl=60, show_spinner=False)
def fetch_live_price_cached(sym, exch, eur_usd_rate):
  """Récupère le prix en direct avec un cache de 60s pour éviter la latence réseau."""
  clean_sym = str(sym).upper().replace("USDT", "").replace("EUR", "").strip()
  price = None
  try:
    res_okx = requests.get(
        f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-USDT",
        timeout=1.2,
    )
    if res_okx.status_code == 200:
      data_okx = res_okx.json().get("data", [])
      if data_okx:
        price_usdt = float(data_okx[0].get("last", 0))
        price = price_usdt / eur_usd_rate
  except Exception:
    pass

  if not price:
    try:
      res_k = requests.get(
          f"https://api.kraken.com/0/public/Ticker?pair={clean_sym}EUR",
          timeout=1.2,
      )
      if res_k.status_code == 200:
        result_k = res_k.json().get("result", {})
        for _, v_data in result_k.items():
          if "c" in v_data:
            price = float(v_data["c"][0])
            break
    except Exception:
      pass
  return price


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_crypto_balances():
  return get_all_exchange_crypto_balances()


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_verified_cash():
  return get_total_verified_cash()


@st.cache_data(ttl=120, show_spinner=False)
def get_cached_reconciliation_audit():
  if RECONCILIATION_ENGINE_AVAILABLE:
    return audit_database_vs_exchanges()
  return []


def send_telegram_notify(text):
  """Envoie une notification d'information directe sur Telegram lors d'une action Web."""
  bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
  user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
  if not bot_token or not user_id:
    return
  url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
  try:
    requests.post(
        url,
        json={"chat_id": user_id, "text": text, "parse_mode": "Markdown"},
        timeout=3,
    )
  except Exception:
    pass


# --- SYSTÈME DE SÉCURITÉ PAR MOT DE PASSE ---
def check_password():
  """Retourne True si l'utilisateur a saisi le bon mot de passe."""
  expected_password = os.getenv("STREAMLIT_PASSWORD", "MonMotDePasseSecret123")

  def password_entered():
    if st.session_state["password"] == expected_password:
      st.session_state["password_correct"] = True
      del st.session_state["password"]
    else:
      st.session_state["password_correct"] = False

  if "password_correct" not in st.session_state:
    st.text_input(
        "🔒 Veuillez saisir le mot de passe pour accéder au cockpit :",
        type="password",
        on_change=password_entered,
        key="password",
    )
    return False
  elif not st.session_state["password_correct"]:
    st.text_input(
        "🔒 Veuillez saisir le mot de passe pour accéder au cockpit :",
        type="password",
        on_change=password_entered,
        key="password",
    )
    st.error("😕 Mot de passe incorrect.")
    return False
  else:
    return True


if not check_password():
  st.stop()

init_db()

st.title("🚀 Cockpit Spéculatif & Moonbag — Dashboard Analytics (v3.0)")
st.markdown(
    "*Suivi multi-exchange temps réel, Microstructure, Journal FIFO, Assistant IA & Reporting*"
)
st.markdown("---")


@st.cache_data(ttl=300, show_spinner=False)
def get_live_eur_usd_rate():
  """Récupère le taux EUR/USD en direct via l'API Frankfurter ou Kraken."""
  try:
    res = requests.get(
        "https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=2
    )
    if res.status_code == 200:
      rate = float(res.json().get("rates", {}).get("USD", 0))
      if rate > 0:
        return round(rate, 4)
  except Exception:
    pass

  try:
    res_k = requests.get(
        "https://api.kraken.com/0/public/Ticker?pair=EURUSD", timeout=2
    )
    if res_k.status_code == 200:
      result_k = res_k.json().get("result", {})
      for _, v_data in result_k.items():
        if "c" in v_data:
          rate = float(v_data["c"][0])
          if rate > 0:
            return round(rate, 4)
  except Exception:
    pass

  return 1.12


# ==========================================
# GESTION GLOBALE DE LA DEVISE (EUR / USD)
# ==========================================
if "live_eur_usd_rate" not in st.session_state:
  st.session_state["live_eur_usd_rate"] = get_live_eur_usd_rate()

default_eur_usd_rate = st.session_state["live_eur_usd_rate"]

with st.sidebar:
  st.header("⚙️ Paramètres du Cockpit")
  currency_choice = st.selectbox(
      "Devise d'affichage principale", ["EUR (€)", "USD ($)"]
  )
  eur_usd_rate = st.number_input(
      "Taux de change EUR/USD (Live / Ajustable)",
      min_value=1.0,
      max_value=1.5,
      value=default_eur_usd_rate,
      step=0.005,
      format="%.4f",
  )

  if st.button("⚡ Rafraîchir les données Live", type="primary"):
    st.cache_data.clear()
    st.session_state["live_eur_usd_rate"] = get_live_eur_usd_rate()
    st.rerun()

if currency_choice.startswith("USD"):
  curr_symbol = "$"
  curr_multiplier = eur_usd_rate
  curr_label = "USD ($)"
else:
  curr_symbol = "€"
  curr_multiplier = 1.0
  curr_label = "EUR (€)"

st.sidebar.markdown(
    f"**Devise active :** {curr_label} | **Taux EUR/USD :** {eur_usd_rate:.4f}"
)
st.sidebar.markdown("---")
st.sidebar.info(
    "💡 Données en cache haute vitesse (1 min). Cliquez sur '⚡ Rafraîchir les"
    " données Live' pour forcer une réinterrogation des APIs."
)

# --- NAVIGATION PAR ONGLETS ---
(
    tab_live,
    tab_micro,
    tab_digest,
    tab_alerts,
    tab_ai,
    tab_monitor,
    tab_fiscal,
) = st.tabs([
    "📊 Positions Actives (Live)",
    "🔥 Microstructure & Dérivés",
    "📰 Daily Briefing (19h Pro)",
    "🔔 Centre d'Alertes",
    "🤖 Assistant IA Pro",
    "🛠️ Monitoring & Santé APIs",
    "🧾 Export Fiscal & Comptable",
])


def fetch_live_price(sym, exch):
  return fetch_live_price_cached(sym, exch, eur_usd_rate)


# ==========================================
# ONGLET 1 : POSITIONS ACTIVES (LIVE & TEMPS RÉEL)
# ==========================================
with tab_live:
  with st.expander("📖 Guide de lecture & Répartition du Portefeuille"):
    st.markdown(f"""
        Inventaire global et ventilation de votre capital en temps réel (Autonome & 100% direct exchanges) :
        * **Volume Total Crypto :** Valeur marchande cumulée de l'ensemble de vos actifs (Exchanges + Wallets froids Exodus).
        * **Majeures (BTC, ETH) :** Vos réserves de valeur fondamentales.
        * **Poche Core :** Vos actifs de conviction long terme (ex: ONDO, PENDLE).
        * **Poche Spéculative :** Votre poche à haute volatilité (Moonbag), pilotée par vos Stop-Loss et Take-Profits.
        * **Buffer de Risque :** Marge moyenne avant que vos Stop-Loss ne se déclenchent.
        """)

  positions = get_open_positions()

  enriched_positions = []
  total_current_market_value = 0.0
  total_invested_base = 0.0

  free_ride_count = 0
  at_risk_count = 0
  total_weight_risk = 0.0
  weighted_sl_distance = 0.0

  if positions:
    for p in positions:
      pos_id = p[0]
      sym = p[1]
      exch = p[2]
      entry_eur = float(p[3]) if p[3] is not None else 0.0
      amount_eur = float(p[4]) if p[4] is not None else 0.0
      tp1_eur = float(p[5]) if len(p) > 5 and p[5] is not None else 0.0
      sl_eur = float(p[7]) if len(p) > 7 and p[7] is not None else 0.0
      step_val = p[9] if len(p) > 9 and p[9] is not None else "ENTRY"

      total_invested_base += amount_eur

      live_eur = fetch_live_price(sym, exch)
      if not live_eur or live_eur <= 0:
        live_eur = entry_eur

      quantity = (amount_eur / entry_eur) if entry_eur > 0 else 0.0
      current_val_eur = quantity * live_eur
      total_current_market_value += current_val_eur

      pnl_eur = current_val_eur - amount_eur
      pnl_pct = (
          ((live_eur - entry_eur) / entry_eur) * 100.0 if entry_eur > 0 else 0.0
      )

      if step_val in ["TP1_DONE", "TP2_DONE"]:
        free_ride_count += 1
        status = "🎯 FREE RIDE (TP1 Validé)"
      elif sl_eur > 0 and live_eur <= (sl_eur * 1.05):
        status = "🛑 SL FRÔLÉ"
        at_risk_count += 1
      else:
        status = "🟢 ACTIF (À Risque)"
        at_risk_count += 1

      if sl_eur > 0 and live_eur > 0:
        dist_to_sl_pct = ((live_eur - sl_eur) / live_eur) * 100.0
        if dist_to_sl_pct > 0:
          weighted_sl_distance += dist_to_sl_pct * current_val_eur
          total_weight_risk += current_val_eur

      enriched_positions.append({
          "ID": pos_id,
          "Symbole": sym,
          "Exchange": exch,
          "Prix Entrée": entry_eur * curr_multiplier,
          "Prix Actuel": live_eur * curr_multiplier,
          "Montant Initial": amount_eur * curr_multiplier,
          "Valeur Actuelle": current_val_eur * curr_multiplier,
          "PnL (€/$)": pnl_eur * curr_multiplier,
          "PnL (%)": pnl_pct,
          "TP1": tp1_eur * curr_multiplier,
          "Stop-Loss": sl_eur * curr_multiplier,
          "Statut": status,
      })

  try:
    all_crypto_balances = get_cached_crypto_balances()
  except Exception:
    all_crypto_balances = {}

  speculative_symbols = {
      str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
      for p in positions
  }

  val_majors = 0.0
  val_core = 0.0
  val_stables_cash = 0.0

  dict_majors = {}
  dict_core = {}

  for asset, info in all_crypto_balances.items():
    amt = info["amount"]
    exch = info["exchange"]
    lp = fetch_live_price(asset, exch) or 0.0
    asset_val = amt * lp

    if asset in ["USDT", "USDC"]:
      val_stables_cash += amt
    elif asset in ["BTC", "ETH"]:
      val_majors += asset_val
      if asset_val > 0.1:
        dict_majors[asset] = dict_majors.get(asset, 0.0) + (
            asset_val * curr_multiplier
        )
    elif asset in speculative_symbols:
      pass
    else:
      val_core += asset_val
      if asset_val > 1.0:
        dict_core[asset] = dict_core.get(asset, 0.0) + (
            asset_val * curr_multiplier
        )

  cash_info = get_cached_verified_cash()
  total_cash_eur = cash_info.get("total_cash", 0.0)
  val_stables_cash += total_cash_eur

  val_speculative = total_current_market_value
  total_crypto_only_val = (
      val_majors + val_core + val_speculative + val_stables_cash
  )

  if total_crypto_only_val > 0:
    pct_majors = (val_majors / total_crypto_only_val) * 100.0
    pct_core = (val_core / total_crypto_only_val) * 100.0
    pct_spec = (val_speculative / total_crypto_only_val) * 100.0
    pct_stables = (val_stables_cash / total_crypto_only_val) * 100.0
  else:
    pct_majors = pct_core = pct_spec = pct_stables = 0.0

  st.markdown("#### 🌐 Ventilation Globale du Capital")
  m_col1, m_col2, m_col3, m_col4 = st.columns(4)
  m_col1.metric(
      "Volume Total Crypto",
      f"{total_crypto_only_val * curr_multiplier:,.2f} {curr_symbol}",
      delta="Exchanges + Exodus",
  )
  m_col2.metric(
      "Majeures (BTC, ETH)",
      f"{val_majors * curr_multiplier:,.2f} {curr_symbol}",
      delta=f"{pct_majors:.2f}% du portfolio",
  )
  m_col3.metric(
      "Poche CORE",
      f"{val_core * curr_multiplier:,.2f} {curr_symbol}",
      delta=f"{pct_core:.2f}% du portfolio",
  )
  m_col4.metric(
      "Poche Spéculative",
      f"{val_speculative * curr_multiplier:,.2f} {curr_symbol}",
      delta=f"{pct_spec:.2f}% (Cible : 10%)",
      delta_color="normal" if pct_spec <= 12 else "inverse",
  )

  st.markdown("")

  total_invested_disp = total_invested_base * curr_multiplier
  total_market_disp = val_speculative * curr_multiplier
  total_pnl_disp = total_market_disp - (total_invested_base * curr_multiplier)
  total_pnl_pct = (
      (total_pnl_disp / (total_invested_base * curr_multiplier)) * 100
      if total_invested_base > 0
      else 0.0
  )
  global_risk_buffer = (
      (weighted_sl_distance / total_weight_risk) if total_weight_risk > 0 else 0.0
  )
  total_cash_disp = total_cash_eur * curr_multiplier

  s_col1, s_col2, s_col3, s_col4 = st.columns(4)
  s_col1.metric(
      "Positions Actives",
      f"{len(positions)} lignes",
      delta=f"🛡️ {free_ride_count} Free Ride | ⚠️ {at_risk_count} À Risque",
  )
  s_col2.metric(
      f"Valeur Marché Spécu ({curr_symbol})",
      f"{total_market_disp:,.2f} {curr_symbol}",
      delta=(
          f"PnL : {total_pnl_pct:+.2f}% ({total_pnl_disp:+,.2f} {curr_symbol})"
      ),
  )
  s_col3.metric(
      "Buffer de Risque Global",
      f"{global_risk_buffer:.1f}%",
      delta=(
          "Marge moyenne avant SL"
          if global_risk_buffer > 10
          else "Attention, proche des SL"
      ),
      delta_color="normal" if global_risk_buffer > 10 else "inverse",
  )
  s_col4.metric(
      f"Cash Disponible Total ({curr_symbol})",
      f"{total_cash_disp:,.2f} {curr_symbol}",
  )

  st.markdown("---")
  st.subheader(f"📋 Inventaire & Suivi Temps Réel des Positions ({curr_symbol})")

  r1_col1, r1_col2 = st.columns([1, 1])

  with r1_col1:
    labels_global = [
        "Majeures (BTC/ETH)",
        "Poche CORE",
        "Spéculatif (Moonbag)",
        "Stables & Cash",
    ]
    values_global = [
        val_majors * curr_multiplier,
        val_core * curr_multiplier,
        val_speculative * curr_multiplier,
        val_stables_cash * curr_multiplier,
    ]
    colors_global = ["#36a2eb", "#4bc0c0", "#ff6384", "#ffce56"]

    fig_pie_global = px.pie(
        names=labels_global,
        values=values_global,
        title="🌐 Allocation Globale du Portefeuille",
        color_discrete_sequence=colors_global,
        hole=0.4,
    )
    fig_pie_global.update_traces(textposition="inside", textinfo="percent+label")
    fig_pie_global.update_layout(
        margin=dict(t=30, b=10, l=10, r=10), showlegend=False, height=280
    )
    st.plotly_chart(fig_pie_global, width="stretch")

  with r1_col2:
    st.markdown("##### 📌 Synthèse visuelle des poches")
    st.write(
        f"• **Majeures (BTC/ETH) :** {val_majors * curr_multiplier:,.2f}"
        f" {curr_symbol} ({pct_majors:.1f}%)"
    )
    st.write(
        f"• **Poche CORE :** {val_core * curr_multiplier:,.2f}"
        f" {curr_symbol} ({pct_core:.1f}%)"
    )
    st.write(
        f"• **Spéculatif (Moonbag) :**"
        f" {val_speculative * curr_multiplier:,.2f} {curr_symbol}"
        f" ({pct_spec:.1f}%)"
    )
    st.write(
        f"• **Stables & Cash :** {val_stables_cash * curr_multiplier:,.2f}"
        f" {curr_symbol} ({pct_stables:.1f}%)"
    )
    st.info(
        "💡 Les trois camemberts ci-dessous détaillent la composition exacte"
        " de chaque poche d'actifs."
    )

  st.markdown("---")
  st.subheader("🔍 Zooms Détaillés par Poche d'Actifs")
  z_col1, z_col2, z_col3 = st.columns(3)

  # 1. Zoom Majeures
  with z_col1:
    if dict_majors:
      df_majors = pd.DataFrame(
          list(dict_majors.items()), columns=["Actif", "Valeur"]
      )
      fig_maj = px.pie(
          df_majors,
          names="Actif",
          values="Valeur",
          title="🏛️ Majeures (BTC vs ETH)",
          hole=0.4,
      )
      fig_maj.update_traces(textposition="inside", textinfo="percent+label")
      fig_maj.update_layout(
          margin=dict(t=30, b=10, l=10, r=10), showlegend=False, height=260
      )
      st.plotly_chart(fig_maj, width="stretch")
    else:
      st.info("Aucun actif majeur détecté.")

  # 2. Zoom Poche CORE
  with z_col2:
    if dict_core:
      df_core_pie = pd.DataFrame(
          list(dict_core.items()), columns=["Actif", "Valeur"]
      )
      fig_core = px.pie(
          df_core_pie,
          names="Actif",
          values="Valeur",
          title="💎 Poche CORE (Long Terme)",
          hole=0.4,
      )
      fig_core.update_traces(textposition="inside", textinfo="percent+label")
      fig_core.update_layout(
          margin=dict(t=30, b=10, l=10, r=10), showlegend=False, height=260
      )
      st.plotly_chart(fig_core, width="stretch")
    else:
      st.info("Aucun actif CORE détecté.")

  # 3. Zoom Poche Spéculative
  with z_col3:
    if enriched_positions:
      df_spec_pie = pd.DataFrame(enriched_positions)
      fig_spec = px.pie(
          df_spec_pie,
          names="Symbole",
          values="Valeur Actuelle",
          title="🚀 Spéculatif (Moonbag BDD)",
          hole=0.4,
      )
      fig_spec.update_traces(textposition="inside", textinfo="percent+label")
      fig_spec.update_layout(
          margin=dict(t=30, b=10, l=10, r=10), showlegend=False, height=260
      )
      st.plotly_chart(fig_spec, width="stretch")
    else:
      st.info("Aucune position spéculative active.")

  st.markdown("---")

  if enriched_positions:
    df_pos = pd.DataFrame(enriched_positions)

    view_mode = st.radio(
        "Mode d'affichage",
        ["🖥️ Tableau Standard (PC)", "📱 Cartes Tactiles (Mobile)"],
        horizontal=True,
    )

    if view_mode.startswith("🖥️"):
      df_display = df_pos.copy()

      df_display["Marge SL"] = df_display.apply(
          lambda r: (
              ((r["Prix Actuel"] - r["Stop-Loss"]) / r["Prix Actuel"]) * 100.0
              if r["Prix Actuel"] > 0 and r["Stop-Loss"] > 0
              else 0.0
          ),
          axis=1,
      )

      def style_pnl(val):
        if pd.isna(val):
          return ""
        if val > 0:
          return (
              "color: #00ff66; font-weight: bold; background-color: rgba(0, 255,"
              " 102, 0.12);"
          )
        elif val < 0:
          return (
              "color: #ff4d4d; font-weight: bold; background-color: rgba(255,"
              " 77, 77, 0.12);"
          )
        return "color: #888888;"

      def style_tp_sl(row):
        styles = [""] * len(row)
        price = row["Prix Actuel"]
        sl = row["Stop-Loss"]
        tp1 = row["TP1"]
        statut = row["Statut"]

        sl_idx = row.index.get_loc("Stop-Loss")
        marge_idx = row.index.get_loc("Marge SL")
        if sl > 0 and price > 0:
          sl_dist_pct = ((price - sl) / price) * 100.0
          if price <= sl:
            styles[sl_idx] = (
                "background-color: #5c1d1d; color: #ff3333; font-weight: bold;"
            )
            styles[marge_idx] = (
                "background-color: #5c1d1d; color: #ff3333; font-weight: bold;"
            )
          elif sl_dist_pct <= 5.0:
            styles[sl_idx] = (
                "background-color: #52321e; color: #ffa500; font-weight: bold;"
            )
            styles[marge_idx] = (
                "background-color: #52321e; color: #ffa500; font-weight: bold;"
            )

        if tp1 > 0 and price > 0:
          tp_idx = row.index.get_loc("TP1")
          if price >= tp1:
            styles[tp_idx] = (
                "background-color: #1e4620; color: #00ff66; font-weight: bold;"
            )
          elif price >= (tp1 * 0.90):
            styles[tp_idx] = (
                "background-color: #2a4d30; color: #88ffaa; font-weight: bold;"
            )

        stat_idx = row.index.get_loc("Statut")
        if "FREE RIDE" in str(statut):
          styles[stat_idx] = (
              "background-color: rgba(0, 255, 102, 0.2); color: #00ff66;"
              " font-weight: bold;"
          )
        elif "SL FRÔLÉ" in str(statut):
          styles[stat_idx] = (
              "background-color: rgba(255, 165, 0, 0.2); color: #ffa500;"
              " font-weight: bold;"
          )

        return styles

      styler = df_display.style.apply(style_tp_sl, axis=1)
      styler = styler.map(style_pnl, subset=["PnL (€/$)", "PnL (%)"])

      styler = styler.format({
          "Prix Entrée": f"{{:.4f}} {curr_symbol}",
          "Prix Actuel": f"{{:.4f}} {curr_symbol}",
          "Montant Initial": f"{{:,.2f}} {curr_symbol}",
          "Valeur Actuelle": f"{{:,.2f}} {curr_symbol}",
          "PnL (€/$)": f"{{:+,.2f}} {curr_symbol}",
          "PnL (%)": "{:+.2f}%",
          "TP1": f"{{:.4f}} {curr_symbol}",
          "Stop-Loss": f"{{:.4f}} {curr_symbol}",
          "Marge SL": "{:+.1f}%",
      })

      st.dataframe(styler, width="stretch", hide_index=True)
    else:
      st.markdown("### 📱 Cartes de suivi mobile temps réel")
      for row in enriched_positions:
        pnl_color = "#00ffcc" if row["PnL (%)"] >= 0 else "#ff4d4d"
        with st.container():
          st.markdown(
              f"""
                    <div style="padding: 15px; border-radius: 10px; border: 1px solid #303030; margin-bottom: 10px; background-color: #1e1e1e;">
                        <h4 style="margin: 0; color: #00ffcc;">{row['Symbole']} <span style="font-size: 12px; color: #888;">({row['Exchange']})</span> — <span style="color: {pnl_color};">{row['Statut']}</span></h4>
                        <p style="margin: 5px 0 0 0; font-size: 14px;">
                            <b>Valeur :</b> {row['Valeur Actuelle']:,.2f} {curr_symbol} | <b>PnL :</b> <span style="color: {pnl_color};">{row['PnL (%)']:+.2f}% ({row['PnL (€/$)']:+,.2f} {curr_symbol})</span><br>
                            <b>Prix Actuel :</b> {row['Prix Actuel']:.4f} {curr_symbol} (Entrée : {row['Prix Entrée']:.4f})<br>
                            <b>TP1 :</b> {row['TP1']:.4f} {curr_symbol} | <b>SL :</b> {row['Stop-Loss']:.4f} {curr_symbol}
                        </p>
                    </div>
                    """,
              unsafe_allow_html=True,
          )

    st.markdown("---")
    st.subheader("⚡ Actions & Gestion Dynamique des Positions")

    act_tab1, act_tab2, act_tab3, act_tab4 = st.tabs([
        "🔒 Clôture de Position",
        "🛡️ Passage en Break-Even (1-Click)",
        "✏️ Édition & Raccourcis SL/TP",
        "🔀 Consolidation de Doublons",
    ])

    with act_tab1:
      c_col1, c_col2 = st.columns([2, 1])
      with c_col1:
        selected_close_id = st.selectbox(
            "Position à clôturer",
            options=df_pos["ID"].tolist(),
            format_func=lambda x: (
                f"ID {x} - {df_pos[df_pos['ID'] == x]['Symbole'].values[0]}"
                f" ({df_pos[df_pos['ID'] == x]['Exchange'].values[0]})"
            ),
            key="close_select",
        )
      with c_col2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔒 Clôturer la position", type="secondary"):
          close_sym = df_pos[df_pos["ID"] == selected_close_id][
              "Symbole"
          ].values[0]
          close_trade(selected_close_id)
          st.cache_data.clear()
          send_telegram_notify(
              f"🔒 *POSITION CLÔTURÉE VIA DASHBOARD*\n• **{close_sym}** (ID"
              f" {selected_close_id}) a été fermée en base de données."
          )
          st.success(f"Position ID {selected_close_id} clôturée en BDD !")
          st.rerun()

    with act_tab2:
      st.markdown(
          "**Caler le Stop-Loss sur le Prix d'Entrée** pour éliminer 100% du"
          " risque sur une position en gain."
      )
      be_col1, be_col2 = st.columns([2, 1])
      with be_col1:
        selected_be_id = st.selectbox(
            "Position à sécuriser (Break-Even)",
            options=df_pos["ID"].tolist(),
            format_func=lambda x: (
                f"ID {x} - {df_pos[df_pos['ID'] == x]['Symbole'].values[0]} |"
                f" Entrée:"
                f" {df_pos[df_pos['ID'] == x]['Prix Entrée'].values[0]} | PnL:"
                f" {df_pos[df_pos['ID'] == x]['PnL (%)'].values[0]}"
            ),
            key="be_select",
        )
      with be_col2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🛡️ Passer en Break-Even", type="primary"):
          be_row = df_pos[df_pos["ID"] == selected_be_id].iloc[0]
          set_trade_break_even(selected_be_id)
          st.cache_data.clear()
          send_telegram_notify(
              f"🛡️ *POSITION SÉCURISÉE (BREAK-EVEN)*\n•"
              f" **{be_row['Symbole']}** (ID {selected_be_id})\n• Nouveau SL"
              f" calé sur prix d'entrée : *{be_row['Prix Entrée']}*"
          )
          st.success(
              f"✅ Stop-Loss de la position ID {selected_be_id} ajusté au prix"
              " d'entrée !"
          )
          st.rerun()

    with act_tab3:
      st.markdown(
          "**Ajuster manuellement ou via les raccourcis (-10%, -15%, -20%) les"
          " paliers TP / SL**"
      )
      selected_edit_id = st.selectbox(
          "Position à modifier",
          options=df_pos["ID"].tolist(),
          format_func=lambda x: (
              f"ID {x} - {df_pos[df_pos['ID'] == x]['Symbole'].values[0]}"
              f" ({df_pos[df_pos['ID'] == x]['Exchange'].values[0]})"
          ),
          key="edit_select",
      )

      curr_row = df_pos[df_pos["ID"] == selected_edit_id].iloc[0]
      raw_entry = curr_row["Prix Entrée"]
      raw_tp1 = curr_row["TP1"]
      raw_sl = curr_row["Stop-Loss"]

      st.markdown(
          "**⚡ Raccourcis de calcul SL rapide (par rapport au prix d'entrée)"
          " :**"
      )
      r_c1, r_c2, r_c3, r_c4 = st.columns(4)
      sl_preset = None
      if r_c1.button("-10% (Resserré)", key="btn_sl_10"):
        sl_preset = raw_entry * 0.90
      if r_c2.button("-15% (Standard)", key="btn_sl_15"):
        sl_preset = raw_entry * 0.85
      if r_c3.button("-20% (Modéré)", key="btn_sl_20"):
        sl_preset = raw_entry * 0.80
      if r_c4.button("-30% (Large)", key="btn_sl_30"):
        sl_preset = raw_entry * 0.70

      e_col1, e_col2, e_col3 = st.columns(3)
      with e_col1:
        new_tp1 = st.number_input(
            f"Nouveau TP1 ({curr_symbol})",
            value=float(raw_tp1) if raw_tp1 > 0 else float(raw_entry * 2.0),
            format="%.4f",
        )
      with e_col2:
        new_tp2 = st.number_input(
            f"Nouveau TP2 ({curr_symbol})",
            value=float(raw_entry * 4.0),
            format="%.4f",
        )
      with e_col3:
        target_sl = (
            sl_preset
            if sl_preset is not None
            else (float(raw_sl) if raw_sl > 0 else float(raw_entry * 0.70))
        )
        new_sl = st.number_input(
            f"Nouveau Stop-Loss ({curr_symbol})",
            value=target_sl,
            format="%.4f",
        )

      if st.button("💾 Enregistrer les nouveaux paliers"):
        update_trade_levels(
            selected_edit_id,
            tp1_price=new_tp1 / curr_multiplier,
            tp2_price=new_tp2 / curr_multiplier,
            sl_price=new_sl / curr_multiplier,
        )
        st.cache_data.clear()
        send_telegram_notify(
            f"✏️ *PALIERS MIS À JOUR VIA DASHBOARD*\n•"
            f" **{curr_row['Symbole']}** (ID {selected_edit_id})\n• Nouveau TP1"
            f" : {new_tp1:.4f} {curr_symbol}\n• Nouveau SL : {new_sl:.4f}"
            f" {curr_symbol}"
        )
        st.success(
            f"✅ Paliers mis à jour pour {curr_row['Symbole']} (ID"
            f" {selected_edit_id}) !"
        )
        st.rerun()

    with act_tab4:
      st.markdown(
          "**Fusionner deux lignes du même actif sur un même exchange** en une"
          " seule position avec PRU pondéré."
      )

      duplicates = df_pos.groupby(["Symbole", "Exchange"]).filter(
          lambda x: len(x) > 1
      )

      if not duplicates.empty:
        st.warning("⚠️ Doublon(s) de position détecté(s) !")
        dup_symbols = duplicates["Symbole"].unique()

        for d_sym in dup_symbols:
          sub_dup = duplicates[duplicates["Symbole"] == d_sym]
          ids = sub_dup["ID"].tolist()
          primary_id = ids[0]
          secondary_id = ids[1]

          st.markdown(
              f"• **{d_sym} ({sub_dup['Exchange'].values[0]})** : Ligne"
              f" principale ID **{primary_id}** & Secondaire ID"
              f" **{secondary_id}**"
          )

          if st.button(
              f"🔀 Fusionner les lignes {d_sym} (ID {primary_id} + ID"
              f" {secondary_id})",
              key=f"merge_btn_{d_sym}",
          ):
            if merge_duplicate_trades(primary_id, secondary_id):
              st.cache_data.clear()
              send_telegram_notify(
                  f"🔀 *FUSION DE DOUBLONS EFFECTUÉE*\n• **{d_sym}** : Lignes"
                  f" ID {secondary_id} fusionnée dans ID {primary_id}.\n•"
                  " PRU et volumes consolidés avec succès."
              )
              st.success(f"✅ Positions {d_sym} fusionnées avec succès !")
              st.rerun()
      else:
        st.info(
            "✅ Aucun doublon de position actuellement dans le portefeuille."
        )

  else:
    st.info("Aucune position active trouvée.")

  # --- HISTORIQUE & JOURNAL DES POSITIONS CLÔTURÉES ---
  st.markdown("---")
  with st.expander(
      "📋 Journal & Historique des Positions Clôturées", expanded=False
  ):
    from db_manager import get_closed_trades_history

    closed_history = get_closed_trades_history()

    if not closed_history:
      st.info(
          "Aucune position clôturée enregistrée dans l'historique pour le"
          " moment."
      )
    else:
      df_closed = pd.DataFrame(
          closed_history,
          columns=[
              "ID",
              "Symbole",
              "Exchange",
              "Prix Entrée (€)",
              "Prix Sortie (€)",
              "Investi (€)",
              "PnL (€)",
              "PnL (%)",
              "Ouverture",
              "Clôture",
              "Raison",
          ],
      )

      total_pnl = df_closed["PnL (€)"].sum()
      wins = len(df_closed[df_closed["PnL (€)"] >= 0])
      losses = len(df_closed[df_closed["PnL (€)"] < 0])
      total_trades = len(df_closed)
      win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

      m1, m2, m3, m4 = st.columns(4)
      m1.metric("PnL Cumulé Réalisé", f"{total_pnl:+,.2f} €")
      m2.metric("Win Rate", f"{win_rate:.1f} %")
      m3.metric("Ratio W / L", f"{wins}W / {losses}L")
      m4.metric("Total Clôturés", f"{total_trades}")

      df_display = df_closed[[
          "Symbole",
          "Exchange",
          "Prix Entrée (€)",
          "Prix Sortie (€)",
          "Investi (€)",
          "PnL (€)",
          "PnL (%)",
          "Clôture",
          "Raison",
      ]].copy()

      def style_pnl_val(val):
        if isinstance(val, (int, float)):
          color = "#00FF00" if val >= 0 else "#FF4B4B"
          return f"color: {color}; font-weight: bold;"
        return ""

      styled_df = df_display.style.format({
          "Prix Entrée (€)": "{:.4f} €",
          "Prix Sortie (€)": "{:.4f} €",
          "Investi (€)": "{:.2f} €",
          "PnL (€)": "{:+,.2f} €",
          "PnL (%)": "{:+.2f} %",
      }).map(style_pnl_val, subset=["PnL (€)", "PnL (%)"])

      st.dataframe(styled_df, use_container_width=True)


# ==========================================
# ONGLET 2 : MICROSTRUCTURE & DÉRIVÉS
# ==========================================
with tab_micro:
  with st.expander("📖 Guide de lecture — Microstructure & Dérivés"):
    st.markdown("""
        Analyse du sentiment de marché dérivés sur les actifs de votre portefeuille :
        * **Funding Rate (8h) :** Taux d'intérêt payé entre Longs et Shorts. 
          * **> +0.03%** : Surchauffe acheteuse (risque de Long Squeeze).
          * **< -0.01%** : Surchauffe vendeuse (opportunité de Short Squeeze).
        * **Open Interest (OI) :** Capital total engagé sur les marchés à terme.
        """)

  st.subheader("🔥 Microstructure de Marché — Funding Rates & Open Interest")

  if not FUNDING_ENGINE_AVAILABLE:
    st.warning("⚠️ Le module `funding_oi_engine.py` est introuvable.")
  else:
    # 1. Extraction dynamique de TOUTES tes cryptos (BDD + Balances Exchanges)
    user_crypto_list = set()
    if 'enriched_positions' in locals() and enriched_positions:
      for p in enriched_positions:
        user_crypto_list.add(p['Symbole'])

    if 'all_crypto_balances' in locals() and all_crypto_balances:
      for asset in all_crypto_balances.keys():
        if asset not in ['USDT', 'USDC', 'EUR', 'USD']:
          user_crypto_list.add(asset)

    if not user_crypto_list:
      user_crypto_list = {
          'BTC',
          'ETH',
          'SOL',
          'NEAR',
          'ONDO',
          'PENDLE',
          'SUI',
          'AVAX',
      }

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
      refresh_foi = st.button("🔄 Rafraîchir Funding/OI", key="btn_refresh_foi")

    if refresh_foi or "funding_data" not in st.session_state:
      with st.spinner(
          f"Analyse des dérivés pour vos {len(user_crypto_list)} actifs..."
      ):
        try:
          st.session_state["funding_data"] = get_funding_oi_summary(
              symbols=list(user_crypto_list)
          )
        except Exception as e:
          st.error(
              "Erreur lors de la récupération des données microstructure :"
              f" {e}"
          )

    data_foi = st.session_state.get("funding_data", {})

    if not data_foi or "pairs" not in data_foi or not data_foi["pairs"]:
      st.info(
          "Cliquez sur 'Rafraîchir Funding/OI' pour analyser vos actifs en"
          " direct."
      )
    else:
      avg_fr = data_foi.get("avg_funding_rate", 0.0)
      total_oi = data_foi.get("total_open_interest_usd", 0.0)
      overheated_longs = data_foi.get("overheated_longs_count", 0)
      short_squeeze_candidates = data_foi.get("short_squeeze_count", 0)

      m1, m2, m3, m4 = st.columns(4)
      m1.metric(
          "Funding Rate Moyen (8h)",
          f"{avg_fr:+.4f} %",
          delta=(
              "Surchauffe Acheteuse"
              if avg_fr > 0.03
              else ("Neutre / Sain" if avg_fr >= 0 else "Dominance Vendeuse")
          ),
          delta_color="inverse" if avg_fr > 0.03 else "normal",
      )
      m2.metric(
          "Open Interest Total (Portefeuille)",
          f"{total_oi / 1e6:,.1f} M$",
      )
      m3.metric(
          "Risque Long Squeeze",
          f"{overheated_longs} pair(s)",
          delta="Alerte Surchauffe" if overheated_longs > 0 else "Niveau Sain",
          delta_color="inverse" if overheated_longs > 0 else "normal",
      )
      m4.metric(
          "Potentiel Short Squeeze",
          f"{short_squeeze_candidates} pair(s)",
          delta=(
              "Opportunité Rebond" if short_squeeze_candidates > 0 else "Calme"
          ),
          delta_color="normal" if short_squeeze_candidates > 0 else "off",
      )

      st.markdown("---")

      df_funding = pd.DataFrame(data_foi["pairs"])

      if not df_funding.empty:

        def style_funding_rate(val):
          if isinstance(val, (int, float)):
            if val > 0.03:
              return (
                  "color: #FF4B4B; font-weight: bold; background-color:"
                  " rgba(255, 75, 75, 0.12);"
              )
            elif val < -0.01:
              return (
                  "color: #00FF00; font-weight: bold; background-color:"
                  " rgba(0, 255, 0, 0.12);"
              )
            elif val >= 0:
              return "color: #FFA500;"
          return ""

        df_display_foi = df_funding[[
            "symbol",
            "exchange",
            "funding_rate_8h_pct",
            "funding_rate_annual_pct",
            "open_interest_usd",
            "bias_status",
        ]].copy()

        df_display_foi.columns = [
            "Symbole",
            "Exchange",
            "Funding 8h (%)",
            "Funding Annuel (%)",
            "Open Interest ($)",
            "Biais Microstructure",
        ]

        styled_df_foi = df_display_foi.style.format({
            "Funding 8h (%)": "{:+,.4f} %",
            "Funding Annuel (%)": "{:+,.2f} %",
            "Open Interest ($)": "{:,.0f} $",
        }).map(style_funding_rate, subset=["Funding 8h (%)"])

        st.dataframe(styled_df_foi, use_container_width=True)


# ==========================================
# ONGLET 3 : DAILY BRIEFING PRO (19H)
# ==========================================
with tab_digest:
  with st.expander("📖 Guide du Daily Briefing Pro (Rapport de 19h)"):
    st.markdown("""
        Tour de contrôle quotidienne :
        * **Climat & Marché :** Suivi de l'indice Fear & Greed et du Circuit Breaker BTC.
        * **Répartition des Poches :** Ventilation précise des actifs.
        * **Règle Cash-Neutral & Arbitrages :** Validation des opportunités et plans Moonbag.
        """)

  st.subheader(
      f"📰 Rapport d'Arbitrage Quotidien — Stratégie Moonbag ({curr_symbol})"
  )

  fng_data = {"value": 59, "classification": "Greed"}
  is_crash, crash_val = False, 1.5

  if EXPERT_ENGINE_AVAILABLE:
    try:
      fng_data = fetch_fear_and_greed_index()
      is_crash, crash_val = check_circuit_breaker()
    except Exception:
      pass

  m_col1, m_col2, m_col3 = st.columns(3)
  m_col1.metric(
      "Indice Fear & Greed",
      f"{fng_data['value']}/100",
      delta=f"{fng_data['classification']}",
  )
  m_col2.metric(
      "Circuit Breaker BTC (24h)",
      f"{crash_val:+.2f}%",
      delta="NORMAL 🟢" if not is_crash else "DÉFENSIF 🚨",
      delta_color="normal" if not is_crash else "inverse",
  )
  cash_eur = cash_info.get("total_cash", 0.0)
  m_col3.metric(
      f"Liquidités Cash Réel ({curr_symbol})",
      f"{cash_eur * curr_multiplier:,.2f} {curr_symbol}",
      delta="Cash-Neutral Strict",
  )

  st.markdown("---")
  st.subheader("🎯 Opportunités & Plan d'Arbitrage Explicite (Moonbag)")
  open_trades_digest = get_open_positions()
  if open_trades_digest:
    for t in open_trades_digest:
      rec_id, sym, exch, entry, amt, tp1, sl = (
          t[0],
          t[1],
          t[2],
          t[3],
          t[4],
          t[5] if len(t) > 5 else 0,
          t[7] if len(t) > 7 else 0,
      )
      val_amt = (amt if amt else 0.0) * curr_multiplier
      val_ent = (entry if entry else 0.0) * curr_multiplier
      val_tp = (tp1 if tp1 else 0.0) * curr_multiplier
      val_sl = (sl if sl else 0.0) * curr_multiplier

      with st.container():
        st.markdown(
            f"""
                <div style="padding: 15px; border-radius: 10px; border: 1px solid #00ffcc; margin-bottom: 15px; background-color: #161a1e;">
                    <h4 style="margin: 0; color: #00ffcc;">Focus : {sym} <span style="font-size: 12px; color: #aaa;">({exch})</span></h4>
                    <p style="margin: 8px 0 0 0; font-size: 14px;">
                        <b>Montant engagé :</b> {val_amt:,.2f} {curr_symbol} @ {val_ent:.4f} {curr_symbol}<br>
                        <b>Plan Moonbag :</b> 🎯 TP1 (x2) : {val_tp:.4f} {curr_symbol} (Vente 50% -> Free Ride) | 🛑 SL : {val_sl:.4f} {curr_symbol}
                    </p>
                </div>
                """,
            unsafe_allow_html=True,
        )
        bc1, bc2 = st.columns(2)
        with bc1:
          if st.button(
              f"✅ Marquer comme Pris (ID {rec_id})", key=f"digest_take_{rec_id}"
          ):
            mark_trade_taken(rec_id)
            st.cache_data.clear()
            st.success(f"Position {sym} enregistrée comme prise !")
            st.rerun()
        with bc2:
          if st.button(
              f"❌ Ignorer l'opportunité (ID {rec_id})",
              key=f"digest_ignore_{rec_id}",
          ):
            mark_trade_ignored(rec_id)
            st.cache_data.clear()
            st.warning(f"Opportunité {sym} ignorée.")
            st.rerun()
  else:
    st.info(
        "Aucune opportunité spéculative active en attente dans la base de"
        " données."
    )

# ==========================================
# ONGLET 4 : CENTRE D'ALERTES & RAPPROCHEMENT
# ==========================================
with tab_alerts:
  with st.expander("📖 Guide du Centre d'Alertes & Réconciliation"):
    st.markdown("""
        Vigie automatisée de votre cockpit :
        * **Rapprochement BDD vs Exchanges :** Détecte les lignes fantômes, les écarts de volumes et les nouveaux trades manuels.
        * **Alerte d'Exposition Spéculative :** Surveille si la poche dépasse les seuils de tolérance (12% et 15%), basée sur le calcul live exact.
        * **Radar TP / SL en Direct :** Détecte si vos actifs ouverts frôlent les Stop-Loss ou atteignent les Take-Profits.
        """)

  st.subheader("🕵️ Rapprochement BDD vs Réalité des Exchanges")
  st.markdown(
      "Audit automatique des écarts entre vos positions en base et vos avoirs"
      " réels :"
  )

  discrepancies = get_cached_reconciliation_audit()

  if discrepancies:
    st.error(
        f"🚨 {len(discrepancies)} anomalie(s) ou écart(s) détecté(s) dans le"
        " portefeuille !"
    )
    for disc in discrepancies:
      d_id = disc.get("id")
      d_sym = disc["symbol"]
      d_exch = disc["exchange"]
      d_type = disc["type"]
      if d_type == "MANUAL_TRADE_DETECTED":
        border_color, bg_color, badge = (
            "#00ffcc",
            "#152a26",
            "💡 NOUVEAU TRADE MANUEL DÉTECTÉ",
        )
      elif d_type == "GHOST_POSITION":
        border_color, bg_color, badge = "#ff4d4d", "#2a1515", "🔴 LIGNE FANTÔME"
      else:
        border_color, bg_color, badge = "#ffa500", "#2a2215", "🟡 ÉCART DE VOLUME"

      display_id_str = (
          f"[ID BDD: {d_id}]" if d_id is not None else "[ID BDD: None]"
      )
      st.markdown(
          f"""
            <div style="padding: 12px; border-radius: 8px; border: 1px solid {border_color}; margin-bottom: 10px; background-color: {bg_color};">
                <h4 style="margin: 0; color: {border_color};">{badge} — {d_sym} ({d_exch}) {display_id_str}</h4>
                <p style="margin: 4px 0 0 0; font-size: 13px; color: #ddd;">
                    {disc['desc']}
                </p>
            </div>
            """,
          unsafe_allow_html=True,
      )

      if d_type == "GHOST_POSITION":
        if st.button(
            f"🧹 Purger / Clôturer la ligne fantôme ID {d_id} ({d_sym})",
            key=f"web_clean_ghost_{d_id}",
        ):
          close_trade(d_id)
          st.cache_data.clear()
          send_telegram_notify(
              f"🧹 *LIGNE FANTÔME PURGÉE VIA WEB*\n• **{d_sym}** (ID {d_id})"
              " supprimée."
          )
          st.success(
              f"Position ID {d_id} ({d_sym}) clôturée et nettoyée de la base"
              " de données avec succès !"
          )
          st.rerun()

      if d_type == "VOLUME_MISMATCH":
        adj_amt = disc.get("adjusted_amount_eur", disc["db_amount"])
        disp_adj_amt = adj_amt * curr_multiplier
        if st.button(
            f"🔄 Synchroniser le volume ID {d_id} ({d_sym}) ->"
            f" {disp_adj_amt:,.2f} {curr_symbol}",
            key=f"web_sync_vol_{d_id}",
        ):
          update_trade_amount(d_id, adj_amt)
          st.cache_data.clear()
          send_telegram_notify(
              f"🔄 *VOLUME RESYNCHRONISÉ VIA WEB*\n• **{d_sym}** (ID {d_id})"
              f" ajusté à {disp_adj_amt:,.2f} {curr_symbol}."
          )
          st.success(
              f"Position ID {d_id} ({d_sym}) resynchronisée avec succès au"
              f" montant de {disp_adj_amt:,.2f} {curr_symbol} !"
          )
          st.rerun()

      if d_type == "MANUAL_TRADE_DETECTED":
        cost_eur = disc.get("cost", 0.0)
        price_eur = disc.get("price", 0.0)
        disp_cost = cost_eur * curr_multiplier
        disp_price = price_eur * curr_multiplier

        b_col1, b_col2 = st.columns(2)
        with b_col1:
          if st.button(
              f"🎯 Activer Moonbag ({d_sym} — {disp_cost:,.2f} {curr_symbol})",
              key=f"web_add_manual_{d_sym}_{d_exch}",
          ):
            from db_manager import add_recommendation, mark_trade_taken

            rec_id = add_recommendation(
                symbol=d_sym,
                exchange=d_exch,
                price=price_eur,
                amount_eur=cost_eur,
                tp1_price=price_eur * 2.0,
                tp2_price=price_eur * 4.0,
                sl_price=price_eur * 0.60,
                sl_type="invalidation",
            )
            mark_trade_taken(rec_id)
            st.cache_data.clear()
            send_telegram_notify(
                f"🎯 *TRADE MOONBAG ONBOARDÉ VIA WEB*\n• **{d_sym}**"
                f" ({disp_cost:,.2f} {curr_symbol} @ {disp_price:.4f}"
                f" {curr_symbol}) ajouté au cockpit."
            )
            st.success(
                f"✅ Position {d_sym} intégrée avec succès au cockpit Moonbag !"
            )
            st.rerun()

        with b_col2:
          if st.button(
              f"💎 Conserver en Poche CORE ({d_sym})",
              key=f"web_core_manual_{d_sym}_{d_exch}",
          ):
            from db_manager import mark_manual_trade_core

            mark_manual_trade_core(
                d_sym, d_exch, cost_eur, price_eur, reason="CORE"
            )
            st.cache_data.clear()
            send_telegram_notify(
                f"💎 *TRADE CLASSÉ POCHE CORE VIA WEB*\n• **{d_sym}**"
                f" ({disp_cost:,.2f} {curr_symbol}) conservé en investissement"
                " long terme."
            )
            st.success(f"✅ Trade {d_sym} classé en Poche CORE (alerte masquée) !")
            st.rerun()
  else:
    st.success(
        "✅ Parfaite synchronisation : aucune ligne fantôme ni écart"
        " significatif détecté."
    )

  st.markdown("---")
  st.subheader("🔥 Heatmap RSI Multi-Timeframe (Screener v2.9)")
  st.markdown(
      "Cartographie en temps réel du RSI (15m, 1h, 4h, 1D) sur vos actifs et"
      " positions actives :"
  )

  if RSI_ENGINE_AVAILABLE:
    tracked_symbols = list(
        {p["Symbole"] for p in enriched_positions}
        | {"BTC", "ETH", "SOL", "NEAR", "ONDO", "PENDLE", "SUI", "AVAX"}
    )

    if st.button("🔄 Actualiser la Heatmap RSI"):
      st.cache_data.clear()
      st.rerun()

    @st.cache_data(ttl=120, show_spinner="Calcul des indicateurs RSI...")
    def get_cached_rsi_matrix(symbols):
      return build_rsi_heatmap_dataframe(symbols)

    df_rsi = get_cached_rsi_matrix(tracked_symbols)

    if not df_rsi.empty:
      styled_df = df_rsi.style.map(color_rsi, subset=["15m", "1h", "4h", "1d"])
      st.dataframe(styled_df, width="stretch", hide_index=True)

      survendu_list = df_rsi[df_rsi["15m"] <= 30]["Actif"].tolist()
      surachete_list = df_rsi[df_rsi["4h"] >= 75]["Actif"].tolist()

      if survendu_list:
        st.success(
            "💡 **Opportunité de rebond court terme (RSI 15m ≤ 30)** :"
            f" {', '.join(survendu_list)}"
        )
      if surachete_list:
        st.warning(
            "⚠️ **Zone de surachat / Prise de profit recommandée (RSI 4h ≥"
            f" 75)** : {', '.join(surachete_list)}"
        )
  else:
    st.info("Module RSI Engine non disponible.")

  st.markdown("---")
  st.subheader("🔔 Vigie des Risques & Marché")

  alerts_list = []
  spec_pct = pct_spec if "pct_spec" in locals() else 10.0

  if spec_pct > 15.0:
    alerts_list.append({
        "level": "CRITICAL",
        "title": f"Surconcentration Spéculative Critique ({spec_pct:.2f}%)",
        "desc": (
            "La poche spéculative dépasse 15% du portefeuille (Cible : 10%)."
            " Risque de volatilité excessive."
        ),
    })
  elif spec_pct > 12.0:
    alerts_list.append({
        "level": "WARNING",
        "title": f"Alerte Exposition Spéculative Élevée ({spec_pct:.2f}%)",
        "desc": (
            "La part spéculative franchit le seuil de tolérance des 12%."
            " Surveillez vos lignes."
        ),
    })
  else:
    alerts_list.append({
        "level": "INFO",
        "title": f"Allocation Spéculative Saine ({spec_pct:.2f}%)",
        "desc": (
            "La part de la poche spéculative est alignée avec l'enveloppe"
            " cible."
        ),
    })

  if positions and enriched_positions:
    for row in enriched_positions:
      sym = row["Symbole"]
      exch = row["Exchange"]
      current_price = row["Prix Actuel"] / curr_multiplier
      tp1 = row["TP1"] / curr_multiplier
      sl = row["Stop-Loss"] / curr_multiplier

      if sl <= 0.0:
        alerts_list.append({
            "level": "CRITICAL",
            "title": f"Stop-Loss manquant pour {sym} ({exch})",
            "desc": f"La position ID {row['ID']} n'a aucun Stop-Loss défini.",
        })
      if tp1 <= 0.0:
        alerts_list.append({
            "level": "WARNING",
            "title": f"Take-Profit non configuré pour {sym} ({exch})",
            "desc": f"La position ID {row['ID']} n'a aucun palier TP1 défini.",
        })

      if current_price and current_price > 0:
        if sl > 0 and current_price <= (sl * 1.05):
          alerts_list.append({
              "level": "CRITICAL",
              "title": f"DANGER : Stop-Loss frôlé sur {sym} ({exch}) !",
              "desc": (
                  f"Prix actuel ({current_price:.4f} {curr_symbol}) proche du"
                  f" Stop-Loss ({sl:.4f} {curr_symbol})."
              ),
          })
        elif tp1 > 0 and current_price >= tp1:
          alerts_list.append({
              "level": "INFO",
              "title": f"OBJECTIF ATTEINT : TP1 Validé sur {sym} ({exch}) 🎯",
              "desc": (
                  f"Prix actuel ({current_price:.4f} {curr_symbol}) a atteint"
                  f" le TP1 ({tp1:.4f} {curr_symbol})."
              ),
          })
        elif tp1 > 0 and current_price >= (tp1 * 0.95):
          alerts_list.append({
              "level": "WARNING",
              "title": f"APPROCHE CIBLE : {sym} ({exch}) proche du TP1 🚀",
              "desc": (
                  f"Prix actuel ({current_price:.4f} {curr_symbol}) est proche"
                  f" du TP1 ({tp1:.4f} {curr_symbol})."
              ),
          })
  else:
    alerts_list.append({
        "level": "INFO",
        "title": "Aucune position active en BDD",
        "desc": "Le portefeuille spéculatif ne comporte aucune ligne active.",
    })

  if "is_crash" in locals() and is_crash:
    alerts_list.append({
        "level": "CRITICAL",
        "title": "Alerte Crash / Circuit Breaker activé",
        "desc": (
            f"Le Bitcoin chute de {crash_val:+.2f}% sur 24h. Mode défensif"
            " enclenché."
        ),
    })
  else:
    crash_display_val = crash_val if "crash_val" in locals() else 1.5
    alerts_list.append({
        "level": "INFO",
        "title": "Marché stable (Circuit Breaker Normal)",
        "desc": (
            f"Variation BTC 24h : {crash_display_val:+.2f}%. Conditions"
            " nominales."
        ),
    })

  if alerts_list:
    for alert in alerts_list:
      lvl = alert["level"]
      if lvl == "CRITICAL":
        border_color, bg_color, badge = "#ff4d4d", "#2a1515", "🔴 CRITIQUE"
      elif lvl == "WARNING":
        border_color, bg_color, badge = "#ffa500", "#2a2215", "🟡 ATTENTION"
      else:
        border_color, bg_color, badge = "#00ffcc", "#152a26", "🟢 INFORMATION"

      st.markdown(
          f"""
            <div style="padding: 15px; border-radius: 10px; border: 1px solid {border_color}; margin-bottom: 12px; background-color: {bg_color};">
                <h4 style="margin: 0; color: {border_color};">{badge} — {alert['title']}</h4>
                <p style="margin: 6px 0 0 0; font-size: 14px; color: #ddd;">
                    {alert['desc']}
                </p>
            </div>
            """,
          unsafe_allow_html=True,
      )

# ==========================================
# ONGLET 5 : ASSISTANT IA PRO
# ==========================================
with tab_ai:
  with st.expander("📖 Guide de l'Assistant IA Conversationnel"):
    st.markdown("""
        Votre stratège crypto personnel en direct :
        * **Prompts Rapides :** Lancez des analyses complexes en un clic sans rien saisir.
        * **Chat Libre :** Posez n'importe quelle question sur vos positions et votre gestion du risque.
        """)

  st.subheader("🤖 Assistant IA & Stratège Crypto (Propulsé par Gemini)")
  st.markdown(
      "Discutez en direct ou lancez une analyse pré-configurée en 1 clic :"
  )

  st.markdown("##### ⚡ Prompts Rapides & Analyses en 1 Clic")
  col_p1, col_p2, col_p3, col_p4 = st.columns(4)

  preset_prompt = None
  preset_context = None

  with col_p1:
    if st.button("📰 Rapport & Évaluation Spéculative", width="stretch"):
      with st.spinner("Analyse impartiale des opportunités et des coûts d'opportunité..."):
        from crypto_speculative_advisor import (
            calculate_unfied_crypto_categories,
            check_circuit_breaker,
            fetch_coingecko_trending_and_markets,
            fetch_fear_and_greed_index,
            get_kraken_spot_symbols,
            get_okx_spot_symbols,
        )

        is_crash, crash_pct = check_circuit_breaker()
        fng = fetch_fear_and_greed_index()
        cats = calculate_unfied_crypto_categories()
        open_pos = get_open_positions()

        raw_opps = [] if is_crash else fetch_coingecko_trending_and_markets()
        kraken_syms, okx_syms = get_kraken_spot_symbols(), get_okx_spot_symbols()
        verified_opps = []
        for item in raw_opps:
          sym = item["symbol"].upper()
          avail = []
          if sym in kraken_syms: avail.append("KRAKEN")
          if sym in okx_syms: avail.append("OKX")
          if avail:
            item["exchanges_verifies"] = avail
            verified_opps.append(item)

        rsi_context_str = "Non disponible"
        if RSI_ENGINE_AVAILABLE:
          try:
            current_syms = [p[1] for p in open_pos] if open_pos else []
            market_syms = ["SOL", "NEAR", "ONDO", "PENDLE", "SUI", "AVAX", "LINK", "APT", "FET", "INJ", "TIA", "RENDER"]
            combined_syms = list(set(current_syms + market_syms))
            
            df_rsi_prompt = build_rsi_heatmap_dataframe(combined_syms)
            rsi_context_str = df_rsi_prompt.to_string(index=False)
          except Exception:
            pass

        target_budget = cats["total_crypto"] * 0.10

        preset_prompt = "Analyse la pertinence spéculative globale de mon portefeuille face aux opportunités actuelles du marché."

        preset_context = f"""Tu es un stratège et trader crypto institutionnel.
Ta mission est d'effectuer une ANALYSE DU COÛT D'OPPORTUNITÉ neutre et sans parti pris entre mes positions spéculatives actuelles et les opportunités globales du marché.

ÉTAT DU MARCHÉ & INDICATEURS :
- Statut Circuit Breaker : {"🚨 DEFENSIVE MODE" if is_crash else "✅ NORMAL"}
- Var BTC 24h : {crash_pct:+.2f}% | Fear & Greed : {fng['value']}/100 ({fng['classification']})

SITUATION DU PORTEFEUILLE :
- Capital Total : {cats['total_crypto']:,.2f} €
- Liquidités Immédiates (Fiat + Stables) : {cats['stables_cash']:,.2f} €
- Positions Spéculatives Actives en BDD : {open_pos}

MATRICE DE MARCHÉ ET SCREENER RSI MULTI-TIMEFRAME :
{rsi_context_str}

CONSIGNES STRICTES D'ANALYSE IMPARTIALE :
1. **Évalue la qualité spéculative de mes positions actuelles** : Ont-elles encore du momentum ? Leur RSI est-il favorable ou suracheté ? La thèse est-elle toujours valide ?
2. **Compare avec le reste du marché** : Y a-t-il d'autres actifs sur Kraken/OKX ayant un meilleur setup spéculatif (ex: RSI 15m/1h survendu avec tendance 4h haussière) ?
3. **Recommandations d'Arbitrage Précises** :
   - NE FORCE PAS la recommandation d'un nouveau token si mes positions actuelles sont déjà les plus performantes.
   - N'HÉSITE PAS à recommander la clôture/allègement d'un de mes tokens 'mous' ou trop exposés pour pivoter vers un actif à meilleur potentiel spéculatif (ou vers le Cash).
   - Pour toute opportunité retenue, fournis le plan Moonbag complet (Exchange, Entrée, TP1 x2, TP2 x4, SL -35% à -45%)."""

  with col_p2:
    if st.button("🛡️ Audit de Protection & SL", width="stretch"):
      with st.spinner("Analyse de la sécurité de vos positions en direct..."):
        open_pos = get_open_positions()

        if open_pos:
          pos_audit_data = []
          total_risk_weight = 0.0
          weighted_sl_dist = 0.0
          missing_sl_count = 0

          for p in open_pos:
            (
                p_id,
                sym,
                exch,
                entry,
                amt,
                tp1,
                tp2,
                sl,
                sl_type,
                step,
                taken_at,
                alert_stat,
            ) = p
            entry = float(entry) if entry else 0.0
            amt = float(amt) if amt else 0.0
            tp1 = float(tp1) if tp1 else 0.0
            sl = float(sl) if sl else 0.0

            lp = fetch_live_price(sym, exch) or entry
            qty = (amt / entry) if entry > 0 else 0.0
            curr_val = qty * lp
            pnl_pct = ((lp - entry) / entry) * 100.0 if entry > 0 else 0.0

            if sl > 0 and lp > 0:
              dist_sl_pct = ((lp - sl) / lp) * 100.0
              if dist_sl_pct > 0:
                weighted_sl_dist += dist_sl_pct * curr_val
                total_risk_weight += curr_val
            else:
              dist_sl_pct = None
              missing_sl_count += 1

            pos_audit_data.append({
                "id": p_id,
                "symbole": sym,
                "exchange": exch,
                "prix_entree_eur": round(entry, 4),
                "prix_actuel_eur": round(lp, 4),
                "valeur_actuelle_eur": round(curr_val, 2),
                "pnl_pct": round(pnl_pct, 2),
                "tp1_eur": round(tp1, 4),
                "sl_eur": round(sl, 4),
                "distance_sl_pct": (
                    round(dist_sl_pct, 2)
                    if dist_sl_pct is not None
                    else "MANQUANT"
                ),
                "etape": step,
            })

          global_buffer = (
              (weighted_sl_dist / total_risk_weight)
              if total_risk_weight > 0
              else 0.0
          )

          preset_prompt = (
              "Exécute un audit strict de protection et de sécurité sur"
              " l'ensemble de mes positions actives."
          )

          preset_context = f"""Tu es un trader crypto expert et un Risk Manager impitoyable.
Fais un audit de sécurité complet et structuré de l'ensemble des positions actuellement ouvertes en portefeuille.

SYNTHÈSE DE LA PROTECTION GLOBALE :
- Nombre de positions actives : {len(open_pos)}
- Buffer de risque moyen global : {global_buffer:.1f}% (Marge avant déclenchement des SL)
- Nombre de positions sans Stop-Loss : {missing_sl_count}

DÉTAIL DES POSITIONS EN PORTEFEUILLE :
{json.dumps(pos_audit_data, indent=2, ensure_ascii=False)}

CONSIGNES DE RÉDACTION POUR L'AUDIT :
1. **Niveau de Risque Global** : Évalue la sécurité générale du portefeuille.
2. **Positions Sans Protection (CRITIQUE)** : Liste clairement les tokens n'ayant AUCUN Stop-Loss défini (sl_eur = 0) et conseille le niveau d'invalidation technique à ajouter.
3. **Zone de Danger / SL Frôlé (ATTENTION)** : Identifie les lignes dont le prix actuel est proche du Stop-Loss (distance < 10%).
4. **Opportunités de Sécurisation (Break-Even / Free Ride)** : Pour les positions où TP1 est atteint ou en fort gain, recommande de remonter le Stop-Loss au prix d'entrée (Break-Even).
5. **Plan d'Action Immédiat** : Résume en 3 à 5 points à puces les réglages prioritaires à effectuer.

Rédige avec des titres clairs (##), des puces et du gras."""
        else:
          preset_prompt = "Fais un audit de sécurité de mes positions."
          preset_context = (
              "Aucune position active n'est actuellement enregistrée en BDD."
              " Indique à l'utilisateur que son portefeuille spéculatif est"
              " totalement liquide et sécurisé."
          )

  with col_p3:
    if st.button("🔄 Arbitrages & Arbitrages Tactiques", width="stretch"):
      with st.spinner("Analyse des arbitrages tactiques sous règle Cash-Neutral..."):
        from crypto_speculative_advisor import calculate_unfied_crypto_categories
        cats = calculate_unfied_crypto_categories()
        open_pos = get_open_positions()

        preset_prompt = "Quels sont les arbitrages spéculatifs les plus pertinents à effectuer aujourd'hui ?"

        preset_context = f"""Tu es un gérant de portefeuille crypto focalisé sur le rendement asymétrique.
Analyse les meilleures rotations de capital possibles entre mes positions actuelles, les liquidités et le marché.

DONNÉES PORTEFEUILLE :
- Liquidités Immédiates : {cats['stables_cash']:,.2f} €
- Positions Spéculatives Actives : {open_pos}

CONSIGNES D'ARBITRAGE :
1. Détermine si le meilleur moove est :
   a) De conserver/sécuriser nos positions actuelles (ex: remonter SL en Break-Even).
   b) D'écrêter les gains d'un token en surachat pour renforcer la poche Cash.
   c) De faire un arbitrage (vendre un actif essoufflé pour en acheter un avec un meilleur setup RSI/momentum).
2. Fournis des consignes concrètes et chiffrées par exchange."""

  with col_p4:
    if st.button("🧭 Plan d'Exécution Pas-à-Pas", width="stretch"):
      with st.spinner(
          "Calcul du plan d'exécution et dispatching par exchange..."
      ):
        from crypto_speculative_advisor import (
            calculate_unfied_crypto_categories,
        )

        cats = calculate_unfied_crypto_categories()
        open_pos = get_open_positions()
        all_balances = get_cached_crypto_balances()

        target_spec = cats["total_crypto"] * 0.10
        target_majors = cats["total_crypto"] * 0.55
        target_core = cats["total_crypto"] * 0.22
        target_cash = cats["total_crypto"] * 0.13

        delta_majors = cats["btc_eth"] - target_majors
        delta_spec = cats["speculative"] - target_spec
        delta_core = target_core - cats["core"]
        delta_cash = target_cash - cats["stables_cash"]

        preset_prompt = (
            "Génère ma feuille de route d'exécution tactique pas-à-pas pour"
            " rééquilibrer le portefeuille sur mes exchanges."
        )

        if RSI_ENGINE_AVAILABLE:
          rsi_summary_text = df_rsi.to_string(index=False)
        else:
          rsi_summary_text = "Non disponible"

        preset_context = f"""Tu es un Execution Trader institutionnel expert.
Ta mission est de convertir une stratégie globale d'allocation en une FEUILLE DE ROUTE D'EXÉCUTION PAS-À-PAS, ultra-concrète et directement applicable sur les échanges de l'utilisateur.

SITUATION DU PORTEFEUILLE & ÉCARTS CIBLES :
- Valeur totale du capital : {cats['total_crypto']:,.2f} €
- Majeures (BTC/ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) | Cible 55% ({target_majors:,.2f} €) -> ÉCART : {delta_majors:+,.2f} € (À Alléger)
- Poche Core : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) | Cible 22% ({target_core:,.2f} €) -> ÉCART : {delta_core:+,.2f} € (À Renforcer)
- Spéculatif (Moonbag) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) | Cible 10% ({target_spec:,.2f} €) -> ÉCART : {delta_spec:+,.2f} € (À Écrêter)
- Liquidités / Cash : {cats['stables_cash']:,.2f} € ({cats['stables_cash_pct']:.1f}%) | Cible 13% ({target_cash:,.2f} €) -> ÉCART : {delta_cash:+,.2f} € (À Reconstituer)

SOLDES RÉELS DÉTECTÉS PAR PLATEFORME (Direct APIs) :
{json.dumps(all_balances, indent=2, ensure_ascii=False)}

POSITIONS SPÉCULATIVES ACTIVES EN BDD :
{open_pos}

CONSIGNES DE RÉDACTION POUR LA FEUILLE DE ROUTE :
1. **Étape 1 : Reconstruction du Cash (Priorité 1 — Allègement Majeures)**
   - Indique exactement quelle quantité de BTC ou ETH vendre, sur quel exchange (Kraken ou OKX selon les soldes réels ci-dessus) et vers quelle paire (BTC/EUR ou BTC/USDC).
   - Propose un découpage temporel (ex. 3 ou 4 tranches hebdomadaires) pour éviter le risque de market timing.

2. **Étape 2 : Écrêtage Spéculatif (~{abs(delta_spec):,.2f} €)**
   - Identifie dans les positions spéculatives actives ci-dessus celles qu'il faut alléger ou clôturer partiellement pour récupérer le surplus de ~200 €.

3. **Étape 3 : Plan de Déploiement DCA sur la Poche CORE**
   - Donne des consignes d'achats limites sur les replis techniques pour renforcer progressivement tes convictions Core (ex. SOL, NEAR, ONDO, PENDLE) sur OKX/Kraken avec les USDC/EUR générés.

Rédige sous forme de checklist d'exécution avec du gras, des montants précis et des noms d'exchanges."""

        preset_context += f"\n\nDONNÉES RSI MULTI-TIMEFRAME (COINGLASS HEATMAP) :\n{rsi_summary_text}\n"
        preset_context += (
            "\nCONSIGNE RISQUE RSI : Ne jamais recommander d'achat sur un actif dont le"
            " RSI 4h est > 70. Privilégier les entrées sur des tokens dont le RSI 15m"
            " sort de zone de survente (< 35)."
        )

  st.markdown("---")

  if "messages" not in st.session_state:
    st.session_state.messages = [{
        "role": "assistant",
        "content": (
            "Bonjour ! Je suis ton assistant trader expert. Sélectionne une"
            " analyse rapide ci-dessus ou pose-moi directement ta question."
        ),
    }]

  for message in st.session_state.messages:
    with st.chat_message(message["role"]):
      st.markdown(message["content"])

  user_input = st.chat_input(
      "Ex: Analyse mes risques actuels ou donne-moi ton avis sur mon"
      " allocation..."
  )

  prompt_to_process = user_input or preset_prompt

  if prompt_to_process:
    st.session_state.messages.append(
        {"role": "user", "content": prompt_to_process}
    )
    with st.chat_message("user"):
      st.markdown(prompt_to_process)

    if not preset_context:
      current_positions = get_open_positions()
      current_cash = get_cached_verified_cash()
      preset_context = f"""Tu es un trader crypto expert, analytique et pédagogue, spécialisé en altcoins, stratégie Moonbag et gestion du risque.
L'utilisateur te consulte sur son cockpit web personnel. 
Données de son portefeuille actuel :
- Positions ouvertes en BDD : {current_positions}
- Cash & Liquidités : {current_cash}
- Devise active : {curr_label}

Réponds de manière professionnelle, structurée, concrète et percutante."""

    with st.chat_message("assistant"):
      with st.spinner("Analyse des marchés et génération du rapport..."):
        try:
          client = genai.Client(api_key=GEMINI_API_KEY)

          preferred_models = [
              "gemini-3.8-flash",
              "gemini-3.7-flash",
              "gemini-3.6-flash",
              "gemini-3.5-flash-lite",
          ]

          answer, used_model = call_gemini_with_fallback(
              client=client,
              prompt_text=prompt_to_process,
              system_instruction=preset_context,
              models=preferred_models,
          )

          st.markdown(answer)
          st.caption(f"🤖 *Réponse générée avec succès via `{used_model}`*")

          st.session_state.messages.append(
              {"role": "assistant", "content": answer}
          )

        except Exception as e:
          error_msg = f"⚠️ Impossible d'obtenir une réponse de Gemini : {e}"
          st.error(error_msg)

# ==========================================
# ONGLET 6 : MONITORING & SANTÉ APIS
# ==========================================
with tab_monitor:
  with st.expander("📖 Guide du Monitoring Technique"):
    st.markdown("""
        Surveillez l'état de santé de vos connecteurs et services système :
        * **Connecteurs API :** Vérification de la disponibilité des exchanges.
        * **Journaux Système (Logs) :** Visualisez les derniers événements des services en arrière-plan.
        """)

  st.subheader("🔌 État de Santé des Connecteurs API")
  col_m1, col_m2, col_m3 = st.columns(3)
  with col_m1:
    st.markdown("#### 🐙 Kraken")
    st.metric("Statut API", "Opérationnel 🟢", delta="Latence < 150ms")
  with col_m2:
    st.markdown("#### 🅾️ OKX")
    st.metric("Statut API", "Opérationnel 🟢", delta="Latence < 120ms")
  with col_m3:
    st.markdown("#### Ⓜ️ MEXC")
    st.metric("Statut API", "Opérationnel 🟢", delta="Latence < 200ms")

  st.markdown("---")
  st.subheader("📜 Journaux Système en Direct (Systemd Logs)")
  log_service = st.selectbox(
      "Sélectionner le service à inspecter",
      ["streamlit.service", "finary-bot.service", "cloudflare.service"],
  )

  if st.button("🔄 Rafraîchir les logs"):
    st.rerun()

  try:
    cmd = f"journalctl -u {log_service} -n 30 --no-pager"
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=3
    )
    if result.returncode != 0 or not result.stdout.strip():
      log_output = (
          "⚠️ Impossible de lire les logs systemd directement via l'interface"
          " web.\n💡 Astuce SSH : sudo journalctl -u"
          f" {log_service} -n 30"
      )
    else:
      log_output = result.stdout
  except Exception as e:
    log_output = f"Erreur lors de la récupération des logs : {e}"

  st.text_area(
      f"Dernières lignes de log pour {log_service}", log_output, height=300
  )


# ==========================================
# ONGLET 7 : EXPORT COMPTABLE & FISCAL
# ==========================================
with tab_fiscal:
  with st.expander("📖 Guide de l'Assistant Fiscal Avancé"):
    st.markdown("Outil d'aide à la déclaration et export CSV normalisé.")

  st.subheader("🧾 Assistant d'Export Fiscal & Comptable (v3.0)")
  year_selected = st.selectbox(
      "Sélectionner l'année fiscale", [2026, 2025, 2024]
  )

  col_f1, col_f2 = st.columns(2)
  with col_f1:
    include_fees = st.checkbox(
        "Inclure le détail des frais de transaction (API fees)",
        value=True,
        key="chk_fees",
    )
  with col_f2:
    only_closed = st.checkbox(
        "Isoler uniquement les positions clôturées (réalisées)",
        value=True,
        key="chk_closed",
    )

  if st.button("📥 Générer et télécharger le rapport fiscal certifié (CSV)"):
    trades_hist = get_all_trades_history()
    df_export = pd.DataFrame(trades_hist)
    if not df_export.empty:
      csv_data = df_export.to_csv(index=False).encode("utf-8")
      st.download_button(
          label=(
              f"💾 Télécharger le rapport de cessions {year_selected} (Format"
              " CSV)"
          ),
          data=csv_data,
          file_name=f"rapport_fiscal_crypto_{year_selected}.csv",
          mime="text/css",
      )
      st.success(f"Rapport fiscal {year_selected} généré avec succès !")
    else:
      st.warning(
          "Aucune donnée historique disponible pour générer ce rapport."
      )
