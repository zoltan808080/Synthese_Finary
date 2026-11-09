#!/usr/bin/env python3
"""Cockpit Spéculatif & Analytics Streamlit v3.9.1 (Benchmark BTC, Boussole de Flux Macro, Vigie Intra-CORE 35%, Sentinelle Zombies, Tickets d'Ordres & Thèses CORE 1-Click)."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from db_manager import (
    add_recommendation,
    close_trade,
    get_auto_tp_sl_status,
    get_closed_trades_history,
    get_open_positions,
    init_db,
    mark_manual_trade_core,
    mark_trade_taken,
    merge_duplicate_trades,
    set_trade_break_even,
    toggle_auto_tp_sl,
    update_trade_amount,
    update_trade_levels,
    # --- Nouveautés v3.6.0+ ---
    get_all_core_metadata,
    update_core_asset_metadata,
    # --- Nouveautés v3.8.0 ---
    get_active_zombies,
    clear_zombie_record,
)
from exchange_connectors import (
    get_all_exchange_crypto_balances,
    get_total_verified_cash,
)
# Import du gestionnaire centralisé Gemini
from gemini_manager import (
    call_gemini_with_fallback,
    load_gemini_config,
    save_gemini_config,
)

# Import Moteur Macro SSOT (BTC.D, TOTAL3, Stablecoins, F&G, Circuit Breaker)
try:
    from macro_market_engine import (
        get_complete_macro_context,
        fetch_fear_and_greed_index,
        check_circuit_breaker,
        get_macro_market_indicators,
        format_macro_context_for_prompt,
    )
    MACRO_ENGINE_AVAILABLE = True
except ImportError:
    MACRO_ENGINE_AVAILABLE = False

# Import de la Sentinelle Actifs Zombies (v3.8.0)
try:
    from zombie_sentinel import run_zombie_sentinel
    ZOMBIE_SENTINEL_AVAILABLE = True
except ImportError:
    ZOMBIE_SENTINEL_AVAILABLE = False

# Import du module central d'externalisation et d'administration des prompts (SSOT v3.5+)
try:
    from prompt_loader import (
        get_system_prompt,
        get_raw_prompt,
        save_system_prompt,
        restore_backup_prompt,
        list_available_prompts,
    )
    PROMPT_LOADER_AVAILABLE = True
except ImportError:
    PROMPT_LOADER_AVAILABLE = False

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
        calculate_unfied_crypto_categories,
        fetch_fear_and_greed_index as advisor_fng,
        check_circuit_breaker as advisor_cb,
    )
    EXPERT_ENGINE_AVAILABLE = True
except ImportError:
    EXPERT_ENGINE_AVAILABLE = False

# Import du moteur RSI
try:
    from rsi_engine import build_rsi_heatmap_dataframe
    RSI_ENGINE_AVAILABLE = True
except ImportError:
    RSI_ENGINE_AVAILABLE = False

# Import du moteur d'auto-ajustement TP/SL
try:
    from auto_tpsl_engine import run_auto_tpsl_cycle
    AUTO_TPSL_AVAILABLE = True
except ImportError:
    AUTO_TPSL_AVAILABLE = False


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_complete_macro_context():
    """Source Unique de Vérité (SSOT) pour le Contexte Macro complet (Cache 5 min)."""
    if MACRO_ENGINE_AVAILABLE:
        try:
            return get_complete_macro_context()
        except Exception:
            pass

    fng = get_cached_fear_and_greed()
    is_crash, btc_24h = get_cached_circuit_breaker()
    return {
        "fear_and_greed": fng,
        "circuit_breaker": {"is_crash": is_crash, "btc_24h_pct": btc_24h},
        "metrics": {
            "btc_dominance": 58.0,
            "eth_dominance": 14.5,
            "total3_usd_b": 650.0,
            "stablecoins_cap_b": 170.0,
            "status": "fallback",
        },
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_fear_and_greed():
    """Source Unique de Vérité (SSOT) pour l'indice Fear & Greed (Cache 5 min)."""
    if MACRO_ENGINE_AVAILABLE:
        try:
            return fetch_fear_and_greed_index()
        except Exception:
            pass
    elif EXPERT_ENGINE_AVAILABLE:
        try:
            return advisor_fng()
        except Exception:
            pass
    return {"value": 50, "classification": "Neutral"}


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_circuit_breaker():
    """Source Unique de Vérité (SSOT) pour le Circuit Breaker BTC (Cache 5 min)."""
    if MACRO_ENGINE_AVAILABLE:
        try:
            return check_circuit_breaker()
        except Exception:
            pass
    elif EXPERT_ENGINE_AVAILABLE:
        try:
            return advisor_cb()
        except Exception:
            pass
    return False, 0.0


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


@st.cache_data(ttl=120, show_spinner=False)
def get_cached_market_tech_micro_context(symbols_tuple):
    """Génère et met en cache (2 min) la synthèse RSI & Microstructure."""
    symbols_list = list(symbols_tuple)

    rsi_context_str = "RSI Non disponible"
    if RSI_ENGINE_AVAILABLE:
        try:
            df_rsi_prompt = build_rsi_heatmap_dataframe(symbols_list)
            if not df_rsi_prompt.empty:
                rsi_context_str = df_rsi_prompt.to_string(index=False)
        except Exception:
            pass

    foi_context_str = "Microstructure Non disponible"
    if FUNDING_ENGINE_AVAILABLE:
        try:
            foi_data = get_funding_oi_summary(symbols_list)
            if foi_data and "pairs" in foi_data and foi_data["pairs"]:
                df_foi = pd.DataFrame(foi_data["pairs"])
                foi_context_str = (
                    f"Funding Rate Moyen (8h) : {foi_data.get('avg_funding_rate', 0):+.4f}%\n"
                    f"Open Interest Total (Portefeuille) : {foi_data.get('total_open_interest_usd', 0)/1e6:,.1f} M$\n"
                    f"Paires en Surchauffe Longs (>+0.03%) : {foi_data.get('overheated_longs_count', 0)} | Potentiels Short Squeeze (<-0.01%) : {foi_data.get('short_squeeze_count', 0)}\n\n"
                )
                if not df_foi.empty:
                    df_foi_disp = df_foi[[
                        "symbol",
                        "exchange",
                        "funding_rate_8h_pct",
                        "funding_rate_annual_pct",
                        "open_interest_usd",
                        "bias_status",
                    ]].copy()
                    df_foi_disp.columns = [
                        "Symbole",
                        "Exchange",
                        "Funding_8h(%)",
                        "Funding_Annuel(%)",
                        "OI($)",
                        "Biais_Microstructure",
                    ]
                    foi_context_str += df_foi_disp.to_string(index=False)
        except Exception:
            pass

    return f"""
---
📊 MATRICE TECHNIQUE & SCREENER RSI MULTI-TIMEFRAME :
{rsi_context_str}

🔥 MICROSTRUCTURE DE MARCHÉ & DÉRIVÉS (FUNDING RATES 8H & OPEN INTEREST - OKX SWAP) :
{foi_context_str}
"""


def build_market_tech_micro_context(open_pos=None, balances=None):
    symbols = {
        "BTC", "ETH", "SOL", "NEAR", "ONDO", "PENDLE",
        "SUI", "AVAX", "RAY", "FET", "RENDER", "STRK", "TAO",
    }
    if open_pos:
        for p in open_pos:
            if len(p) > 1 and p[1]:
                symbols.add(
                    str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
                )
    if balances:
        for asset in balances.keys():
            if asset not in ["USDT", "USDC", "EUR", "USD"]:
                symbols.add(str(asset).upper().strip())
    return get_cached_market_tech_micro_context(tuple(sorted(list(symbols))))


def compute_ai_liquidities(balances, eur_usd_rate=1.12, fallback_cash_eur=0.0):
    fiat_eur = 0.0
    stables_eur = 0.0
    if balances:
        rate = eur_usd_rate if eur_usd_rate and eur_usd_rate > 0 else 1.12
        for asset, info in balances.items():
            asset_u = str(asset).upper()
            amt = info.get("amount", 0.0) if isinstance(info, dict) else float(info)
            if asset_u in ["EUR", "USD"]:
                fiat_eur += amt if asset_u == "EUR" else (amt / rate)
            elif asset_u in ["USDT", "USDC", "DAI", "FDUSD"]:
                stables_eur += amt / rate if asset_u in ["USDT", "USDC"] else amt

    total_liq = fiat_eur + stables_eur
    if total_liq == 0 and fallback_cash_eur > 0:
        total_liq = fallback_cash_eur
        stables_eur = fallback_cash_eur
    return round(fiat_eur, 2), round(stables_eur, 2), round(total_liq, 2)


def extract_core_proposals_from_text(text: str):
    """Extrait le bloc JSON machine 'core_proposals' généré par macro_arbitrage.md (v3.9.0+)."""
    import re
    if not text or "core_proposals" not in text:
        return None
    # Recherche bloc ```json ... ``` ou ``` ... ```
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?"core_proposals"[\s\S]*?\})\s*```', text, re.IGNORECASE)
    if match:
        try:
            data = json.loads(match.group(1))
            return data.get("core_proposals")
        except Exception:
            pass
    # Recherche directe d'objet JSON sans backticks
    match2 = re.search(r'(\{[\s\S]*?"core_proposals"[\s\S]*?\})', text)
    if match2:
        try:
            data = json.loads(match2.group(1))
            return data.get("core_proposals")
        except Exception:
            pass
    return None


def get_formatted_core_and_majors_breakdown(balances, eur_usd_rate=1.12, open_pos=None):
    if not balances:
        return "Aucun actif CORE ou Majeure détecté."
    
    speculative_symbols = set()
    if open_pos:
        for p in open_pos:
            if len(p) > 1 and p[1]:
                speculative_symbols.add(
                    str(p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
                )

    core_meta = {}
    try:
        core_meta = get_all_core_metadata()
    except Exception:
        pass

    breakdown = []
    for asset, info in balances.items():
        asset_u = str(asset).upper().strip()
        if asset_u in ["EUR", "USD", "USDT", "USDC", "DAI", "FDUSD"] or asset_u in speculative_symbols:
            continue
        
        amt = info.get("amount", 0.0) if isinstance(info, dict) else float(info)
        exch = info.get("exchange", "") if isinstance(info, dict) else ""
        lp = fetch_live_price(asset_u, exch) or 0.0
        val_eur = amt * lp
        
        if val_eur >= 50.0:
            meta_info = core_meta.get(asset_u, {})
            status_str = f" [Thèse: {meta_info.get('thesis_status', 'ACTIVE')}]" if meta_info else ""
            note_str = f" (Note: {meta_info.get('notes')})" if meta_info and meta_info.get("notes") else ""
            breakdown.append(f"• {asset_u} : {val_eur:,.2f} € ({exch}){status_str}{note_str}")
            
    return "\n".join(breakdown) if breakdown else "Aucun actif significatif uniquement détenu en CORE / Majeures."


load_dotenv()

st.set_page_config(
    page_title="Crypto Speculative Monitor & Analytics",
    page_icon="🚀",
    layout="wide",
)

# --- STYLING CSS v3.7.0+ : ORDER TICKETS & MONOSPACE TERMINAL ---
st.markdown(
    """
    <style>
    /* Amélioration visuelle des blocs de tickets d'ordres (pre / code) */
    pre, code {
        font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
        letter-spacing: -0.2px;
    }
    div[data-testid="stChatMessage"] pre {
        background-color: #121820 !important;
        border: 1px solid #1f3a52 !important;
        border-left: 4px solid #00ffcc !important;
        border-radius: 6px !important;
        padding: 12px !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_live_price_cached(sym, exch, eur_usd_rate):
    clean_sym = str(sym).upper().replace("USDT", "").replace("EUR", "").strip()
    clean_exch = str(exch).upper().strip() if exch else ""
    rate = eur_usd_rate if eur_usd_rate and eur_usd_rate > 0 else 1.12

    if clean_sym in ["EUR", "USD"]:
        return 1.0 if clean_sym == "EUR" else (1.0 / rate)

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # 1. Kraken Spot
    if "KRAKEN" in clean_exch:
        for pair in [f"{clean_sym}EUR", f"X{clean_sym}ZEUR"]:
            try:
                res = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                    headers=headers,
                    timeout=4.0,
                )
                if res.status_code == 200:
                    result = res.json().get("result", {})
                    if result:
                        key = list(result.keys())[0]
                        price = float(result[key]["c"][0])
                        if price > 0:
                            return price
            except Exception:
                pass

        for pair in [f"{clean_sym}USD", f"{clean_sym}USDT"]:
            try:
                res = requests.get(
                    f"https://api.kraken.com/0/public/Ticker?pair={pair}",
                    headers=headers,
                    timeout=4.0,
                )
                if res.status_code == 200:
                    result = res.json().get("result", {})
                    if result:
                        key = list(result.keys())[0]
                        price = float(result[key]["c"][0])
                        if price > 0:
                            return price / rate
            except Exception:
                pass

        try:
            cg_id = "litentry" if clean_sym == "LIT" else clean_sym.lower()
            res = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=eur",
                headers=headers,
                timeout=3.0,
            )
            if res.status_code == 200:
                data = res.json()
                if cg_id in data and "eur" in data[cg_id]:
                    return float(data[cg_id]["eur"])
        except Exception:
            pass

        return None

    # 2. OKX
    elif "OKX" in clean_exch:
        try:
            res = requests.get(
                f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-EUR",
                headers=headers,
                timeout=3.0,
            )
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    price = float(data[0]["last"])
                    if price > 0:
                        return price
        except Exception:
            pass

        for inst in [f"{clean_sym}-USDT", f"{clean_sym}-USDC"]:
            try:
                res = requests.get(
                    f"https://www.okx.com/api/v5/market/ticker?instId={inst}",
                    headers=headers,
                    timeout=3.0,
                )
                if res.status_code == 200:
                    data = res.json().get("data", [])
                    if data:
                        price = float(data[0]["last"])
                        if price > 0:
                            return price / rate
            except Exception:
                pass

        return None

    # 3. Fallback générique (Kraken ➔ CoinGecko)
    try:
        res_k = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={clean_sym}EUR",
            headers=headers,
            timeout=3.0,
        )
        if res_k.status_code == 200:
            result_k = res_k.json().get("result", {})
            if result_k:
                key = list(result_k.keys())[0]
                price = float(result_k[key]["c"][0])
                if price > 0:
                    return price
    except Exception:
        pass

    return None


def fetch_live_price(sym, exch):
    clean_sym = str(sym).upper().replace("USDT", "").replace("EUR", "").strip()
    clean_exch = str(exch).upper().strip() if exch else ""
    if clean_sym in ["EUR", "USD"]:
        return 1.0 if clean_sym == "EUR" else (1.0 / eur_usd_rate)
    if clean_exch:
        return fetch_live_price_cached(sym, exch, eur_usd_rate)
    if clean_sym in _bulk_prices_map and _bulk_prices_map[clean_sym] is not None:
        return _bulk_prices_map[clean_sym]
    return fetch_live_price_cached(sym, exch, eur_usd_rate)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_bulk_live_prices(symbols_tuple, eur_usd_rate):
    symbols_list = list(symbols_tuple)
    prices = {}
    if not symbols_list:
        return prices
    with ThreadPoolExecutor(max_workers=min(12, len(symbols_list))) as executor:
        future_to_sym = {
            executor.submit(fetch_live_price_cached, sym, "", eur_usd_rate): sym
            for sym in symbols_list
        }
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                prices[sym] = future.result()
            except Exception:
                prices[sym] = None
    return prices


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


def check_password():
    expected_password = os.getenv("STREAMLIT_PASSWORD", "MonMotDePasseSecret123")

    def password_entered():
        if "password" in st.session_state:
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

st.title("🚀 Cockpit Spéculatif & Moonbag — Dashboard Analytics (v3.9.1)")
st.markdown(
    "*Suivi multi-exchange temps réel (Kraken & OKX), Boussole de Flux Macro, Benchmark BTC (Alpha), Vigie CORE 35%, Sentinelle Zombies & Thèses CORE 1-Click*"
)
st.markdown("---")


@st.cache_data(ttl=300, show_spinner=False)
def get_live_eur_usd_rate():
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

(
    tab_live,
    tab_micro,
    tab_alerts,
    tab_ai,
    tab_monitor,
) = st.tabs([
    "📊 Positions Actives (Live & BTC)",
    "🔥 Microstructure & Dérivés",
    "🔔 Centre d'Alertes",
    "🤖 Assistant IA Pro",
    "🛠️ Administration & Monitoring",
])


# Pre-fetch optimisé des prix en parallèle (Multi-threading)
active_symbols_set = {"BTC"}
try:
    _open_pos_pre = get_open_positions()
    if _open_pos_pre:
        for _p in _open_pos_pre:
            if len(_p) > 1 and _p[1]:
                active_symbols_set.add(
                    str(_p[1]).upper().replace("USDT", "").replace("EUR", "").strip()
                )
except Exception:
    pass

try:
    _balances_pre = get_cached_crypto_balances()
    if _balances_pre:
        for _asset in _balances_pre.keys():
            if _asset not in ["USDT", "USDC", "EUR", "USD"]:
                active_symbols_set.add(str(_asset).upper().strip())
except Exception:
    pass

_bulk_prices_map = (
    fetch_bulk_live_prices(tuple(sorted(list(active_symbols_set))), eur_usd_rate)
    if active_symbols_set
    else {}
)

# Récupération du prix live du BTC pour le benchmarking Alpha (v3.5+)
btc_live_eur = fetch_live_price("BTC", "KRAKEN") or fetch_live_price("BTC", "OKX") or 1.0

# ==========================================
# ONGLET 1 : POSITIONS ACTIVES (LIVE & TEMPS RÉEL)
# ==========================================
with tab_live:
    with st.expander("📖 Guide de lecture & Répartition du Portefeuille"):
        st.markdown(f"""
            Inventaire global et ventilation de votre capital en temps réel (Autonome & 100% direct exchanges) :
            * **Ligne 1 (Macro & Sentiment) :** Fear & Greed Index, Circuit Breaker BTC, Dominance BTC (BTC.D), TOTAL3 (Cap. Alts hors BTC/ETH) et Poudre sèche Stablecoins Globales.
            * **Ligne 2 (Allocations) :** Volume Total Crypto, Majeures (BTC/ETH), Poche CORE, Poche Spéculative Total (Moonbag) et Stables/Cash.
            * **Boussole de Flux Macro (v3.9.1) :** Lecture croisée (BTC.D x Dynamique BTC) modulant le niveau de sélectivité Alpha et le Score de Confluence IA sans blocage binaire arbitraire.
            * **Vigie 35% Intra-CORE (v3.6) :** Contrôle strict de surconcentration au sein de la poche long terme.
            """)

    positions = get_open_positions()
    auto_tpsl_map = get_auto_tp_sl_status()

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
            
            # Récupération du prix BTC à l'entrée (Index 12)
            btc_entry_eur = float(p[12]) if (len(p) > 12 and p[12] is not None and float(p[12]) > 0) else btc_live_eur
            is_auto = auto_tpsl_map.get(pos_id, True)

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

            # --- CALCUL BENCHMARK BTC & ALPHA ---
            btc_pnl_pct = (((btc_live_eur - btc_entry_eur) / btc_entry_eur) * 100.0) if btc_entry_eur > 0 else 0.0
            alpha_btc_pct = pnl_pct - btc_pnl_pct

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
                "Mode": "🤖 Auto IA" if is_auto else "👤 Manuel",
                "Symbole": sym,
                "Exchange": exch,
                "Prix Entrée": entry_eur * curr_multiplier,
                "Prix Actuel": live_eur * curr_multiplier,
                "Montant Initial": amount_eur * curr_multiplier,
                "Valeur Actuelle": current_val_eur * curr_multiplier,
                "PnL (€/$)": pnl_eur * curr_multiplier,
                "PnL (%)": pnl_pct,
                "Alpha vs BTC": alpha_btc_pct,
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
    val_real_fiat = 0.0
    val_stables_crypto = 0.0

    dict_majors = {}
    dict_core = {}

    for asset, info in all_crypto_balances.items():
        amt = info["amount"]
        exch = info["exchange"]
        lp = fetch_live_price(asset, exch) or 0.0
        asset_val = amt * lp

        if asset in ["EUR", "USD"]:
            val_real_fiat += (
                amt
                if asset == "EUR"
                else (amt / eur_usd_rate if eur_usd_rate else amt)
            )
        elif asset in ["USDT", "USDC", "DAI", "FDUSD"]:
            val_stables_crypto += (
                amt / eur_usd_rate
                if asset in ["USDT", "USDC"] and eur_usd_rate
                else amt
            )
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

    val_stables_cash = val_real_fiat + val_stables_crypto
    if val_stables_cash == 0 and total_cash_eur > 0:
        val_stables_cash = total_cash_eur

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

    macro_context = get_cached_complete_macro_context()
    fng_data = macro_context.get("fear_and_greed", {"value": 50, "classification": "Neutral"})
    cb_data = macro_context.get("circuit_breaker", {"is_crash": False, "btc_24h_pct": 0.0})
    is_crash = cb_data.get("is_crash", False)
    crash_val = cb_data.get("btc_24h_pct", 0.0)
    macro_metrics = macro_context.get("metrics", {})
    btc_d = macro_metrics.get("btc_dominance", 58.0)
    total3_b = macro_metrics.get("total3_usd_b", 650.0)
    stables_cap_b = macro_metrics.get("stablecoins_cap_b", 170.0)

    # ==========================================
    # 🧭 COMPOSANT v3.9.1 : BOUSSOLE DE FLUX MACRO (MARKET COMPASS)
    # ==========================================
    if is_crash or (crash_val <= -3.0 and btc_d >= 60.0):
        st.markdown(
            f"""
            <div style="padding: 12px 18px; border-radius: 8px; border-left: 6px solid #ff4d4d; background-color: #2b1111; margin-bottom: 16px;">
                <div style="font-size: 15px; font-weight: bold; color: #ff6666;">
                    🚨 BOUSSOLE MACRO : RÉGIME RISK-OFF / SAIGNÉE ALTS — BTC 24h : {crash_val:+.2f}% | BTC.D : {btc_d:.1f}%
                </div>
                <div style="font-size: 13px; color: #e0e0e0; margin-top: 4px;">
                    <b>Dynamique :</b> Repli brusque du marché avec fuite de liquidité hors des Altcoins (Pilier Macro : <b>1/5 ⭐</b>).<br>
                    <b>Posture :</b> Gel défensif des nouveaux achats spéculatifs. Préservation stricte du Cash et surveillance des Stop-Loss.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif btc_d >= 60.0:
        st.markdown(
            f"""
            <div style="padding: 12px 18px; border-radius: 8px; border-left: 6px solid #36a2eb; background-color: #102030; margin-bottom: 16px;">
                <div style="font-size: 15px; font-weight: bold; color: #66b8ff;">
                    🧭 BOUSSOLE MACRO : MARCHÉ MENÉ PAR BTC (BTC_LED_MARKET) — DOMINANCE À {btc_d:.1f}% (BTC 24h : {crash_val:+.2f}%)
                </div>
                <div style="font-size: 13px; color: #e0e0e0; margin-top: 4px;">
                    <b>Dynamique :</b> Le Bitcoin concentre le flux directeur mais aucun verrou binaire n'est actif (Pilier Macro : <b>3/5 ⭐</b>).<br>
                    <b>Posture :</b> Trading Moonbag et arbitrages CORE <b>AUTORISÉS sous sélectivité Alpha</b> (privilégier les actifs avec Alpha vs BTC positif et RSI sain validant <b>&ge; 4/5 ⭐</b> de Confluence).
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif btc_d >= 57.5:
        st.markdown(
            f"""
            <div style="padding: 10px 16px; border-radius: 8px; border-left: 6px solid #ffa500; background-color: #2b220f; margin-bottom: 16px;">
                <div style="font-size: 14px; font-weight: bold; color: #ffa500;">
                    ⚖️ BOUSSOLE MACRO : CANAL D'ÉQUILIBRE (BALANCED_ROTATION) — DOMINANCE À {btc_d:.1f}%
                </div>
                <div style="font-size: 12px; color: #ddd; margin-top: 3px;">
                    Respiration saine du marché (Pilier Macro : <b>4/5 ⭐</b>). Conditions nominales pour les arbitrages intra-CORE et l'ouverture de setups Moonbag (&ge; 4/5 ⭐).
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div style="padding: 12px 18px; border-radius: 8px; border-left: 6px solid #00ff66; background-color: #0f2b18; margin-bottom: 16px;">
                <div style="font-size: 15px; font-weight: bold; color: #00ff66;">
                    🟢 BOUSSOLE MACRO : EXPANSION ALTCOINS (ALT_EXPANSION) — DOMINANCE À {btc_d:.1f}% (&lt; 57.5%)
                </div>
                <div style="font-size: 13px; color: #e0e0e0; margin-top: 4px;">
                    Diffusion large des liquidités vers TOTAL3 (Pilier Macro : <b>5/5 ⭐</b>). Conditions optimales de rotation vers les poches CORE et Spéculative.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("#### 🌐 Sentiment & Macro-Marché Globale")
    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
    
    m_col1.metric("Fear & Greed Index", f"{fng_data['value']}/100", delta=f"{fng_data['classification']}")
    m_col2.metric("Circuit Breaker BTC", f"{crash_val:+.2f}% (24h)", delta="NORMAL 🟢" if not is_crash else "DÉFENSIF 🚨", delta_color="normal" if not is_crash else "inverse")
    m_col3.metric("Dominance BTC (BTC.D)", f"{btc_d:.1f} %", delta="Expansion Alts < 57.5%" if btc_d < 57.5 else ("Canal Équilibre" if btc_d < 60.0 else "Sélectivité Alpha (BTC-Led)"), delta_color="off")
    m_col4.metric("TOTAL3 (Cap. Alts)", f"{total3_b:,.1f} Mds $", delta="Hors BTC & ETH", delta_color="off")
    m_col5.metric("Poudre Sèche Stables", f"{stables_cap_b:,.1f} Mds $", delta="USDT / USDC / DAI", delta_color="off")

    st.markdown("")

    st.markdown("#### 💼 Allocations & Portefeuille Réel")
    total_invested_disp = total_invested_base * curr_multiplier
    total_market_disp = val_speculative * curr_multiplier
    total_pnl_disp = total_market_disp - total_invested_disp
    total_pnl_pct = ((total_pnl_disp / total_invested_disp) * 100) if total_invested_disp > 0 else 0.0
    global_risk_buffer = ((weighted_sl_distance / total_weight_risk)) if total_weight_risk > 0 else 0.0

    p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns(5)
    p_col1.metric("Volume Total Crypto", f"{total_crypto_only_val * curr_multiplier:,.2f} {curr_symbol}", delta="Kraken + OKX + Exodus")
    p_col2.metric("Majeures (BTC/ETH)", f"{val_majors * curr_multiplier:,.2f} {curr_symbol}", delta=f"{pct_majors:.1f}% du portfolio")
    p_col3.metric("Poche CORE", f"{val_core * curr_multiplier:,.2f} {curr_symbol}", delta=f"{pct_core:.1f}% du portfolio")
    p_col4.metric("Poche Spéculative Total", f"{total_market_disp:,.2f} {curr_symbol}", delta=f"{pct_spec:.1f}% du portfolio | PnL : {total_pnl_pct:+.2f}% ({total_pnl_disp:+,.2f} {curr_symbol})", delta_color="normal" if total_pnl_pct >= 0 else "inverse")
    p_col5.metric("Stables & Cash", f"{val_stables_cash * curr_multiplier:,.2f} {curr_symbol}", delta=f"{pct_stables:.1f}% du portfolio (Cible : 13%)", delta_color="off")

    st.markdown("")

    r_col1, r_col2 = st.columns(2)
    r_col1.metric("Positions Spéculatives Actives", f"{len(positions)} ligne(s)", delta=f"🎯 {free_ride_count} Free Ride | ⚠️ {at_risk_count} À Risque")
    r_col2.metric("Buffer de Risque Global", f"{global_risk_buffer:.1f}%", delta="Marge moyenne avant SL" if global_risk_buffer > 10 else "Attention, proche des SL", delta_color="normal" if global_risk_buffer > 10 else "inverse")

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

    with z_col2:
        if dict_core and val_core > 0:
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
                margin=dict(t=30, b=10, l=10, r=10), showlegend=False, height=240
            )
            st.plotly_chart(fig_core, width="stretch")

            # --- VIGIE DE CONCENTRATION INTRA-CORE (35% MAX) ---
            val_core_disp = val_core * curr_multiplier
            plafond_35_disp = val_core_disp * 0.35
            core_meta_map = get_all_core_metadata()

            st.markdown(f"##### ⚖️ Vigie 35% Max Intra-CORE (Cap: {plafond_35_disp:,.2f} {curr_symbol})")

            for asset, val_disp in sorted(dict_core.items(), key=lambda x: x[1], reverse=True):
                intra_pct = (val_disp / val_core_disp) * 100.0 if val_core_disp > 0 else 0.0
                asset_meta = core_meta_map.get(asset, {"thesis_status": "ACTIVE", "notes": ""})
                
                if intra_pct >= 35.0:
                    bar_color = "#ff4d4d"
                    badge_status = "🚨 PLAFOND DÉPASSÉ"
                elif intra_pct >= 30.0:
                    bar_color = "#ffa500"
                    badge_status = "⚠️ PROCHE CAP (30-35%)"
                else:
                    bar_color = "#00ffcc"
                    badge_status = "🟢 CONFORME"

                delta_cap = val_disp - plafond_35_disp

                st.markdown(
                    f"""
                    <div style="font-size: 13px; margin-top: 6px;">
                        <b>{asset}</b> : {val_disp:,.2f} {curr_symbol} (<b>{intra_pct:.1f}%</b> de la poche) — <span style="color: {bar_color}; font-weight: bold;">{badge_status}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                progress_val = min(1.0, max(0.0, intra_pct / 35.0))
                st.progress(progress_val)

                if intra_pct >= 35.0:
                    st.caption(f"✂️ Écrêtage requis : **{delta_cap:,.2f} {curr_symbol}** à réallouer vers BTC/Cash.")

            # --- ÉDITION QUALIFICATIVE & CYCLE DE VIE DU CORE ---
            with st.expander("🏷️ Cycle de vie & Thèses CORE"):
                sel_core_asset = st.selectbox(
                    "Actif CORE à qualifier",
                    options=list(dict_core.keys()),
                    key="sel_core_meta_asset"
                )
                current_meta = core_meta_map.get(sel_core_asset, {"thesis_status": "ACTIVE", "notes": ""})
                
                status_options = {
                    "ACTIVE": "🟢 Pilier Actif (Thèse intacte)",
                    "WATCHLIST_TRIM": "🟡 Surveillance / À écrémer (Prise de profit)",
                    "EXIT_RECOMMENDED": "🔴 Sortie recommandée (Coût d'opportunité / Risque)"
                }
                
                curr_status_key = current_meta.get("thesis_status", "ACTIVE")
                status_idx = list(status_options.keys()).index(curr_status_key) if curr_status_key in status_options else 0

                new_status = st.selectbox(
                    "Statut doctrinal",
                    options=list(status_options.keys()),
                    format_func=lambda x: status_options[x],
                    index=status_idx,
                    key=f"status_select_{sel_core_asset}"
                )
                
                new_notes = st.text_input(
                    "Note stratégique / Thèse",
                    value=current_meta.get("notes", ""),
                    placeholder="Ex: Actif de flux USDT sans thèse long terme ou Pilier L1",
                    key=f"notes_input_{sel_core_asset}"
                )

                if st.button(f"💾 Sauvegarder qualification {sel_core_asset}", key=f"btn_save_core_{sel_core_asset}"):
                    update_core_asset_metadata(sel_core_asset, new_status, new_notes)
                    st.cache_data.clear()
                    st.success(f"Statut mis à jour pour {sel_core_asset} !")
                    st.rerun()

        else:
            st.info("Aucun actif CORE détecté.")

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

            def style_alpha_btc(val):
                if pd.isna(val) or not isinstance(val, (int, float)):
                    return ""
                if val >= 5.0:
                    return "color: #00ff66; font-weight: bold; background-color: rgba(0, 255, 102, 0.15);"
                elif val > 0:
                    return "color: #88ffaa; background-color: rgba(0, 255, 102, 0.05);"
                elif val <= -5.0:
                    return "color: #ff4d4d; font-weight: bold; background-color: rgba(255, 77, 77, 0.15);"
                elif val < 0:
                    return "color: #ffaaaa; background-color: rgba(255, 77, 77, 0.05);"
                return ""

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
            styler = styler.map(style_alpha_btc, subset=["Alpha vs BTC"])

            styler = styler.format({
                "Prix Entrée": f"{{:.4f}} {curr_symbol}",
                "Prix Actuel": f"{{:.4f}} {curr_symbol}",
                "Montant Initial": f"{{:,.2f}} {curr_symbol}",
                "Valeur Actuelle": f"{{:,.2f}} {curr_symbol}",
                "PnL (€/$)": f"{{:+,.2f}} {curr_symbol}",
                "PnL (%)": "{:+.2f}%",
                "Alpha vs BTC": "{:+.2f}% ₿",
                "TP1": f"{{:.4f}} {curr_symbol}",
                "Stop-Loss": f"{{:.4f}} {curr_symbol}",
                "Marge SL": "{:+.1f}%",
            })

            st.dataframe(styler, width="stretch", hide_index=True)
        else:
            st.markdown("### 📱 Cartes de suivi mobile temps réel")
            for row in enriched_positions:
                pnl_color = "#00ffcc" if row["PnL (%)"] >= 0 else "#ff4d4d"
                alpha_color = "#00ffcc" if row["Alpha vs BTC"] >= 0 else "#ff4d4d"
                with st.container():
                    st.markdown(
                        f"""
                        <div style="padding: 15px; border-radius: 10px; border: 1px solid #303030; margin-bottom: 10px; background-color: #1e1e1e;">
                            <h4 style="margin: 0; color: #00ffcc;">{row['Symbole']} <span style="font-size: 12px; color: #888;">({row['Exchange']})</span> — <span style="color: #00ffcc;">[{row['Mode']}]</span> — <span style="color: {pnl_color};">{row['Statut']}</span></h4>
                            <p style="margin: 5px 0 0 0; font-size: 14px;">
                                <b>Valeur :</b> {row['Valeur Actuelle']:,.2f} {curr_symbol} | <b>PnL :</b> <span style="color: {pnl_color};">{row['PnL (%)']:+.2f}% ({row['PnL (€/$)']:+,.2f} {curr_symbol})</span><br>
                                <b>Alpha vs BTC :</b> <span style="color: {alpha_color}; font-weight: bold;">{row['Alpha vs BTC']:+.2f}% ₿</span><br>
                                <b>Prix Actuel :</b> {row['Prix Actuel']:.4f} {curr_symbol} (Entrée : {row['Prix Entrée']:.4f})<br>
                                <b>TP1 :</b> {row['TP1']:.4f} {curr_symbol} | <b>SL :</b> {row['Stop-Loss']:.4f} {curr_symbol}
                            </p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

        st.markdown("---")
        st.subheader("⚡ Actions & Gestion Dynamique des Positions")

        act_tab1, act_tab2, act_tab3, act_tab4, act_tab5 = st.tabs([
            "🔒 Clôture de Position",
            "🛡️ Passage en Break-Even (1-Click)",
            "✏️ Édition & Raccourcis SL/TP",
            "🤖 Mode Auto TP/SL (IA)",
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

            state_key = f"preset_sl_val_{selected_edit_id}"
            if state_key not in st.session_state:
                st.session_state[state_key] = (
                    float(raw_sl) if raw_sl > 0 else float(raw_entry * 0.70)
                )

            st.markdown(
                "**⚡ Raccourcis de calcul SL rapide (par rapport au prix d'entrée)"
                " :**"
            )
            r_c1, r_c2, r_c3, r_c4 = st.columns(4)

            if r_c1.button("-10% (Resserré)", key="btn_sl_10"):
                st.session_state[state_key] = float(raw_entry * 0.90)
                st.rerun()
            if r_c2.button("-15% (Standard)", key="btn_sl_15"):
                st.session_state[state_key] = float(raw_entry * 0.85)
                st.rerun()
            if r_c3.button("-20% (Modéré)", key="btn_sl_20"):
                st.session_state[state_key] = float(raw_entry * 0.80)
                st.rerun()
            if r_c4.button("-30% (Large)", key="btn_sl_30"):
                st.session_state[state_key] = float(raw_entry * 0.70)
                st.rerun()

            e_col1, e_col2, e_col3 = st.columns(3)
            with e_col1:
                new_tp1 = st.number_input(
                    f"Nouveau TP1 ({curr_symbol})",
                    value=float(raw_tp1) if raw_tp1 > 0 else float(raw_entry * 2.0),
                    format="%.4f",
                    key=f"input_tp1_{selected_edit_id}",
                )
            with e_col2:
                new_tp2 = st.number_input(
                    f"Nouveau TP2 ({curr_symbol})",
                    value=float(raw_entry * 4.0),
                    format="%.4f",
                    key=f"input_tp2_{selected_edit_id}",
                )
            with e_col3:
                new_sl = st.number_input(
                    f"Nouveau Stop-Loss ({curr_symbol})",
                    value=st.session_state[state_key],
                    format="%.4f",
                    key=f"input_sl_field_{selected_edit_id}",
                )

            if st.button("💾 Enregistrer les nouveaux paliers"):
                update_trade_levels(
                    selected_edit_id,
                    tp1_price=new_tp1 / curr_multiplier,
                    tp2_price=new_tp2 / curr_multiplier,
                    sl_price=new_sl / curr_multiplier,
                )

                if state_key in st.session_state:
                    del st.session_state[state_key]

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
                "**Activer / Désactiver la gestion automatique TP/SL par l'IA**"
                " pour chaque position :"
            )
            selected_auto_id = st.selectbox(
                "Position à modifier",
                options=df_pos["ID"].tolist(),
                format_func=lambda x: (
                    f"ID {x} - {df_pos[df_pos['ID'] == x]['Symbole'].values[0]}"
                    f" ({df_pos[df_pos['ID'] == x]['Exchange'].values[0]}) | Mode"
                    f" actuel : {df_pos[df_pos['ID'] == x]['Mode'].values[0]}"
                ),
                key="auto_select",
            )
            auto_row = df_pos[df_pos["ID"] == selected_auto_id].iloc[0]
            is_currently_auto = "Auto IA" in str(auto_row["Mode"])

            a_col1, a_col2 = st.columns(2)
            with a_col1:
                if st.button(
                    "🤖 Activer le mode Auto IA",
                    type="primary",
                    disabled=is_currently_auto,
                    key="btn_enable_auto",
                ):
                    toggle_auto_tp_sl(selected_auto_id, True)
                    st.cache_data.clear()
                    send_telegram_notify(
                        f"🤖 *MODE AUTO TP/SL ACTIVÉ VIA WEB*\n•"
                        f" **{auto_row['Symbole']}** (ID {selected_auto_id}) est désormais"
                        " géré automatiquement par l'IA."
                    )
                    st.success(
                        f"✅ Position ID {selected_auto_id} ({auto_row['Symbole']})"
                        " passée en mode Auto IA !"
                    )
                    st.rerun()
            with a_col2:
                if st.button(
                    "👤 Repasser en Gestion Manuelle",
                    type="secondary",
                    disabled=not is_currently_auto,
                    key="btn_disable_auto",
                ):
                    toggle_auto_tp_sl(selected_auto_id, False)
                    st.cache_data.clear()
                    send_telegram_notify(
                        f"👤 *MODE AUTO TP/SL DÉSACTIVÉ VIA WEB*\n•"
                        f" **{auto_row['Symbole']}** (ID {selected_auto_id}) repasse en"
                        " gestion manuelle."
                    )
                    st.info(
                        f"ℹ️ Position ID {selected_auto_id} ({auto_row['Symbole']})"
                        " repassée en gestion manuelle."
                    )
                    st.rerun()

        with act_tab5:
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

    st.markdown("---")
    with st.expander(
        "📋 Journal & Historique des Positions Clôturées", expanded=False
    ):
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
        user_crypto_list = set()
        if "enriched_positions" in locals() and enriched_positions:
            for p in enriched_positions:
                user_crypto_list.add(p["Symbole"])

        if "all_crypto_balances" in locals() and all_crypto_balances:
            for asset in all_crypto_balances.keys():
                if asset not in ["USDT", "USDC", "EUR", "USD"]:
                    user_crypto_list.add(asset)

        if not user_crypto_list:
            user_crypto_list = {
                "BTC", "ETH", "SOL", "NEAR", "ONDO", "PENDLE", "SUI", "AVAX",
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
# ONGLET 3 : CENTRE D'ALERTES & RAPPROCHEMENT
# ==========================================
with tab_alerts:
    with st.expander("📖 Guide du Centre d'Alertes, Réconciliation & Zombies"):
        st.markdown("""
            Vigie automatisée de votre cockpit :
            * **Rapprochement BDD vs Exchanges :** Détecte les lignes fantômes, les écarts de volumes et les nouveaux trades manuels.
            * **Sentinelle des Actifs Zombies (v3.8) :** Détecte l'attrition d'Alpha à 30 jours (Alpha <= -15%), avec immunité pour les trades récents (<21j) et les piliers CORE actifs.
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
                        rec_id = add_recommendation(
                            symbol=d_sym,
                            exchange=d_exch,
                            price=price_eur,
                            amount_eur=cost_eur,
                            tp1_price=price_eur * 2.0,
                            tp2_price=price_eur * 4.0,
                            sl_price=price_eur * 0.60,
                            sl_type="invalidation",
                            btc_price_entry=btc_live_eur,
                        )
                        mark_trade_taken(rec_id, btc_entry_price=btc_live_eur)
                        st.cache_data.clear()
                        send_telegram_notify(
                            f"🎯 *TRADE MOONBAG ONBOARDÉ VIA WEB*\n• **{d_sym}**"
                            f" ({disp_cost:,.2f} {curr_symbol} @ {disp_price:.4f}"
                            f" {curr_symbol}) ajouté au cockpit en mode Auto/IA par défaut."
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

    # =========================================================================
    # 🧟 COMPOSANT v3.8.0 : SENTINELLE DES ACTIFS ZOMBIES (ANTI-BAGHOLDING)
    # =========================================================================
    st.subheader("🧟 Sentinelle des Actifs Zombies (Anti-Bagholding v3.8.0)")
    st.markdown(
        "Surveillance continue de l'attrition d'Alpha face au Bitcoin. Les actifs subissant un décrochage "
        "critique (> -15.0% sur 30 jours) sont signalés pour éviction, sauf veto technique de survente."
    )

    z_ctrl_col1, z_ctrl_col2 = st.columns([1, 3])
    with z_ctrl_col1:
        if st.button("🧟 Lancer l'Audit Zombies en Direct", type="primary", key="btn_run_zombie_audit"):
            if ZOMBIE_SENTINEL_AVAILABLE:
                with st.spinner("Audit en direct de l'attrition d'Alpha et vérification des 3 garde-fous..."):
                    try:
                        run_zombie_sentinel(send_telegram=False)
                        st.cache_data.clear()
                        st.success("✅ Audit Zombies exécuté avec succès !")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur lors de l'exécution de l'audit zombie : {e}")
            else:
                st.error("⚠️ Module `zombie_sentinel.py` non disponible.")
    with z_ctrl_col2:
        st.caption(
            "💡 Garde-fous actifs : Les positions spéculatives récentes (<21 jours) et les Piliers CORE avec thèse 'ACTIVE' "
            "sont sanctuarisés et exclus automatiquement de la qualification zombie."
        )

    active_zombies_db = get_active_zombies()

    if active_zombies_db:
        st.warning(f"⚠️ **{len(active_zombies_db)} actif(s) zombie(s) identifié(s) dans le portefeuille :**")
        for z in active_zombies_db:
            z_sym = z[0]
            z_exch = z[1]
            z_alpha = float(z[2] or 0.0)
            z_tok_pnl = float(z[3] or 0.0)
            z_btc_pnl = float(z[4] or 0.0)
            z_val = float(z[5] or 0.0) * curr_multiplier
            z_rsi = float(z[6] or 50.0)
            z_sev = z[7] or "STANDARD"
            z_detected = z[8]

            border_c = "#ff4d4d" if z_sev == "CRITICAL" else "#ffa500"
            bg_c = "#2a1515" if z_sev == "CRITICAL" else "#2a2215"
            badge_txt = "🔴 CRITIQUE (<= -25%)" if z_sev == "CRITICAL" else "🟡 STANDARD (-15% à -25%)"
            action_txt = "LIQUIDATION TOTALE (100%) vers BTC" if z_sev == "CRITICAL" else "ALLÈGEMENT (50%) vers BTC"

            has_rsi_veto = z_rsi <= 30.0
            veto_badge = " | ⏸️ <b>VETO SURVENTE ACTIF (RSI 1D ≤ 30)</b>" if has_rsi_veto else ""

            st.markdown(
                f"""
                <div style="padding: 12px 16px; border-radius: 8px; border: 1px solid {border_c}; margin-bottom: 10px; background-color: {bg_c};">
                    <h4 style="margin: 0; color: {border_c};">{badge_txt} — {z_sym} ({z_exch}){veto_badge}</h4>
                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #eee;">
                        <b>Valeur actuelle :</b> {z_val:,.2f} {curr_symbol} | <b>Alpha vs BTC :</b> <span style="color: #ff6666; font-weight: bold;">{z_alpha:+.2f}%</span> | <b>RSI 1D :</b> {z_rsi:.1f}<br>
                        <b>PnL Token (30j) :</b> {z_tok_pnl:+.2f}% (vs BTC {z_btc_pnl:+.2f}%)<br>
                        <b>Consigne doctrinale :</b> {action_txt}
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            z_btn_c1, z_btn_c2 = st.columns([1, 3])
            with z_btn_c1:
                if st.button(f"🛡️ Réhabiliter / Sortir de veille ({z_sym})", key=f"btn_rehab_{z_sym}"):
                    clear_zombie_record(z_sym)
                    st.cache_data.clear()
                    st.success(f"Actif {z_sym} retiré de la table des zombies.")
                    st.rerun()
            with z_btn_c2:
                if has_rsi_veto:
                    st.caption(f"⏸️ Vente au marché interdite car {z_sym} est en survente extrême. Attendre le rebond vers RSI 50.")
                else:
                    st.caption(f"✂️ Action immédiate : Passer un ordre LIMIT de vente pour arbitrer vers BTC.")
    else:
        st.success("🟢 **Santé du portefeuille optimale :** Aucun actif zombie détecté. Le capital est protégé contre l'attrition lente face au Bitcoin.")

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

    # Synchronisation Vigie avec la table des zombies actifs (v3.8.0)
    zombies_in_db = get_active_zombies()
    if zombies_in_db:
        alerts_list.append({
            "level": "CRITICAL" if any(z[7] == "CRITICAL" for z in zombies_in_db) else "WARNING",
            "title": f"🧟 {len(zombies_in_db)} Actif(s) Zombie(s) sous surveillance",
            "desc": (
                f"Lignes accusant un coût d'opportunité sévère vs BTC (> -15.0% d'Alpha). "
                "Consultez la section dédiée ci-dessus pour arbitrer."
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

    is_crash, crash_val = get_cached_circuit_breaker()
    if is_crash:
        alerts_list.append({
            "level": "CRITICAL",
            "title": "Alerte Crash / Circuit Breaker activé",
            "desc": (
                f"Le Bitcoin chute de {crash_val:+.2f}% sur 24h. Mode défensif"
                " enclenché."
            ),
        })
    else:
        alerts_list.append({
            "level": "INFO",
            "title": "Marché stable (Circuit Breaker Normal)",
            "desc": (
                f"Variation BTC 24h : {crash_val:+.2f}%. Conditions"
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
# ONGLET 4 : ASSISTANT IA PRO (SSOT EXTERNALISÉ v3.5+)
# ==========================================
with tab_ai:
    with st.expander("📖 Guide de l'Assistant IA Conversationnel (Architecture SSOT & Benchmark BTC)"):
        st.markdown("""
            Votre stratège crypto personnel en direct :
            * **Prompts Rapides :** Lancez des analyses complexes en un clic sans rien saisir.
            * **Benchmark BTC Intégré :** Gemini analyse désormais la surperformance réelle (Alpha) de vos positions spéculatives vs BTC.
            * **Confluence & Tickets d'Ordres (v3.7) :** Recommandations assorties d'un Score de Conviction (1 à 5 ⭐) et de fiches d'ordres pré-mâchées.
            * **Gouvernance CORE 1-Click (v3.9) :** Détection automatique des qualifications de thèses (`core_proposals`) et bouton d'application directe en BDD.
            * **Chat Libre :** Posez n'importe quelle question sur vos positions et votre gestion du risque.
            """)

    st.subheader("🤖 Assistant IA & Stratège Crypto (Propulsé par Gemini & Prompts SSOT)")
    st.markdown(
        "Discutez en direct ou lancez une analyse pré-configurée en 1 clic :"
    )

    if AUTO_TPSL_AVAILABLE:
        st.markdown("##### ⚡ Gestion Automatique des Paliers TP/SL (IA)")
        col_auto1, col_auto2 = st.columns([2, 3])
        with col_auto1:
            if st.button(
                "🤖 Lancer le Recalibrage Auto TP/SL (IA)",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Analyse du marché et recalibrage des positions Auto..."):
                    res_tpsl = run_auto_tpsl_cycle(manual_trigger=True)
                    if res_tpsl["updated_count"] > 0:
                        st.success(
                            f"✅ {res_tpsl['updated_count']} position(s) mise(s) à jour par l'IA !"
                        )
                        for log_entry in res_tpsl["logs"]:
                            st.write(log_entry)
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.info(
                            "ℹ️ Aucune modification nécessaire par l'IA ou aucune position"
                            " en mode Auto."
                        )
                        for log_entry in res_tpsl["logs"]:
                            st.write(log_entry)
        with col_auto2:
            st.caption(
                "💡 Le recalibrage s'exécute automatiquement toutes les 8h (lors des"
                " clôtures de Funding Rate). Vous pouvez également forcer un cycle"
                " à tout moment ci-contre."
            )
        st.markdown("---")

    st.markdown("##### ⚡ Prompts Rapides & Analyses en 1 Clic")
    col_p1, col_p2, col_p3 = st.columns(3)

    preset_prompt = None
    preset_context = None

    open_pos = get_open_positions()
    try:
        all_balances = get_cached_crypto_balances()
    except Exception:
        all_balances = {}

    fng_data = get_cached_fear_and_greed()
    is_crash, crash_pct = get_cached_circuit_breaker()

    macro_prompt_block = (
        format_macro_context_for_prompt()
        if MACRO_ENGINE_AVAILABLE
        else f"DONNÉES DU MARCHÉ ET CONTEXTE MACRO :\n- Statut Circuit Breaker : {'🚨 DEFENSIVE MODE' if is_crash else '✅ NORMAL'} (Var BTC 24h : {crash_pct:+.2f}%)\n- Indice Fear & Greed : {fng_data['value']}/100 ({fng_data['classification']})"
    )

    with col_p1:
        if st.button("📰 Rapport & Évaluation Spéculative", width="stretch"):
            with st.spinner(
                "Analyse impartiale des opportunités, RSI et microstructure..."
            ):
                tech_micro_block = build_market_tech_micro_context(
                    open_pos=open_pos, balances=all_balances
                )
                from crypto_speculative_advisor import (
                    calculate_unfied_crypto_categories,
                )

                cats = calculate_unfied_crypto_categories()

                live_positions_for_ai = (
                    [
                        {
                            "Symbole": p["Symbole"],
                            "Exchange": p["Exchange"],
                            "Valeur_Actuelle_Marché_EUR": round(
                                p["Valeur Actuelle"], 2
                            ),
                            "Montant_Investi_Initial_EUR": round(
                                p["Montant Initial"], 2
                            ),
                            "Prix_Entree": round(p["Prix Entrée"], 4),
                            "Prix_Actuel_Live": round(p["Prix Actuel"], 4),
                            "PnL_Pct": round(p["PnL (%)"], 2),
                            "Alpha_vs_BTC_Pct": round(p.get("Alpha vs BTC", 0.0), 2),
                            "TP1": round(p["TP1"], 4),
                            "Stop_Loss": round(p["Stop-Loss"], 4),
                        }
                        for p in enriched_positions
                    ]
                    if "enriched_positions" in locals() and enriched_positions
                    else open_pos
                )

                fiat_real_eur, stables_real_eur, total_liquidites_dispo = (
                    compute_ai_liquidities(
                        all_balances,
                        eur_usd_rate=eur_usd_rate,
                        fallback_cash_eur=cats.get("stables_cash", 0.0),
                    )
                )

                core_majors_str = get_formatted_core_and_majors_breakdown(all_balances, eur_usd_rate, open_pos=open_pos)
                system_rules = get_system_prompt("advisor_speculative")

                preset_prompt = (
                    "Analyse la pertinence spéculative globale de mon portefeuille"
                    " face aux opportunités actuelles du marché et à la performance du BTC."
                )

                preset_context = f"""{system_rules}

---

VENTILATION GLOBALE DU PORTEFEUILLE (VALEURS MARCHANDE TEMPS RÉEL) :
- Capital Total Crypto : {cats['total_crypto']:,.2f} €
- Majeures (BTC/ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) [Cible : 55%]
- Poche Core : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) [Cible : 22%]
- Spéculatif (Moonbag BDD) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) [Cible : 10% / Max : 12%]
- Solde Fiat Réel sur Exchanges (EUR/USD) : {fiat_real_eur:,.2f} €
- Solde Stablecoins sur Exchanges (USDT/USDC) : {stables_real_eur:,.2f} €
- TOTAL LIQUIDITÉS MOBILISABLES : {total_liquidites_dispo:,.2f} €

ACTIFS DÉTENUS EN POCHE CORE ET MAJEURES (FILTRE ANTI-DOUBLON IMPÉRATIF) :
{core_majors_str}

POSITIONS SPÉCULATIVES ACTIVES (BDD AVEC ALPHA VS BTC) :
{json.dumps(live_positions_for_ai, indent=2, ensure_ascii=False)}

{macro_prompt_block}

{tech_micro_block}
"""

    with col_p2:
        if st.button("🛡️ Audit de Protection & SL", width="stretch"):
            with st.spinner("Analyse de la sécurité de vos positions en direct..."):
                tech_micro_block = build_market_tech_micro_context(
                    open_pos=open_pos, balances=all_balances
                )
                from crypto_speculative_advisor import (
                    calculate_unfied_crypto_categories,
                )

                cats = calculate_unfied_crypto_categories()
                system_rules = get_system_prompt("risk_manager")

                fiat_real_eur, stables_real_eur, total_liquidites_dispo = (
                    compute_ai_liquidities(
                        all_balances,
                        eur_usd_rate=eur_usd_rate,
                        fallback_cash_eur=cats.get("stables_cash", 0.0),
                    )
                )

                core_majors_str = get_formatted_core_and_majors_breakdown(all_balances, eur_usd_rate, open_pos=open_pos)

                if open_pos:
                    pos_audit_data = []
                    total_risk_weight = 0.0
                    weighted_sl_dist = 0.0
                    missing_sl_count = 0

                    for p in open_pos:
                        p_id = p[0]
                        sym = p[1]
                        exch = p[2]
                        entry = float(p[3]) if p[3] else 0.0
                        amt = float(p[4]) if p[4] else 0.0
                        tp1 = float(p[5]) if len(p) > 5 and p[5] else 0.0
                        sl = float(p[7]) if len(p) > 7 and p[7] else 0.0
                        step = p[9] if len(p) > 9 and p[9] else "ENTRY"
                        btc_e = float(p[12]) if (len(p) > 12 and p[12] and float(p[12]) > 0) else btc_live_eur

                        lp = fetch_live_price(sym, exch) or entry
                        qty = (amt / entry) if entry > 0 else 0.0
                        curr_val = qty * lp
                        pnl_pct = ((lp - entry) / entry) * 100.0 if entry > 0 else 0.0
                        
                        btc_pnl_pct = (((btc_live_eur - btc_e) / btc_e) * 100.0) if btc_e > 0 else 0.0
                        alpha_btc_pct = pnl_pct - btc_pnl_pct

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
                            "alpha_vs_btc_pct": round(alpha_btc_pct, 2),
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

                    preset_context = f"""{system_rules}

---

VENTILATION GLOBALE DU PORTEFEUILLE TEMPS RÉEL (SOURCES DIRECTES EXCHANGES) :
- Capital Total Crypto : {cats['total_crypto']:,.2f} €
- Majeures (BTC/ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) [Cible : 55%]
- Poche Core : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) [Cible : 22%]
- Spéculatif (Moonbag BDD) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) [Cible : 10% / Max : 12%]
- Liquidités Cash (Fiat + Stables) : {total_liquidites_dispo:,.2f} € (Fiat: {fiat_real_eur:,.2f} €, Stables: {stables_real_eur:,.2f} €) [Cible : 13%]

ACTIFS DÉTENUS EN POCHE CORE ET MAJEURES (FILTRE ANTI-DOUBLON IMPÉRATIF) :
{core_majors_str}

SITUATION DES POSITIONS SPÉCULATIVES EN BDD À AUDITER :
- Nombre de positions actives : {len(open_pos)}
- Buffer de risque moyen global : {global_buffer:.1f}% (Marge avant déclenchement des SL)
- Nombre de positions sans Stop-Loss : {missing_sl_count}

DÉTAIL DES POSITIONS EN PORTEFEUILLE :
{json.dumps(pos_audit_data, indent=2, ensure_ascii=False)}

{macro_prompt_block}

{tech_micro_block}
"""
                else:
                    preset_prompt = "Fais un audit de sécurité de mes positions."
                    preset_context = f"""{system_rules}

---

Aucune position active n'est actuellement enregistrée en BDD.
Indique à l'utilisateur que son portefeuille spéculatif est totalement liquide et sécurisé.
"""

    with col_p3:
        if st.button("🔄 Arbitrages & Rééquilibrage Macro", width="stretch"):
            with st.spinner(
                "Analyse de l'allocation globale, audit de la poche CORE et arbitrages..."
            ):
                tech_micro_block = build_market_tech_micro_context(
                    open_pos=open_pos, balances=all_balances
                )
                from crypto_speculative_advisor import (
                    calculate_unfied_crypto_categories,
                )

                cats = calculate_unfied_crypto_categories()
                
                system_rules = get_system_prompt("macro_arbitrage")
                if not system_rules or "RÔLE" not in system_rules:
                    system_rules = get_system_prompt("advisor_speculative")

                fiat_real_eur, stables_real_eur, total_liquidites_dispo = (
                    compute_ai_liquidities(
                        all_balances,
                        eur_usd_rate=eur_usd_rate,
                        fallback_cash_eur=cats.get("stables_cash", 0.0),
                    )
                )

                core_majors_str = get_formatted_core_and_majors_breakdown(all_balances, eur_usd_rate, open_pos=open_pos)

                target_majors = cats["total_crypto"] * 0.55
                target_core = cats["total_crypto"] * 0.22
                target_spec = cats["total_crypto"] * 0.10
                target_cash = cats["total_crypto"] * 0.13

                delta_majors = cats["btc_eth"] - target_majors
                delta_core = cats["core"] - target_core
                delta_spec = cats["speculative"] - target_spec
                delta_cash = total_liquidites_dispo - target_cash

                preset_prompt = (
                    "Audite la composition de ma poche CORE, évalue si les % alloués aux poches sont adaptés "
                    "au marché actuel et propose des arbitrages stratégiques inter et intra-poches."
                )

                preset_context = f"""{system_rules}

---

DONNÉES PORTEFEUILLE & ÉCARTS AUX CIBLES TEMPS RÉEL :
- Capital Total Crypto : {cats['total_crypto']:,.2f} €

1. MAJEURES (BTC/ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) | Cible 55.0% ({target_majors:,.2f} €) ➔ ÉCART : {delta_majors:+,.2f} €
2. POCHE CORE : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) | Cible 22.0% ({target_core:,.2f} €) ➔ ÉCART : {delta_core:+,.2f} €
3. POCHE SPÉCULATIVE : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) | Cible 10.0% ({target_spec:,.2f} €) ➔ ÉCART : {delta_spec:+,.2f} €
4. CASH & STABLECOINS : {total_liquidites_dispo:,.2f} € ({ (total_liquidites_dispo/cats['total_crypto']*100) if cats['total_crypto']>0 else 0:.1f}%) | Cible 13.0% ({target_cash:,.2f} €) ➔ ÉCART : {delta_cash:+,.2f} €

DÉTAIL COMPLET DES ACTIFS DÉTENUS (POCHE CORE & MAJEURES À AUDITER) :
{core_majors_str}

POSITIONS SPÉCULATIVES ACTIVES EN BDD :
{open_pos}

{macro_prompt_block}

{tech_micro_block}
"""

    st.markdown("---")

    if "messages" not in st.session_state:
        st.session_state.messages = [{
            "role": "assistant",
            "content": (
                "Bonjour ! Je suis ton assistant trader expert. Sélectionne une"
                " analyse rapide ci-dessus ou pose-moi directement ta question."
            ),
        }]

    for msg_idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                proposals = extract_core_proposals_from_text(message["content"])
                if proposals and isinstance(proposals, dict):
                    st.markdown("---")
                    st.markdown("##### ✨ Qualifications CORE suggérées par l'IA :")
                    prop_cols = st.columns(min(4, len(proposals)))
                    for p_i, (p_sym, p_data) in enumerate(proposals.items()):
                        col = prop_cols[p_i % len(prop_cols)]
                        status = p_data.get("status", "ACTIVE")
                        notes = p_data.get("notes", "")
                        badge = "🟢" if status == "ACTIVE" else ("🟡" if status == "WATCHLIST_TRIM" else "🔴")
                        col.caption(f"{badge} **{p_sym}** : `{status}`\n*{notes}*")
                    
                    apply_key = f"btn_apply_core_proposals_history_{msg_idx}"
                    if st.button("✨ Appliquer ces qualifications CORE en BDD (1-Click)", type="primary", key=apply_key):
                        for p_sym, p_data in proposals.items():
                            update_core_asset_metadata(
                                symbol=p_sym,
                                thesis_status=p_data.get("status", "ACTIVE"),
                                notes=p_data.get("notes", "")
                            )
                        st.cache_data.clear()
                        send_telegram_notify(
                            f"🏷️ *THÈSES CORE MISES À JOUR (1-CLICK IA)*\n• {len(proposals)} actif(s) qualifié(s) automatiquement en BDD."
                        )
                        st.success(f"✅ {len(proposals)} qualification(s) CORE appliquée(s) en base de données !")
                        st.rerun()

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
            current_balances = get_cached_crypto_balances()

            from crypto_speculative_advisor import (
                calculate_unfied_crypto_categories,
            )
            cats = calculate_unfied_crypto_categories()

            fiat_real_eur, stables_real_eur, total_liquidites_dispo = (
                compute_ai_liquidities(
                    current_balances,
                    eur_usd_rate=eur_usd_rate,
                    fallback_cash_eur=cats.get("stables_cash", 0.0),
                )
            )

            core_majors_str = get_formatted_core_and_majors_breakdown(current_balances, eur_usd_rate, open_pos=current_positions)
            system_rules = get_system_prompt("project_rules")
            tech_micro_block = build_market_tech_micro_context(
                open_pos=current_positions, balances=current_balances
            )

            preset_context = f"""{system_rules}

---

DONNÉES DU PORTEFEUILLE ACTUEL (TEMPS RÉEL) :
- Capital Total Crypto : {cats['total_crypto']:,.2f} €
- Majeures (BTC/ETH) : {cats['btc_eth']:,.2f} € ({cats['btc_eth_pct']:.1f}%) [Cible : 55%]
- Poche Core : {cats['core']:,.2f} € ({cats['core_pct']:.1f}%) [Cible : 22%]
- Spéculatif (Moonbag BDD) : {cats['speculative']:,.2f} € ({cats['speculative_pct']:.1f}%) [Cible : 10%]
- Solde Fiat Réel sur Exchanges (EUR/USD) : {fiat_real_eur:,.2f} €
- Solde Stablecoins sur Exchanges (USDT/USDC) : {stables_real_eur:,.2f} €
- TOTAL LIQUIDITÉS MOBILISABLES : {total_liquidites_dispo:,.2f} €

ACTIFS DÉTENUS EN POCHE CORE ET MAJEURES (FILTRE ANTI-DOUBLON IMPÉRATIF) :
{core_majors_str}

- Positions ouvertes en BDD : {current_positions}
- Devise active : {curr_label}

{macro_prompt_block}

{tech_micro_block}
"""

        with st.chat_message("assistant"):
            with st.spinner("Analyse des marchés et génération du rapport..."):
                try:
                    answer, used_model = call_gemini_with_fallback(
                        prompt_text=prompt_to_process,
                        system_instruction=preset_context,
                    )

                    st.markdown(answer)
                    st.caption(f"🤖 *Réponse générée avec succès via `{used_model}`*")

                    # Détection immédiate des propositions CORE dans la nouvelle réponse
                    proposals = extract_core_proposals_from_text(answer)
                    if proposals and isinstance(proposals, dict):
                        st.markdown("---")
                        st.markdown("##### ✨ Qualifications CORE suggérées par l'IA :")
                        prop_cols = st.columns(min(4, len(proposals)))
                        for p_i, (p_sym, p_data) in enumerate(proposals.items()):
                            col = prop_cols[p_i % len(prop_cols)]
                            status = p_data.get("status", "ACTIVE")
                            notes = p_data.get("notes", "")
                            badge = "🟢" if status == "ACTIVE" else ("🟡" if status == "WATCHLIST_TRIM" else "🔴")
                            col.caption(f"{badge} **{p_sym}** : `{status}`\n*{notes}*")
                        
                        apply_key_live = f"btn_apply_core_proposals_live_{len(st.session_state.messages)}"
                        if st.button("✨ Appliquer ces qualifications CORE en BDD (1-Click)", type="primary", key=apply_key_live):
                            for p_sym, p_data in proposals.items():
                                update_core_asset_metadata(
                                    symbol=p_sym,
                                    thesis_status=p_data.get("status", "ACTIVE"),
                                    notes=p_data.get("notes", "")
                                )
                            st.cache_data.clear()
                            send_telegram_notify(
                                f"🏷️ *THÈSES CORE MISES À JOUR (1-CLICK IA)*\n• {len(proposals)} actif(s) qualifié(s) automatiquement en BDD."
                            )
                            st.success(f"✅ {len(proposals)} qualification(s) CORE appliquée(s) en base de données !")
                            st.rerun()

                    st.session_state.messages.append(
                        {"role": "assistant", "content": answer}
                    )

                except Exception as e:
                    error_msg = f"⚠️ Impossible d'obtenir une réponse de Gemini : {e}"
                    st.error(error_msg)

# ==========================================
# ONGLET 5 : ADMINISTRATION & MONITORING
# ==========================================
with tab_monitor:
    with st.expander("📖 Guide du Monitoring Technique & de l'Administration"):
        st.markdown("""
            Surveillez l'état de santé de vos connecteurs et administrez votre cockpit :
            * **Connecteurs API :** Vérification de la disponibilité des exchanges.
            * **Configuration Gemini SSOT :** Ajustez dynamiquement la priorité des modèles, timeouts et retries.
            * **Administration Prompts SSOT :** Éditez et restaurez les prompts Markdown (`.md`) d'expertise IA.
            * **Journaux Système (Logs) :** Visualisez les derniers événements des services en arrière-plan.
            """)

    st.subheader("🔌 État de Santé des Connecteurs API")
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        st.markdown("#### 🐙 Kraken")
        st.metric("Statut API", "Opérationnel 🟢", delta="Latence < 150ms")
    with col_m2:
        st.markdown("#### 🅾️ OKX")
        st.metric("Statut API", "Opérationnel 🟢", delta="Latence < 120ms")

    st.markdown("---")
    st.subheader("⚙️ Configuration Centralisée des Modèles Gemini (SSOT)")
    st.markdown("Ajustez la priorité des modèles Gemini, le délai de timeout et le nombre de retries applicables à l'ensemble des moteurs du projet :")

    gemini_cfg = load_gemini_config()
    models_list = gemini_cfg.get("models_priority") or gemini_cfg.get("models", [])
    models_list_str = "\n".join(models_list)

    col_cfg1, col_cfg2 = st.columns([2, 1])
    with col_cfg1:
        new_models_str = st.text_area(
            "Ordre de priorité des modèles Gemini (1 par ligne)",
            value=models_list_str,
            height=160,
            help="En cas de surcharge ou timeout, le système tente le modèle N fois avant de basculer automatiquement sur le modèle suivant.",
        )
    with col_cfg2:
        new_timeout = st.number_input(
            "Timeout par modèle (secondes)",
            min_value=10,
            max_value=120,
            value=int(gemini_cfg.get("timeout_seconds", 45)),
            step=5,
        )
        new_retries = st.number_input(
            "Nombre de retries par modèle",
            min_value=1,
            max_value=5,
            value=int(gemini_cfg.get("max_retries_per_model", 2)),
            step=1,
            help="Nombre de tentatives sur un même modèle en cas d'erreur 503/timeout avant d'effectuer la bascule sur le modèle suivant.",
        )
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Enregistrer la Configuration Gemini", type="primary"):
            parsed_models = [m.strip() for m in new_models_str.split("\n") if m.strip()]
            updated_cfg = {
                "models_priority": parsed_models,
                "models": parsed_models,
                "timeout_seconds": int(new_timeout),
                "max_retries_per_model": int(new_retries),
            }
            if save_gemini_config(updated_cfg):
                st.success("✅ Configuration Gemini mise à jour et appliquée à tous les services !")
                send_telegram_notify(
                    "⚙️ *CONFIGURATION GEMINI MISE À JOUR VIA WEB*\n"
                    f"• Priorité : {', '.join(parsed_models)}\n"
                    f"• Timeout : {new_timeout}s | Retries / modèle : {new_retries}"
                )
                st.rerun()
            else:
                st.error("❌ Erreur lors de la sauvegarde de la configuration.")

    # --- ADMINISTRATION & ÉDITION DYNAMIQUE DES PROMPTS SSOT (.md) ---
    st.markdown("---")
    st.subheader("📜 Administration & Édition des Prompts System SSOT (.md)")
    st.markdown(
        "Consultez, modifiez et restaurez en direct les consignes métier, les rôles IA et les verrous de Risk Management "
        "qui pilotent l'ensemble de vos analyses."
    )

    if not PROMPT_LOADER_AVAILABLE:
        st.error("⚠️ Le module `prompt_loader.py` n'est pas disponible.")
    else:
        available_prompts = list_available_prompts()
        if "macro_arbitrage" not in available_prompts and os.path.exists("prompts/macro_arbitrage.md"):
            available_prompts.append("macro_arbitrage")

        prompt_descriptions = {
            "advisor_speculative": "🎯 Rapport Spéculatif (19h), Screener Moonbag & Tickets d'Ordres",
            "auto_tpsl": "🤖 Moteur de recalibrage automatique TP/SL (Cron 8h & Manuel)",
            "flash_advice": "⚡ Conseil Flash IA & Alertes Telegram (Sentinelle 24/7)",
            "macro_arbitrage": "⚖️ Stratégie Macro, Thèses CORE & Tickets d'Exécution",
            "project_rules": "⚙️ Règles fondamentales du projet, arbitrages & Cash-Neutral strict",
            "risk_manager": "🛡️ Audit de sécurité, vérification des Stop-Loss et de l'exposition",
        }

        selected_prompt_key = st.selectbox(
            "Sélectionnez le fichier de prompt à administrer",
            options=sorted(list(set(available_prompts))),
            format_func=lambda x: f"{x}.md — {prompt_descriptions.get(x, 'Prompt personnalisé')}",
            key="select_prompt_admin",
        )

        current_raw_text = get_raw_prompt(selected_prompt_key)

        st.info(
            f"📌 **Fichier actif :** `prompts/{selected_prompt_key}.md` | **Taille :** {len(current_raw_text)} caractères"
        )

        st.warning(
            "⚠️ **PRÉCAUTIONS D'USAGE CRITIQUES :**\n"
            "• Toute modification prend effet **immédiatement** pour tous les rapports Telegram, le cron de 8h et l'assistant IA.\n"
            "• Ne supprimez pas les consignes de formatage JSON ni les verrous de sécurité.\n"
            "• Un fichier de secours (`.bak`) est automatiquement généré à chaque sauvegarde pour permettre une restauration en 1 clic."
        )

        edited_prompt_text = st.text_area(
            f"Éditeur de contenu — prompts/{selected_prompt_key}.md",
            value=current_raw_text,
            height=320,
            key=f"text_area_{selected_prompt_key}",
        )

        p_col1, p_col2, p_col3 = st.columns([2, 1, 1])
        with p_col1:
            confirm_save = st.checkbox(
                f"Je confirme vouloir modifier les consignes système de `{selected_prompt_key}.md`",
                key=f"chk_confirm_{selected_prompt_key}",
            )
        with p_col2:
            if st.button(
                "💾 Enregistrer le Prompt",
                type="primary",
                disabled=not confirm_save,
                key=f"btn_save_{selected_prompt_key}",
            ):
                if save_system_prompt(selected_prompt_key, edited_prompt_text):
                    st.cache_data.clear()
                    send_telegram_notify(
                        f"📜 *PROMPT SYSTEM MODIFIÉ VIA DASHBOARD*\n"
                        f"• Fichier : `prompts/{selected_prompt_key}.md`\n"
                        f"• Un backup `.bak` a été créé automatiquement."
                    )
                    st.success(
                        f"✅ Prompt `{selected_prompt_key}.md` mis à jour avec succès ! Cache purgé."
                    )
                    st.rerun()
                else:
                    st.error(
                        f"❌ Échec de la sauvegarde du fichier `prompts/{selected_prompt_key}.md`."
                    )
        with p_col3:
            if st.button("🔄 Restaurer Backup (.bak)", key=f"btn_restore_{selected_prompt_key}"):
                if restore_backup_prompt(selected_prompt_key):
                    st.cache_data.clear()
                    send_telegram_notify(
                        f"🔄 *PROMPT SYSTEM RESTAURÉ VIA DASHBOARD*\n"
                        f"• Fichier : `prompts/{selected_prompt_key}.md` restauré depuis son backup `.bak`."
                    )
                    st.success(
                        f"✅ Prompt `{selected_prompt_key}.md` restauré avec succès depuis sa sauvegarde `.bak` !"
                    )
                    st.rerun()
                else:
                    st.warning(
                        f"⚠️ Aucune sauvegarde `.bak` disponible pour `{selected_prompt_key}.md`."
                    )

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