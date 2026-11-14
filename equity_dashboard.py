#!/usr/bin/env python3
"""
Cockpit Patrimonial Actions, ETF & Épargne Salariale — Streamlit App v2.0.0
Évolutions v2.0.0 :
- Centre d'Alertes & Réconciliation Finary <-> BDD (détection auto nouveaux achats spéculatifs + onboarding 1-Click, écarts volume, lignes fantômes)
- Notifications Telegram temps réel sur chaque action (Onboarding, Clôture FIFO, Break-Even, Modification paliers, Prompts SSOT)
- Découverte Dynamique du Marché séparée (Vivier Spéculatif 'Actions Chocs' vs Vivier CORE ETF/Blue Chips) avec re-scan forcé
- Déclencheur manuel du recalibrage Auto TP/SL IA (equity_auto_tpsl.py)
- Guides de lecture 100 % dédiés Bourse, ETF, CTO & Épargne Salariale
"""

import json
import os
import re
import subprocess
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from dotenv import load_dotenv

from equity_db_manager import (
    init_equity_db,
    get_connection,
    get_all_classifications,
    upsert_asset_classification,
    get_open_speculative_equities,
    add_speculative_equity,
    update_equity_levels,
    set_equity_break_even,
    toggle_equity_auto_tpsl,
    close_equity_trade,
    get_closed_equity_history,
    get_all_core_etf_metadata,
    update_core_etf_metadata,
)
from equity_market_engine import (
    get_equity_macro_compass,
    fetch_bulk_equity_metrics,
)
from daily_digest_engine import (
    init_finary_session,
    fetch_finary_portfolio_section,
    extract_finary_cash_and_savings,
    extract_real_estate_and_loans,
    fetch_dividends_and_yield_metrics,
    compute_current_month_salary_breakdown,
)
from finary_uapi.user_holdings_accounts import get_holdings_accounts

try:
    from equity_reconciliation import audit_equity_database_vs_finary
    RECON_AVAILABLE = True
except ImportError:
    RECON_AVAILABLE = False

try:
    from equity_discovery_engine import get_dynamic_equity_candidates
    DISCOVERY_AVAILABLE = True
except ImportError:
    DISCOVERY_AVAILABLE = False

try:
    from equity_auto_tpsl import run_equity_auto_tpsl_cycle
    AUTO_TPSL_AVAILABLE = True
except ImportError:
    AUTO_TPSL_AVAILABLE = False

from gemini_manager import (
    call_gemini_with_fallback,
    load_gemini_config,
    save_gemini_config,
)

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

load_dotenv(override=True)

st.set_page_config(
    page_title="Cockpit Actions, ETF & Patrimoine (v2.0)",
    page_icon="🏛️",
    layout="wide",
)

st.markdown(
    """
    <style>
    pre, code {
        font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
        letter-spacing: -0.2px;
    }
    div[data-testid="stChatMessage"] pre {
        background-color: #121820 !important;
        border: 1px solid #1f3a52 !important;
        border-left: 4px solid #38bdf8 !important;
        border-radius: 6px !important;
        padding: 12px !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def send_telegram_notify(text):
    """Envoie une notification Telegram immédiate lors d'une opération sur le cockpit."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not bot_token or not user_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": user_id, "text": text, "parse_mode": "Markdown"},
            timeout=4,
        )
    except Exception:
        pass


def check_password():
    expected_password = os.getenv("STREAMLIT_PASSWORD", "MonMotDePasseSecret123")

    def password_entered():
        if "eq_password" in st.session_state:
            if st.session_state["eq_password"] == expected_password:
                st.session_state["eq_password_correct"] = True
                del st.session_state["eq_password"]
            else:
                st.session_state["eq_password_correct"] = False

    if "eq_password_correct" not in st.session_state:
        st.text_input(
            "🔒 Veuillez saisir le mot de passe pour accéder au Cockpit Actions :",
            type="password",
            on_change=password_entered,
            key="eq_password",
        )
        return False
    elif not st.session_state["eq_password_correct"]:
        st.text_input(
            "🔒 Veuillez saisir le mot de passe pour accéder au Cockpit Actions :",
            type="password",
            on_change=password_entered,
            key="eq_password",
        )
        st.error("😕 Mot de passe incorrect.")
        return False
    return True


if not check_password():
    st.stop()

init_equity_db()


def color_rsi(val):
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


@st.cache_data(ttl=300, show_spinner="Synchronisation Finary & Patrimoine en cours...")
def get_cached_finary_wealth_state():
    session = init_finary_session()
    invest_data = fetch_finary_portfolio_section(session, "investments")
    try:
        holdings_data = get_holdings_accounts(session)
    except Exception:
        holdings_data = {}

    cash_info = extract_finary_cash_and_savings(invest_data, holdings_data)
    re_data = extract_real_estate_and_loans(session, holdings_data)
    livret_bal = cash_info.get("total_savings", 16921.10)
    div_metrics = fetch_dividends_and_yield_metrics(session, livret_bal, re_data.get("monthly_rent_lmnp", 791.0))
    sal_data = compute_current_month_salary_breakdown(div_metrics, re_data)

    res_inv = invest_data.get("result", invest_data) if isinstance(invest_data, dict) else {}
    raw_lines = []
    for acc in res_inv.get("accounts", []):
        acc_name = str(acc.get("name") or "Inconnu").strip()
        securities = acc.get("bank_account_securities") or acc.get("securities") or acc.get("holdings") or []
        for item in securities:
            sec = item.get("security", {}) if isinstance(item.get("security"), dict) else {}
            name = str(sec.get("name") or item.get("name") or "Titre inconnu").strip()
            isin = str(sec.get("isin") or item.get("isin") or "").strip()
            qty = float(item.get("quantity") or 0.0)
            pru = float(item.get("buying_price") or item.get("average_buying_price") or 0.0)
            val = float(item.get("current_value") or item.get("balance") or 0.0)
            if val <= 0.5:
                continue
            asset_key = f"{isin}_{acc_name}" if isin and isin != "N/A" else f"{name}_{acc_name}"
            raw_lines.append({
                "asset_key": asset_key,
                "name": name,
                "account": acc_name,
                "isin": isin if isin != "N/A" else "",
                "quantity": qty,
                "pru_eur": pru,
                "value_eur": val,
            })

    return {
        "lines": raw_lines,
        "cash_info": cash_info,
        "re_data": re_data,
        "div_metrics": div_metrics,
        "sal_data": sal_data,
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_macro_compass():
    return get_equity_macro_compass()


@st.cache_data(ttl=180, show_spinner=False)
def get_cached_yahoo_metrics(tickers_tuple):
    return fetch_bulk_equity_metrics(list(tickers_tuple))


def extract_equity_proposals_from_text(text: str):
    if not text or "equity_proposals" not in text:
        return None
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?"equity_proposals"[\s\S]*?\})\s*```', text, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1)).get("equity_proposals")
        except Exception:
            pass
    return None


# ==============================================================================
# HEADER & SIDEBAR
# ==============================================================================
st.title("🏛️ Cockpit Actions, ETF & Patrimoine — Dashboard Analytics (v2.0.0)")
st.markdown(
    "*Pilotage par Poches (Socle Salarial LT / Cœur Indiciel MT / Spéculatif 'Actions Chocs' CT), Découverte Dynamique, Réconciliation Finary, Alertes Telegram & IA SSOT*"
)
st.markdown("---")

with st.sidebar:
    st.header("⚙️ Gouvernance Patrimoniale")
    buffer_livret = float(os.getenv("EMERGENCY_FUND_BUFFER_EUR", "13500.0"))
    st.metric("🛡️ Matelas Livret A Sanctuarisé", f"{buffer_livret:,.0f} €", delta="Intouchable")
    st.info("⛔ **Doctrine Périmètre :** Zéro nouveau PEA, Zéro Assurance-Vie. Pilotage exclusif CTO Bourso/LCL, PEE/PERCO/PERO EDF & Mon Petit Placement.")

    if st.button("⚡ Rafraîchir les données Live", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ==============================================================================
# CONSOLIDATION DES DONNÉES LIVE & CLASSIFICATION SSOT
# ==============================================================================
wealth_state = get_cached_finary_wealth_state()
finary_lines = wealth_state["lines"]
cash_info = wealth_state["cash_info"]
re_data = wealth_state["re_data"]
div_metrics = wealth_state["div_metrics"]
sal_data = wealth_state["sal_data"]

classifications = get_all_classifications()
class_map = {c["asset_key"]: c for c in classifications}

salarial_kw = ["PEE", "PEG", "PERCO", "PERO", "EGEPARGNE", "EGÉPARGNE", "PROFIL EQUILIBRE", "LBPAM", "EDF"]
etf_core_kw = ["ETF", "WORLD", "CW8", "WPEA", "S&P", "500", "STOXX", "MSCI", "LYXOR", "AMUNDI", "ISHARES", "GOLD", "ETC", "PETIT PLACEMENT", "IAM ", "21SHARES"]

new_finary_lines = []
for line in finary_lines:
    ak = line["asset_key"]
    if ak not in class_map:
        upper_ctx = f"{line['account']} {line['name']}".upper()
        if any(k in upper_ctx for k in salarial_kw):
            auto_bucket = "LT_SALARIAL"
        elif any(k in upper_ctx for k in etf_core_kw):
            auto_bucket = "MT_CORE_ETF"
        else:
            auto_bucket = "CT_SPECULATIVE"

        upsert_asset_classification(
            asset_key=ak,
            name=line["name"],
            account=line["account"],
            isin=line["isin"],
            yahoo_ticker="",
            bucket=auto_bucket,
        )
        new_finary_lines.append({**line, "auto_bucket": auto_bucket})

if new_finary_lines:
    classifications = get_all_classifications()
    class_map = {c["asset_key"]: c for c in classifications}

all_tickers = [c["yahoo_ticker"] for c in classifications if c["yahoo_ticker"]]
all_tickers.append("CW8.PA")
yahoo_metrics_map = get_cached_yahoo_metrics(tuple(sorted(set(all_tickers))))

cw8_info = yahoo_metrics_map.get("CW8.PA", {})
cw8_live_price = cw8_info.get("price", 708.8) or 708.8
cw8_24h_pct = cw8_info.get("change_24h_pct", 0.0)

val_lt_salarial = 0.0
val_mt_core = 0.0
val_ct_speculative = 0.0

dict_lt_salarial = {}
dict_mt_core = {}
dict_ct_speculative = {}
enriched_all_lines = []

for line in finary_lines:
    ak = line["asset_key"]
    meta = class_map.get(ak, {"bucket": "MT_CORE_ETF", "yahoo_ticker": "", "is_excluded": False})
    if meta.get("is_excluded"):
        continue

    bucket = meta["bucket"]
    yticker = meta["yahoo_ticker"]
    ym = yahoo_metrics_map.get(yticker, {})
    val = line["value_eur"]
    pru = line["pru_eur"]
    qty = line["quantity"]

    cost_basis = (qty * pru) if (qty > 0 and pru > 0) else val
    pnl_eur = val - cost_basis
    pnl_pct = ((val - cost_basis) / cost_basis * 100.0) if cost_basis > 0 else 0.0
    alpha_vs_cw8 = pnl_pct - cw8_24h_pct

    item_enriched = {
        **line,
        "bucket": bucket,
        "yahoo_ticker": yticker,
        "cost_basis_eur": round(cost_basis, 2),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pct": round(pnl_pct, 2),
        "alpha_vs_cw8": round(alpha_vs_cw8, 2),
        "change_24h_pct": ym.get("change_24h_pct", 0.0),
        "rsi_1h": ym.get("rsi_1h", 50.0),
        "rsi_4h": ym.get("rsi_4h", 50.0),
        "rsi_1d": ym.get("rsi_1d", 50.0),
        "dist_sma200_pct": ym.get("dist_sma200_pct", 0.0),
    }
    enriched_all_lines.append(item_enriched)

    if bucket == "LT_SALARIAL":
        val_lt_salarial += val
        dict_lt_salarial[line["name"]] = dict_lt_salarial.get(line["name"], 0.0) + val
    elif bucket == "MT_CORE_ETF":
        val_mt_core += val
        dict_mt_core[line["name"]] = dict_mt_core.get(line["name"], 0.0) + val
    else:
        val_ct_speculative += val
        dict_ct_speculative[line["name"]] = dict_ct_speculative.get(line["name"], 0.0) + val

brokerage_cash = cash_info.get("brokerage_cash", 656.14)
arbitrable_savings = cash_info.get("arbitrable_savings", 3421.10)
poudre_seche_bourse = brokerage_cash + arbitrable_savings

total_equity_portfolio = val_lt_salarial + val_mt_core + val_ct_speculative + poudre_seche_bourse

pct_lt = (val_lt_salarial / total_equity_portfolio * 100.0) if total_equity_portfolio > 0 else 0.0
pct_mt = (val_mt_core / total_equity_portfolio * 100.0) if total_equity_portfolio > 0 else 0.0
pct_ct = (val_ct_speculative / total_equity_portfolio * 100.0) if total_equity_portfolio > 0 else 0.0
pct_cash = (poudre_seche_bourse / total_equity_portfolio * 100.0) if total_equity_portfolio > 0 else 0.0

macro_compass = get_cached_macro_compass()

discrepancies = (
    audit_equity_database_vs_finary(finary_lines, yahoo_metrics_map)
    if RECON_AVAILABLE else []
)
alert_badge = f" ({len(discrepancies)})" if discrepancies else ""

# ==============================================================================
# ONGLETS DU DASHBOARD (6 ONGLETS STRUCTURÉS)
# ==============================================================================
tab_overview, tab_spec, tab_screener, tab_alerts, tab_ai, tab_admin = st.tabs([
    "📊 Vue Globale & 4 Poches",
    "🚀 Poche Spéculative ('Actions Chocs')",
    "🔥 Screener RSI & Découverte Marché",
    f"🔔 Centre d'Alertes & Réconciliation{alert_badge}",
    "🤖 Assistant IA Pro & Tickets",
    "🛠️ Administration, Mapping & SSOT",
])

# ==============================================================================
# ONGLET 1 : VUE GLOBALE & GOUVERNANCE DES 4 POCHES
# ==============================================================================
with tab_overview:
    with st.expander("📖 Guide de lecture — Vue Globale & Gouvernance des 4 Poches"):
        st.markdown("""
            Synthèse patrimoniale et allocation stratégique de vos actifs boursiers et salariaux :
            * **Boussole Macro Bourse :** Croisement en direct de l'indice de volatilité **VIX**, du **Taux 10 ans US (`^TNX`)** et de la tendance du **MSCI World (`CW8.PA`)** par rapport à sa moyenne mobile 200 jours (SMA 200).
            * **1. Socle Salarial & Retraite (`LT_SALARIAL`) :** PEG EDF, PERCO et PERO. Enveloppe sanctuarisée dédiée à la capture de l'abondement employeur et à l'horizon retraite.
            * **2. Cœur Indiciel & Fonds (`MT_CORE_ETF`) :** ETF mondiaux/régionaux (CTO BoursoBank & LCL), ETC Or Physique et fonds Mon Petit Placement. Soumis à une **Vigie de surconcentration à 35 % max** par ligne.
            * **3. Poche Spéculative 'Actions Chocs' (`CT_SPECULATIVE`) :** Titres vifs tactiques plafonnés à **6 % max** du portefeuille global, suivis avec PRU, TP1 et Stop-Loss.
            * **4. Poudre Sèche Bourse :** Espèces courtage (CTO LCL `0985W` + BoursoBank) + surplus du Livret A au-delà du matelas de sécurité sanctuarisé (**13 500 €**).
        """)

    regime = macro_compass["regime"]
    vix = macro_compass["vix"]
    tnx = macro_compass["us_10y_yield"]
    cw8_sma = macro_compass["cw8_dist_sma200_pct"]

    if regime == "RISK_OFF_VOLATILITY":
        st.markdown(
            f"""
            <div style="padding: 12px 18px; border-radius: 8px; border-left: 6px solid #ff4d4d; background-color: #2b1111; margin-bottom: 16px;">
                <div style="font-size: 15px; font-weight: bold; color: #ff6666;">
                    🚨 BOUSSOLE MACRO BOURSE : RÉGIME RISK-OFF / VOLATILITÉ — VIX : {vix:.1f} | MSCI World vs SMA200 : {cw8_sma:+.2f}%
                </div>
                <div style="font-size: 13px; color: #e0e0e0; margin-top: 4px;">
                    <b>Posture :</b> Gel des achats spéculatifs sur actions vives. Priorité à la préservation du cash et aux ordres limites bas sur ETF Monde.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif regime == "CAUTIOUS_CONSOLIDATION":
        st.markdown(
            f"""
            <div style="padding: 12px 18px; border-radius: 8px; border-left: 6px solid #ffa500; background-color: #2b220f; margin-bottom: 16px;">
                <div style="font-size: 15px; font-weight: bold; color: #ffa500;">
                    ⚠️ BOUSSOLE MACRO BOURSE : CONSOLIDATION / PRUDENCE — VIX : {vix:.1f} | Taux 10Y US : {tnx:.2f}%
                </div>
                <div style="font-size: 13px; color: #e0e0e0; margin-top: 4px;">
                    <b>Posture :</b> Sélectivité stricte sur les actions vives. Déploiement progressif de la Poudre Sèche sur replis RSI des ETF cœur.
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
                    🟢 BOUSSOLE MACRO BOURSE : TENDANCE PORTEUSE ({regime}) — VIX : {vix:.1f} | MSCI World vs SMA200 : {cw8_sma:+.2f}%
                </div>
                <div style="font-size: 13px; color: #e0e0e0; margin-top: 4px;">
                    <b>Posture :</b> Conditions nominales (4/5 ⭐). Maintien des tendances ETF, surveillance des prises de profits (TP1) sur les actions chocs.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("#### 🌐 Indicateurs Macro & Flux Patrimoniaux")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Indice VIX (Volatilité)", f"{vix:.2f}", delta="Calme < 20" if vix < 20 else "Tension > 20", delta_color="normal" if vix < 20 else "inverse")
    m2.metric("Taux 10 Ans US (^TNX)", f"{tnx:.2f} %", delta="Référence Taux", delta_color="off")
    m3.metric("MSCI World (CW8.PA)", f"{cw8_live_price:.2f} €", delta=f"{cw8_24h_pct:+.2f}% (24h) | {cw8_sma:+.1f}% vs SMA200")
    m4.metric("Cash-Flow Net du Mois", f"{sal_data['net_free_cash_flow_month']:+,.2f} €", delta="Salaire + Passif - Prêt RP")
    m5.metric("Taux d'Indépendance", f"{sal_data['independence_ratio']:.1f} %", delta=f"Rente : {div_metrics['monthly_passive_income']:,.0f} €/mois")

    st.markdown("")
    st.markdown("#### 💼 Ventilation des 4 Poches Actions & Liquidités Bourse")
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Total Actions & Cash Bourse", f"{total_equity_portfolio:,.2f} €", delta="Hors Crypto & Matelas 13.5k€")
    p2.metric("1. Socle Salarial (LT)", f"{val_lt_salarial:,.2f} €", delta=f"{pct_lt:.1f}% (PEE / PERCO / PERO)")
    p3.metric("2. Cœur Indiciel & Fonds (MT)", f"{val_mt_core:,.2f} €", delta=f"{pct_mt:.1f}% (ETF + MPP + Gold)")
    p4.metric("3. Spéculatif 'Chocs' (CT)", f"{val_ct_speculative:,.2f} €", delta=f"{pct_ct:.1f}% (Cap strict ≤ 6%)", delta_color="normal" if pct_ct <= 6.0 else "inverse")
    p5.metric("4. Poudre Sèche Bourse", f"{poudre_seche_bourse:,.2f} €", delta=f"{pct_cash:.1f}% (Courtage {brokerage_cash:.0f}€ + Surplus LA {arbitrable_savings:.0f}€)", delta_color="off")

    st.markdown("---")
    r1_c1, r1_c2 = st.columns([1, 1])
    with r1_c1:
        fig_global = px.pie(
            names=["1. Socle Salarial (LT)", "2. Cœur Indiciel & Fonds (MT)", "3. Spéculatif 'Chocs' (CT)", "4. Poudre Sèche Bourse"],
            values=[val_lt_salarial, val_mt_core, val_ct_speculative, poudre_seche_bourse],
            title="🌐 Répartition Stratégique du Portefeuille Actions",
            color_discrete_sequence=["#36a2eb", "#4bc0c0", "#ff6384", "#ffce56"],
            hole=0.4,
        )
        fig_global.update_traces(textposition="inside", textinfo="percent+label")
        fig_global.update_layout(margin=dict(t=35, b=10, l=10, r=10), showlegend=False, height=280)
        st.plotly_chart(fig_global, use_container_width=True)

    with r1_c2:
        st.markdown("##### 📌 Gouvernance & Équilibre Doctrinal")
        st.write(f"• **Socle Retraite & Salarial (LT) :** **{val_lt_salarial:,.2f} €** ({pct_lt:.1f}%) — *Intouchable, captation abondement EDF.*")
        st.write(f"• **Cœur Indiciel & Fonds (MT/LT) :** **{val_mt_core:,.2f} €** ({pct_mt:.1f}%) — *Moteur de capitalisation diversifié.*")
        st.write(f"• **Poche Spéculative 'Play' (CT) :** **{val_ct_speculative:,.2f} €** ({pct_ct:.1f}%) — *Enveloppe asymétrique sous TP/SL.*")
        st.write(f"• **Poudre Sèche Bourse :** **{poudre_seche_bourse:,.2f} €** ({pct_cash:.1f}%) — *Matelas Livret A de {buffer_livret:,.0f} € strictement isolé.*")
        st.info("💡 Les trois graphiques ci-dessous détaillent chaque poche et appliquent la **Vigie 35% Max** sur le Cœur Indiciel.")

    st.markdown("---")
    st.subheader("🔍 Zooms Détaillés par Poche Stratégique")
    z1, z2, z3 = st.columns(3)

    with z1:
        if dict_lt_salarial:
            df_lt = pd.DataFrame(list(dict_lt_salarial.items()), columns=["Fonds", "Valeur"])
            fig_lt = px.pie(df_lt, names="Fonds", values="Valeur", title="🏛️ 1. Socle Salarial & Retraite (LT)", hole=0.4)
            fig_lt.update_traces(textposition="inside", textinfo="percent+label")
            fig_lt.update_layout(margin=dict(t=35, b=10, l=10, r=10), showlegend=False, height=260)
            st.plotly_chart(fig_lt, use_container_width=True)

    with z2:
        if dict_mt_core and val_mt_core > 0:
            df_mt = pd.DataFrame(list(dict_mt_core.items()), columns=["Support", "Valeur"])
            fig_mt = px.pie(df_mt, names="Support", values="Valeur", title="💎 2. Cœur Indiciel & Fonds (MT)", hole=0.4)
            fig_mt.update_traces(textposition="inside", textinfo="percent+label")
            fig_mt.update_layout(margin=dict(t=35, b=10, l=10, r=10), showlegend=False, height=240)
            st.plotly_chart(fig_mt, use_container_width=True)

            plafond_35 = val_mt_core * 0.35
            core_meta_map = get_all_core_etf_metadata()
            st.markdown(f"##### ⚖️ Vigie 35% Max Intra-Cœur (Cap: {plafond_35:,.0f} €)")
            for asset_n, v_eur in sorted(dict_mt_core.items(), key=lambda x: x[1], reverse=True)[:5]:
                intra_p = (v_eur / val_mt_core * 100.0) if val_mt_core > 0 else 0.0
                color_b = "#ff4d4d" if intra_p >= 35.0 else ("#ffa500" if intra_p >= 30.0 else "#00ffcc")
                st.markdown(
                    f"<div style='font-size:12px; margin-top:4px;'><b>{asset_n[:28]}</b> : {v_eur:,.0f} € (<b>{intra_p:.1f}%</b>) <span style='color:{color_b};'>●</span></div>",
                    unsafe_allow_html=True,
                )
                st.progress(min(1.0, intra_p / 35.0))

            with st.expander("🏷️ Qualification & Thèses Cœur MT/LT"):
                sel_core = st.selectbox("Support à qualifier", options=sorted(list(dict_mt_core.keys())), key="sel_eq_core")
                c_meta = core_meta_map.get(sel_core, {"thesis_status": "ACTIVE", "notes": ""})
                st_opts = {"ACTIVE": "🟢 Thèse Intacte", "WATCHLIST_TRIM": "🟡 À écrémer", "EXIT_RECOMMENDED": "🔴 Sortie recommandée"}
                idx_s = list(st_opts.keys()).index(c_meta["thesis_status"]) if c_meta["thesis_status"] in st_opts else 0
                new_st = st.selectbox("Statut", options=list(st_opts.keys()), format_func=lambda x: st_opts[x], index=idx_s, key="eq_core_st")
                new_nt = st.text_input("Note stratégique", value=c_meta["notes"], key="eq_core_nt")
                if st.button("💾 Enregistrer Thèse", key="btn_save_eq_core"):
                    update_core_etf_metadata(sel_core, new_st, new_nt)
                    send_telegram_notify(f"🏷️ *THÈSE CŒUR MT/LT MISE À JOUR*\n• **{sel_core}** : `{new_st}`\n• Note : _{new_nt}_")
                    st.success(f"Thèse mise à jour pour {sel_core} !")
                    st.rerun()

    with z3:
        if dict_ct_speculative:
            df_ct = pd.DataFrame(list(dict_ct_speculative.items()), columns=["Action", "Valeur"])
            fig_ct = px.pie(df_ct, names="Action", values="Valeur", title="🚀 3. Spéculatif 'Actions Chocs' (CT)", hole=0.4)
            fig_ct.update_traces(textposition="inside", textinfo="percent+label")
            fig_ct.update_layout(margin=dict(t=35, b=10, l=10, r=10), showlegend=False, height=260)
            st.plotly_chart(fig_ct, use_container_width=True)

# ==============================================================================
# ONGLET 2 : ZOOM POCHE SPÉCULATIVE ('ACTIONS CHOCS / PLAY')
# ==============================================================================
with tab_spec:
    with st.expander("📖 Guide de lecture — Pilotage de la Poche Spéculative ('Actions Chocs')"):
        st.markdown("""
            Suivi opérationnel des actions vives logées sur votre **CTO LCL (`ORD DE SENANT`)** ou **CTO BoursoBank** :
            * **PRU & Cours Live :** Comparaison entre votre Prix de Revient Unitaire Finary et le cours temps réel Yahoo Finance.
            * **Alpha vs CW8 :** Mesure la surperformance ou sous-performance relative de chaque titre face à l'indice **MSCI World (`CW8.PA`)**.
            * **Mode `🤖 Auto IA` vs `👤 Manuel` :** Les positions en mode *Auto IA* ont leurs paliers TP1 et Stop-Loss recalibrés automatiquement par la sentinelle `equity_auto_tpsl.py` (mi-séance Paris 12h30 & ouverture US 18h00) avec **Verrou Ratchet** (le SL ne baisse jamais).
            * **Break-Even 1-Click :** Remonte instantanément le Stop-Loss au niveau du PRU pour sécuriser un *Free Ride* sans risque de perte en capital.
        """)

    st.subheader("🚀 Pilotage de la Poche Spéculative ('Actions Chocs / Play')")

    spec_db_rows = get_open_speculative_equities()
    finary_lookup = {(l["name"], l["account"]): l for l in finary_lines}

    enriched_spec = []
    tot_spec_invested = 0.0
    tot_spec_current = 0.0

    for row in spec_db_rows:
        t_id, sym, ytick, acc, isin, entry_p, qty, amt_eur, tp1, tp2, sl, step, auto_flag, cw8_entry, hwm, taken_at = row
        f_item = finary_lookup.get((sym, acc), {})
        ym = yahoo_metrics_map.get(ytick, {})

        live_p = ym.get("price", 0.0)
        if live_p > 0 and qty > 0:
            curr_val = qty * live_p
        elif f_item:
            curr_val = f_item.get("value_eur", amt_eur)
            live_p = (curr_val / qty) if qty > 0 else entry_p
        else:
            curr_val = amt_eur
            live_p = entry_p

        tot_spec_invested += amt_eur
        tot_spec_current += curr_val

        pnl_eur = curr_val - amt_eur
        pnl_pct = ((curr_val - amt_eur) / amt_eur * 100.0) if amt_eur > 0 else 0.0
        alpha_cw8 = pnl_pct - cw8_24h_pct
        marge_sl = ((live_p - sl) / live_p * 100.0) if (live_p > 0 and sl > 0) else 0.0

        if sl > 0 and live_p <= sl:
            statut = "🔴 SL TOUCHÉ"
        elif sl > 0 and live_p <= sl * 1.05:
            statut = "🛑 SL FRÔLÉ"
        elif tp1 > 0 and live_p >= tp1:
            statut = "🎯 TP1 ATTEINT"
        elif sl >= entry_p and entry_p > 0:
            statut = "🛡️ FREE RIDE"
        else:
            statut = "🟢 ACTIF"

        enriched_spec.append({
            "ID": t_id,
            "Mode": "🤖 Auto IA" if auto_flag else "👤 Manuel",
            "Action": sym,
            "Ticker": ytick,
            "Compte": acc,
            "PRU": entry_p,
            "Cours Live": live_p,
            "Investi (€)": amt_eur,
            "Valeur (€)": curr_val,
            "PnL (€)": pnl_eur,
            "PnL (%)": pnl_pct,
            "Alpha vs CW8": alpha_cw8,
            "RSI 1D": ym.get("rsi_1d", 50.0),
            "TP1": tp1,
            "Stop-Loss": sl,
            "Marge SL": marge_sl,
            "Statut": statut,
        })

    if enriched_spec:
        spec_pnl_eur = tot_spec_current - tot_spec_invested
        spec_pnl_pct = (spec_pnl_eur / tot_spec_invested * 100.0) if tot_spec_invested > 0 else 0.0

        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Capital Engagé (PRU)", f"{tot_spec_invested:,.2f} €")
        sc2.metric("Valorisation Live Poche Play", f"{tot_spec_current:,.2f} €", delta=f"{spec_pnl_eur:+,.2f} € ({spec_pnl_pct:+.2f}%)")
        sc3.metric("Poids dans Portefeuille Actions", f"{pct_ct:.2f} %", delta="Conforme (≤ 6%)" if pct_ct <= 6.0 else "Dépassement Cap 6%", delta_color="normal" if pct_ct <= 6.0 else "inverse")
        sc4.metric("Lignes Actives Play", f"{len(enriched_spec)} actions")

        df_spec = pd.DataFrame(enriched_spec)

        def style_spec_pnl(val):
            if pd.isna(val) or not isinstance(val, (int, float)):
                return ""
            if val > 0:
                return "color: #00ff66; font-weight: bold; background-color: rgba(0, 255, 102, 0.10);"
            elif val < 0:
                return "color: #ff4d4d; font-weight: bold; background-color: rgba(255, 77, 77, 0.10);"
            return ""

        styler_spec = df_spec.style.map(style_spec_pnl, subset=["PnL (€)", "PnL (%)", "Alpha vs CW8"]).map(color_rsi, subset=["RSI 1D"]).format({
            "PRU": "{:.2f} €",
            "Cours Live": "{:.2f} €",
            "Investi (€)": "{:,.2f} €",
            "Valeur (€)": "{:,.2f} €",
            "PnL (€)": "{:+,.2f} €",
            "PnL (%)": "{:+.2f}%",
            "Alpha vs CW8": "{:+.2f}%",
            "RSI 1D": "{:.1f}",
            "TP1": "{:.2f} €",
            "Stop-Loss": "{:.2f} €",
            "Marge SL": "{:+.1f}%",
        })
        st.dataframe(styler_spec, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("⚡ Actions & Gestion Dynamique des Lignes Spéculatives")
        sp_t1, sp_t2, sp_t3, sp_t4 = st.tabs([
            "🛡️ Passage en Break-Even (1-Click)",
            "✏️ Édition & Raccourcis SL/TP",
            "🤖 Mode Auto TP/SL (IA)",
            "🔒 Clôture & Archivage FIFO",
        ])

        with sp_t1:
            be_id = st.selectbox(
                "Sélectionner une action pour caler le SL sur le PRU",
                options=df_spec["ID"].tolist(),
                format_func=lambda x: f"ID {x} — {df_spec[df_spec['ID']==x]['Action'].values[0]} (PnL: {df_spec[df_spec['ID']==x]['PnL (%)'].values[0]:+.2f}%)",
                key="eq_be_sel",
            )
            if st.button("🛡️ Passer en Break-Even (SL = PRU)", type="primary", key="btn_eq_be"):
                be_row = df_spec[df_spec["ID"] == be_id].iloc[0]
                set_equity_break_even(be_id)
                send_telegram_notify(
                    f"🛡️ *ACTION SÉCURISÉE (BREAK-EVEN)*\n• **{be_row['Action']}** (`{be_row['Ticker']}`)\n• Nouveau Stop-Loss calé sur PRU : **{be_row['PRU']:.2f} €**"
                )
                st.success(f"✅ Stop-Loss aligné sur le PRU pour {be_row['Action']} !")
                st.rerun()

        with sp_t2:
            ed_id = st.selectbox(
                "Position à ajuster",
                options=df_spec["ID"].tolist(),
                format_func=lambda x: f"ID {x} — {df_spec[df_spec['ID']==x]['Action'].values[0]}",
                key="eq_ed_sel",
            )
            r_ed = df_spec[df_spec["ID"] == ed_id].iloc[0]
            base_pru = float(r_ed["PRU"])
            sk = f"eq_sl_preset_{ed_id}"
            if sk not in st.session_state:
                st.session_state[sk] = float(r_ed["Stop-Loss"])

            rc1, rc2, rc3, rc4 = st.columns(4)
            if rc1.button("-8% (Serré)", key="eq_sl_8"):
                st.session_state[sk] = round(base_pru * 0.92, 4)
                st.rerun()
            if rc2.button("-12% (Tactique)", key="eq_sl_12"):
                st.session_state[sk] = round(base_pru * 0.88, 4)
                st.rerun()
            if rc3.button("-15% (Standard)", key="eq_sl_15"):
                st.session_state[sk] = round(base_pru * 0.85, 4)
                st.rerun()
            if rc4.button("-20% (Large)", key="eq_sl_20"):
                st.session_state[sk] = round(base_pru * 0.80, 4)
                st.rerun()

            ec1, ec2, ec3 = st.columns(3)
            n_tp1 = ec1.number_input("Nouveau TP1 (€)", value=float(r_ed["TP1"]), format="%.2f", key=f"in_eq_tp1_{ed_id}")
            n_tp2 = ec2.number_input("Nouveau TP2 (€)", value=round(base_pru * 1.80, 2), format="%.2f", key=f"in_eq_tp2_{ed_id}")
            n_sl = ec3.number_input("Nouveau Stop-Loss (€)", value=st.session_state[sk], format="%.2f", key=f"in_eq_sl_{ed_id}")
            if st.button("💾 Enregistrer Paliers", key="btn_save_eq_lvls"):
                update_equity_levels(ed_id, n_tp1, n_tp2, n_sl)
                if sk in st.session_state:
                    del st.session_state[sk]
                send_telegram_notify(
                    f"✏️ *PALIERS ACTIONS MIS À JOUR*\n• **{r_ed['Action']}** (`{r_ed['Ticker']}`)\n• TP1 : **{n_tp1:.2f} €** | Stop-Loss : **{n_sl:.2f} €**"
                )
                st.success("✅ Paliers mis à jour en base !")
                st.rerun()

        with sp_t3:
            au_id = st.selectbox(
                "Position à basculer",
                options=df_spec["ID"].tolist(),
                format_func=lambda x: f"ID {x} — {df_spec[df_spec['ID']==x]['Action'].values[0]} ({df_spec[df_spec['ID']==x]['Mode'].values[0]})",
                key="eq_au_sel",
            )
            au_row = df_spec[df_spec["ID"] == au_id].iloc[0]
            ac1, ac2 = st.columns(2)
            if ac1.button("🤖 Activer Auto IA", type="primary", key="btn_eq_auto_on"):
                toggle_equity_auto_tpsl(au_id, True)
                send_telegram_notify(f"🤖 *MODE AUTO TP/SL ACTIVÉ (ACTIONS)*\n• **{au_row['Action']}** est désormais piloté par l'IA.")
                st.rerun()
            if ac2.button("👤 Passer en Manuel", key="btn_eq_auto_off"):
                toggle_equity_auto_tpsl(au_id, False)
                send_telegram_notify(f"👤 *MODE AUTO TP/SL DÉSACTIVÉ (ACTIONS)*\n• **{au_row['Action']}** repasse en gestion manuelle.")
                st.rerun()

        with sp_t4:
            cl_id = st.selectbox(
                "Position vendue à archiver (FIFO)",
                options=df_spec["ID"].tolist(),
                format_func=lambda x: f"ID {x} — {df_spec[df_spec['ID']==x]['Action'].values[0]}",
                key="eq_cl_sel",
            )
            r_cl = df_spec[df_spec["ID"] == cl_id].iloc[0]
            ex_p = st.number_input("Prix de vente exécuté (€)", value=float(r_cl["Cours Live"]), format="%.2f", key="in_eq_exit")
            if st.button("🔒 Archiver la clôture", key="btn_eq_close"):
                close_equity_trade(cl_id, exit_price=ex_p, reason="MANUAL_CLOSE")
                send_telegram_notify(
                    f"🔒 *POSITION ACTION CLÔTURÉE (FIFO)*\n• **{r_cl['Action']}** (`{r_cl['Compte']}`)\n• Prix sortie : **{ex_p:.2f} €** (PRU : {r_cl['PRU']:.2f} €)"
                )
                st.success("✅ Opération archivée dans le journal FIFO !")
                st.rerun()
    else:
        st.info("Aucune ligne spéculative active en BDD.")

    with st.expander("📋 Journal FIFO des Opérations Clôturées", expanded=False):
        hist_rows = get_closed_equity_history()
        if hist_rows:
            df_h = pd.DataFrame(hist_rows, columns=["ID", "Action", "Compte", "PRU (€)", "Sortie (€)", "Investi (€)", "PnL (€)", "PnL (%)", "Ouverture", "Clôture", "Raison"])
            st.dataframe(df_h, use_container_width=True, hide_index=True)
        else:
            st.info("Aucune opération clôturée enregistrée.")

# ==============================================================================
# ONGLET 3 : SCREENER RSI MULTI-TF & DÉCOUVERTE DYNAMIQUE DU MARCHÉ
# ==============================================================================
with tab_screener:
    with st.expander("📖 Guide de lecture — Screener RSI & Découverte Dynamique du Marché"):
        st.markdown("""
            Outil de détection des points d'entrée techniques et d'exploration d'opportunités hors portefeuille :
            * **Heatmap RSI Multi-Timeframe :** Calcule le RSI (1h, 4h, 1D) et l'écart à la SMA 200 sur l'ensemble de vos ETF et actions vives. Un **RSI 1D ≤ 30** (vert vif) signale une zone de survente propice au déploiement progressif de votre Poudre Sèche.
            * **Découverte Dynamique Séparée :** Scanne un vivier liquide Euronext Paris & US éligible à vos CTO (en filtrant automatiquement vos titres déjà détenus) et sépare :
              1. **Opportunités Spéculatives ('Actions Chocs') :** Rebonds techniques RSI ou momentum supérieur au MSCI World.
              2. **Opportunités CORE / MT-LT (ETF & Blue Chips) :** Points d'accumulation DCA sur replis de marché.
        """)

    st.subheader("🔥 Heatmap RSI Multi-Timeframe (Portefeuille Actuel : ETF & Actions Vives)")

    screener_rows = []
    for item in enriched_all_lines:
        if item["yahoo_ticker"]:
            screener_rows.append({
                "Poche": "🚀 Spéculatif" if item["bucket"] == "CT_SPECULATIVE" else "💎 Cœur MT/LT",
                "Titre": item["name"],
                "Ticker": item["yahoo_ticker"],
                "Compte": item["account"],
                "Valeur (€)": item["value_eur"],
                "PnL Latent (%)": item["pnl_pct"],
                "Var 24h (%)": item["change_24h_pct"],
                "RSI 1h": item["rsi_1h"],
                "RSI 4h": item["rsi_4h"],
                "RSI 1D": item["rsi_1d"],
                "Dist SMA200": item["dist_sma200_pct"],
            })

    if screener_rows:
        df_scr = pd.DataFrame(screener_rows).sort_values(by="RSI 1D", ascending=True)
        styler_scr = df_scr.style.map(color_rsi, subset=["RSI 1h", "RSI 4h", "RSI 1D"]).format({
            "Valeur (€)": "{:,.2f} €",
            "PnL Latent (%)": "{:+.2f}%",
            "Var 24h (%)": "{:+.2f}%",
            "RSI 1h": "{:.1f}",
            "RSI 4h": "{:.1f}",
            "RSI 1D": "{:.1f}",
            "Dist SMA200": "{:+.1f}%",
        })
        st.dataframe(styler_scr, use_container_width=True, hide_index=True)

        oversold = df_scr[df_scr["RSI 1D"] <= 35]["Titre"].tolist()
        if oversold:
            st.success(f"💡 **Zone d'accumulation technique en portefeuille (RSI 1D ≤ 35) :** {', '.join(oversold)}")

    st.markdown("---")
    st.subheader("🔭 Découverte Dynamique du Marché (Hors Portefeuille — Spéculatif vs CORE)")

    if DISCOVERY_AVAILABLE:
        d_c1, d_c2 = st.columns([1, 4])
        with d_c1:
            force_disc = st.button("🔄 Forcer Re-scan Marché", type="primary", key="btn_eq_force_disc")
        with d_c2:
            st.caption("Scan multi-threadé Euronext & US filtrant automatiquement les titres déjà détenus en portefeuille (Cache TTL 15 min).")

        disc_data = get_dynamic_equity_candidates(top_spec=8, top_core=8, force_refresh=force_disc)
        col_d_spec, col_d_core = st.columns(2)

        with col_d_spec:
            st.markdown("##### 🚀 Vivier Spéculatif ('Actions Chocs / Play')")
            if disc_data.get("speculative"):
                df_d_spec = pd.DataFrame(disc_data["speculative"])[
                    ["ticker", "name", "price", "alpha_vs_cw8", "rsi_1d", "opportunity_type"]
                ]
                df_d_spec.columns = ["Ticker", "Valeur", "Cours", "Alpha CW8", "RSI 1D", "Signal"]
                st.dataframe(
                    df_d_spec.style.map(color_rsi, subset=["RSI 1D"]).format({
                        "Cours": "{:.2f}",
                        "Alpha CW8": "{:+.2f}%",
                        "RSI 1D": "{:.1f}",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

        with col_d_core:
            st.markdown("##### 💎 Vivier CORE / MT-LT (ETF & Blue Chips)")
            if disc_data.get("core"):
                df_d_core = pd.DataFrame(disc_data["core"])[
                    ["ticker", "name", "price", "dist_sma200_pct", "rsi_1d", "opportunity_type"]
                ]
                df_d_core.columns = ["Ticker", "Support", "Cours", "vs SMA200", "RSI 1D", "Signal"]
                st.dataframe(
                    df_d_core.style.map(color_rsi, subset=["RSI 1D"]).format({
                        "Cours": "{:.2f}",
                        "vs SMA200": "{:+.1f}%",
                        "RSI 1D": "{:.1f}",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
    else:
        st.info("Module `equity_discovery_engine.py` non disponible.")

# ==============================================================================
# ONGLET 4 : CENTRE D'ALERTES, RÉCONCILIATION & ZOMBIES CTO
# ==============================================================================
with tab_alerts:
    with st.expander("📖 Guide de lecture — Centre d'Alertes, Réconciliation & Fiscalité CTO"):
        st.markdown("""
            Vigie automatisée de cohérence comptable et de gestion des risques Bourse :
            * **Détection Auto des Nouveaux Achats Finary :** Dès qu'un nouvel ordre d'achat exécuté sur votre CTO LCL ou BoursoBank remonte via Finary, le système le détecte ici et vous permet de l'**intégrer en 1 clic** dans votre cockpit Play (avec PRU exact et alerte Telegram).
            * **Synchronisation des Volumes & Lignes Fantômes :** Détecte les renforts partiels ($\ge 15\ \%$) et les positions vendues à archiver dans le journal FIFO.
            * **Sentinelle Zombies & Tax-Loss Harvesting (PFU 30 %) :** Identifie les actions vives sur CTO accusant une moins-value latente sévère (`≤ -15 %`) et chiffre l'économie d'impôt réalisable en fin d'année fiscale.
        """)

    st.subheader("🕵️ Rapprochement Finary vs Base de Données (`equity_wealth.db`)")

    if discrepancies:
        st.warning(f"🔔 **{len(discrepancies)} événement(s) ou écart(s) détecté(s) entre Finary et votre cockpit :**")
        for idx_d, disc in enumerate(discrepancies):
            d_type = disc["type"]
            d_sym = disc["symbol"]
            d_acc = disc["account"]
            d_id = disc.get("id")

            if d_type == "NEW_SPECULATIVE_DETECTED":
                border_c, bg_c, badge = "#00ffcc", "#152a26", "💡 NOUVEL ACHAT SPÉCULATIF DÉTECTÉ"
            elif d_type == "GHOST_POSITION":
                border_c, bg_c, badge = "#ff4d4d", "#2a1515", "🔴 POSITION VENDUE / FANTÔME"
            else:
                border_c, bg_c, badge = "#ffa500", "#2a2215", "🟡 ÉCART DE QUANTITÉ (RENFORT / ALLÈGEMENT)"

            st.markdown(
                f"""
                <div style="padding: 12px 16px; border-radius: 8px; border: 1px solid {border_c}; margin-bottom: 10px; background-color: {bg_c};">
                    <h4 style="margin: 0; color: {border_c};">{badge} — {d_sym} ({d_acc})</h4>
                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #eee;">{disc['desc']}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if d_type == "NEW_SPECULATIVE_DETECTED":
                c_b1, c_b2 = st.columns(2)
                with c_b1:
                    if st.button(
                        f"🚀 Ajouter au Cockpit Play ({d_sym} — {disc['current_val']:,.2f} €)",
                        type="primary",
                        key=f"onboard_spec_{idx_d}_{d_sym}",
                    ):
                        add_speculative_equity(
                            symbol=d_sym,
                            account=d_acc,
                            price_entry=disc["price_entry"],
                            quantity=disc["quantity"],
                            amount_eur=disc["amount_eur"],
                            yahoo_ticker=disc.get("yahoo_ticker", ""),
                            isin=disc.get("isin", ""),
                            cw8_price_entry=cw8_live_price,
                        )
                        send_telegram_notify(
                            f"🚀 *NOUVELLE ACTION INTÉGRÉE AU COCKPIT PLAY*\n• **{d_sym}** sur _{d_acc}_\n• Montant : **{disc['amount_eur']:,.2f} €** (PRU : {disc['price_entry']:.2f} €)\n• Suivi TP/SL et sentinelle activés."
                        )
                        st.cache_data.clear()
                        st.success(f"✅ {d_sym} ajouté à votre Poche Spéculative !")
                        st.rerun()
                with c_b2:
                    if st.button(
                        f"💎 Reclasser en Cœur MT/LT ({d_sym})",
                        key=f"reclass_core_{idx_d}_{d_sym}",
                    ):
                        ak_reclass = f"{disc['isin']}_{d_acc}" if disc.get("isin") else f"{d_sym}_{d_acc}"
                        upsert_asset_classification(
                            asset_key=ak_reclass,
                            name=d_sym,
                            account=d_acc,
                            isin=disc.get("isin", ""),
                            yahoo_ticker=disc.get("yahoo_ticker", ""),
                            bucket="MT_CORE_ETF",
                        )
                        send_telegram_notify(f"💎 *ACTION RECLASSÉE EN CŒUR MT/LT*\n• **{d_sym}** transféré hors poche spéculative.")
                        st.cache_data.clear()
                        st.success(f"✅ {d_sym} reclassé en Cœur MT/LT !")
                        st.rerun()

            elif d_type == "VOLUME_MISMATCH":
                if st.button(
                    f"🔄 Synchroniser quantité & PRU ID {d_id} ({d_sym} -> {disc['finary_qty']:.2f} titres)",
                    key=f"sync_vol_eq_{d_id}",
                ):
                    conn = get_connection()
                    c = conn.cursor()
                    c.execute(
                        "UPDATE speculative_equities SET quantity = ?, price_entry = ?, amount_eur = ? WHERE id = ?",
                        (disc["adjusted_qty"], disc["adjusted_pru"], disc["adjusted_amount_eur"], d_id),
                    )
                    conn.commit()
                    conn.close()
                    send_telegram_notify(
                        f"🔄 *VOLUME ACTION RESYNCHRONISÉ*\n• **{d_sym}** : {disc['finary_qty']:.2f} titres | PRU : {disc['adjusted_pru']:.2f} €"
                    )
                    st.cache_data.clear()
                    st.success(f"✅ Ligne {d_sym} resynchronisée avec Finary !")
                    st.rerun()

            elif d_type == "GHOST_POSITION":
                if st.button(
                    f"🔒 Archiver la vente en FIFO ID {d_id} ({d_sym})",
                    key=f"close_ghost_eq_{d_id}",
                ):
                    close_equity_trade(d_id, exit_price=disc["exit_price"], reason="FINARY_SOLD_SYNC")
                    send_telegram_notify(f"🧹 *VENTE SYNCHRONISÉE & ARCHIVÉE (FIFO)*\n• **{d_sym}** clôturé en base de données.")
                    st.cache_data.clear()
                    st.success(f"✅ Position {d_sym} archivée dans le journal FIFO !")
                    st.rerun()
    else:
        st.success("✅ Parfaite synchronisation : toutes vos lignes Finary sont alignées avec la base de données.")

    st.markdown("---")
    st.subheader("🧟 Sentinelle des Actifs Zombies & Tax-Loss Harvesting (CTO)")

    zombies = [
        item for item in enriched_all_lines
        if item["bucket"] == "CT_SPECULATIVE" and item["pnl_pct"] <= -15.0 and item["value_eur"] >= 50.0
    ]

    if zombies:
        for z in sorted(zombies, key=lambda x: x["pnl_eur"]):
            tax_saving_pfu = abs(z["pnl_eur"]) * 0.30
            veto_rsi = z["rsi_1d"] <= 30.0
            veto_msg = " | ⏸️ **VETO SURVENTE (RSI 1D ≤ 30 : attendre rebond technique)**" if veto_rsi else ""
            st.markdown(
                f"""
                <div style="padding: 12px 16px; border-radius: 8px; border: 1px solid #ff4d4d; margin-bottom: 10px; background-color: #2a1515;">
                    <h4 style="margin: 0; color: #ff6666;">🧟 ZOMBIE CTO — {z['name']} ({z['account']}){veto_msg}</h4>
                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #eee;">
                        <b>Valeur actuelle :</b> {z['value_eur']:,.2f} € | <b>Moins-value latente :</b> <span style="color:#ff6666; font-weight:bold;">{z['pnl_eur']:+,.2f} € ({z['pnl_pct']:+.2f}%)</span> | <b>RSI 1D :</b> {z['rsi_1d']:.1f}<br>
                        <b>Économie d'impôt PFU (30%) récupérable :</b> <span style="color:#00ffcc; font-weight:bold;">+{tax_saving_pfu:,.2f} €</span> sur vos plus-values CTO de l'année.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.success("🟢 Aucune action zombie en attrition critique détectée.")

# ==============================================================================
# ONGLET 5 : ASSISTANT IA PRO & TICKETS D'ORDRES BOURSE (SSOT)
# ==============================================================================
with tab_ai:
    with st.expander("📖 Guide de lecture — Assistant IA Stratège Bourse & Tickets d'Ordres"):
        st.markdown("""
            Votre comité d'investissement IA piloté par vos règles patrimoniales SSOT :
            * **Recalibrage Auto TP/SL (IA) :** Exécute à la demande le moteur `equity_auto_tpsl.py` pour remonter les Stop-Loss (Verrou Ratchet) et sécuriser les lignes en gain ≥ +25 %.
            * **Audits Spécialisés en 1 Clic :**
              1. *Audit Poche Spéculative :* Identifie les prises de profit sur vos actions chocs et les opportunités d'achat du vivier dynamique.
              2. *Audit Protection & Tax-Loss CTO :* Vérifie vos Stop-Loss et propose des arbitrages fiscaux (purge de moins-values latentes CTO).
              3. *Arbitrages Macro MT/LT :* Oriente votre flux d'épargne mensuel (salaire + 13e mois + loyer LMNP) vers vos ETF cœur de portefeuille, avec **application 1-Click des thèses en BDD**.
        """)

    st.subheader("🤖 Stratège Actions & Patrimoine IA (Prompts SSOT & Tickets Bourse)")

    if AUTO_TPSL_AVAILABLE:
        st.markdown("##### ⚡ Gestion Automatique des Paliers TP/SL Actions (IA)")
        col_a1, col_a2 = st.columns([2, 3])
        with col_a1:
            if st.button("🤖 Lancer le Recalibrage Auto TP/SL (IA)", type="primary", use_container_width=True):
                with st.spinner("Analyse des cours live, RSI et recalibrage Ratchet..."):
                    res_tpsl = run_equity_auto_tpsl_cycle(manual_trigger=True)
                    for lg in res_tpsl["logs"]:
                        st.write(lg)
                    if res_tpsl["updated_count"] > 0:
                        send_telegram_notify(
                            f"🤖 *RECALIBRAGE AUTO TP/SL ACTIONS (IA)*\n• {res_tpsl['updated_count']} ligne(s) ajustée(s) via le dashboard."
                        )
                        st.cache_data.clear()
        with col_a2:
            st.caption("💡 S'exécute automatiquement via Cron à 12h30 et 18h00 (Lun–Ven), ou à la demande ci-contre.")
        st.markdown("---")

    ai_c1, ai_c2, ai_c3 = st.columns(3)
    preset_prompt = None
    preset_context = None

    spec_summary_json = json.dumps(
        [
            {
                "Action": x["name"],
                "Ticker": x["yahoo_ticker"],
                "Compte": x["account"],
                "ISIN": x["isin"],
                "PRU": x["pru_eur"],
                "Valeur_EUR": x["value_eur"],
                "PnL_Pct": x["pnl_pct"],
                "RSI_1D": x["rsi_1d"],
            }
            for x in enriched_all_lines if x["bucket"] == "CT_SPECULATIVE"
        ],
        indent=2,
        ensure_ascii=False,
    )

    core_summary_json = json.dumps(
        [
            {
                "Support": x["name"],
                "Ticker": x["yahoo_ticker"],
                "Compte": x["account"],
                "ISIN": x["isin"],
                "Valeur_EUR": x["value_eur"],
                "PnL_Pct": x["pnl_pct"],
                "RSI_1D": x["rsi_1d"],
            }
            for x in enriched_all_lines if x["bucket"] == "MT_CORE_ETF"
        ],
        indent=2,
        ensure_ascii=False,
    )

    dyn_discovery_block = ""
    if DISCOVERY_AVAILABLE:
        try:
            d_ai = get_dynamic_equity_candidates(top_spec=6, top_core=6)
            dyn_discovery_block = f"\nVIVIER DYNAMIQUE DU MARCHÉ (HORS PORTEFEUILLE) :\n{json.dumps(d_ai, indent=2, ensure_ascii=False)}"
        except Exception:
            pass

    common_wealth_header = f"""
DONNÉES PATRIMONIALES & MACRO EN TEMPS RÉEL :
- Boussole Macro Bourse : Régime {macro_compass['regime']} ({macro_compass['macro_stars']}/5 ⭐) | VIX : {macro_compass['vix']} | Taux 10Y US : {macro_compass['us_10y_yield']}% | MSCI World (CW8.PA) : {cw8_live_price} € ({cw8_24h_pct:+.2f}% 24h, {cw8_sma:+.1f}% vs SMA200)
- Ventilation des 4 Poches Actions & Cash Bourse ({total_equity_portfolio:,.2f} €) :
  1. Socle Salarial & Retraite (LT_SALARIAL) : {val_lt_salarial:,.2f} € ({pct_lt:.1f}%)
  2. Cœur Indiciel & Fonds (MT_CORE_ETF) : {val_mt_core:,.2f} € ({pct_mt:.1f}%)
  3. Poche Spéculative 'Actions Chocs' (CT_SPECULATIVE) : {val_ct_speculative:,.2f} € ({pct_ct:.1f}%) [Cap Strict ≤ 6%]
  4. Poudre Sèche Bourse : {poudre_seche_bourse:,.2f} € (Espèces Courtage : {brokerage_cash:,.2f} € | Surplus Livret A > {buffer_livret:,.0f} € : {arbitrable_savings:,.2f} €)
- Matelas de Sécurité Sanctuarisé (Livret A LCL) : {buffer_livret:,.2f} € (Intouchable)
- Capacité d'Épargne & Flux Mensuel :
  • Salaire Net du Mois : {sal_data['current_month_salary']:,.2f} € {sal_data['detail_label']}
  • Revenus Passifs (LMNP 791 € + Dividendes + Intérêts) : {div_metrics['monthly_passive_income']:,.2f} €/mois (Taux Indépendance : {sal_data['independence_ratio']:.1f}%)
  • Solde Net Après Mensualité Crédit RP (-{re_data['monthly_loan_payment']:,.2f} €) : {sal_data['net_free_cash_flow_month']:+,.2f} €/mois
{dyn_discovery_block}
"""

    with ai_c1:
        if st.button("🚀 Audit Poche Spéculative & Opportunités", use_container_width=True):
            sys_p = get_system_prompt("equity_advisor_speculative") if PROMPT_LOADER_AVAILABLE else ""
            preset_prompt = "Audite ma Poche Spéculative ('Actions Chocs'), identifie les lignes à écrémer ou renforcer sur repli RSI et génère les tickets d'ordres Bourse correspondants."
            preset_context = f"{sys_p}\n\n{common_wealth_header}\n\nDÉTAIL POCHE SPÉCULATIVE :\n{spec_summary_json}"

    with ai_c2:
        if st.button("🛡️ Audit Protection SL & Tax-Loss CTO", use_container_width=True):
            sys_p = get_system_prompt("equity_risk_manager") if PROMPT_LOADER_AVAILABLE else ""
            preset_prompt = "Exécute un audit de protection Stop-Loss sur mes actions vives et identifie les opportunités de Tax-Loss Harvesting avant fin d'année."
            preset_context = f"{sys_p}\n\n{common_wealth_header}\n\nDÉTAIL POCHE SPÉCULATIVE :\n{spec_summary_json}"

    with ai_c3:
        if st.button("⚖️ Arbitrages Macro MT/LT & Fléchage Flux", use_container_width=True):
            sys_p = get_system_prompt("equity_macro_arbitrage") if PROMPT_LOADER_AVAILABLE else ""
            preset_prompt = "Audite ma Poche Cœur MT/LT (ETF, Mon Petit Placement, Or), évalue l'allocation globale et propose le fléchage optimal de mon excédent mensuel."
            preset_context = f"{sys_p}\n\n{common_wealth_header}\n\nDÉTAIL POCHE CŒUR MT/LT :\n{core_summary_json}"

    st.markdown("---")
    if "eq_messages" not in st.session_state:
        st.session_state.eq_messages = [{
            "role": "assistant",
            "content": "Bonjour ! Je suis ton stratège patrimonial & actions. Sélectionne une analyse rapide ci-dessus ou pose-moi ta question sur tes CTO, ETF ou ton épargne salariale.",
        }]

    for idx_m, msg in enumerate(st.session_state.eq_messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                props = extract_equity_proposals_from_text(msg["content"])
                if props and isinstance(props, dict):
                    st.markdown("---")
                    st.markdown("##### ✨ Qualifications de Thèses suggérées par l'IA :")
                    p_cols = st.columns(min(4, len(props)))
                    for i_p, (p_name, p_d) in enumerate(props.items()):
                        c_p = p_cols[i_p % len(p_cols)]
                        st_v = p_d.get("status", "ACTIVE")
                        nt_v = p_d.get("notes", "")
                        b_ico = "🟢" if st_v == "ACTIVE" else ("🟡" if st_v == "WATCHLIST_TRIM" else "🔴")
                        c_p.caption(f"{b_ico} **{p_name}** : `{st_v}`\n*{nt_v}*")
                    if st.button("✨ Appliquer ces qualifications en BDD (1-Click)", type="primary", key=f"apply_eq_prop_h_{idx_m}"):
                        for p_name, p_d in props.items():
                            update_core_etf_metadata(p_name, p_d.get("status", "ACTIVE"), p_d.get("notes", ""))
                        send_telegram_notify(f"✨ *QUALIFICATIONS THÈSES BOURSE APPLIQUÉES (1-CLICK IA)*\n• {len(props)} support(s) mis à jour en BDD.")
                        st.success("✅ Qualifications enregistrées en base de données !")
                        st.rerun()

    u_in = st.chat_input("Ex: Quelle part de ma poudre sèche déployer sur l'ETF S&P 500 ou NVIDIA cette semaine ?")
    to_run = u_in or preset_prompt

    if to_run:
        st.session_state.eq_messages.append({"role": "user", "content": to_run})
        with st.chat_message("user"):
            st.markdown(to_run)

        if not preset_context:
            sys_default = get_system_prompt("equity_macro_arbitrage") if PROMPT_LOADER_AVAILABLE else ""
            preset_context = f"{sys_default}\n\n{common_wealth_header}\n\nPOCHE SPÉCULATIVE :\n{spec_summary_json}\n\nPOCHE CŒUR MT/LT :\n{core_summary_json}"

        with st.chat_message("assistant"):
            with st.spinner("Analyse des marchés et génération des recommandations..."):
                try:
                    ans, used_m = call_gemini_with_fallback(
                        prompt_text=to_run,
                        system_instruction=preset_context,
                    )
                    st.markdown(ans)
                    st.caption(f"🤖 *Analyse générée via `{used_m}`*")
                    st.session_state.eq_messages.append({"role": "assistant", "content": ans})
                    st.rerun()
                except Exception as e:
                    st.error(f"⚠️ Erreur Gemini : {e}")

# ==============================================================================
# ONGLET 6 : ADMINISTRATION, MAPPING DES POCHES & SSOT CONTROL PANEL
# ==============================================================================
with tab_admin:
    with st.expander("📖 Guide de lecture — Administration & Gouvernance SSOT"):
        st.markdown("""
            Panneau de contrôle centralisé de votre infrastructure Bourse & Patrimoine :
            * **Référentiel des 66 Lignes Finary :** Reclassez à tout moment un titre entre **`LT_SALARIAL`**, **`MT_CORE_ETF`** et **`CT_SPECULATIVE`**, ou ajustez son ticker Yahoo Finance (`.PA`, `.DE`, US).
            * **Paramètres Financiers (`.env`) :** Pilotez votre matelas Livret A sanctuarisé (**13 500 €**), votre salaire net socle et votre prime annuelle partagés avec le digest matinal.
            * **Configuration Gemini & Prompts SSOT (`.md`) :** Éditez vos consignes d'investissement avec sauvegarde `.bak` automatique et notification Telegram.
        """)

    st.subheader("🗂️ Référentiel & Classification des Lignes Finary (Mapping SSOT)")
    df_class = pd.DataFrame(classifications)
    if not df_class.empty:
        edited_df = st.data_editor(
            df_class[["asset_key", "account", "name", "isin", "yahoo_ticker", "bucket"]],
            column_config={
                "asset_key": st.column_config.TextColumn("Clé SSOT", disabled=True),
                "account": st.column_config.TextColumn("Compte Finary", disabled=True),
                "name": st.column_config.TextColumn("Libellé Titre", disabled=True),
                "isin": st.column_config.TextColumn("Code ISIN"),
                "yahoo_ticker": st.column_config.TextColumn("Ticker Yahoo (.PA / US)"),
                "bucket": st.column_config.SelectboxColumn(
                    "Poche Stratégique",
                    options=["LT_SALARIAL", "MT_CORE_ETF", "CT_SPECULATIVE"],
                    required=True,
                ),
            },
            hide_index=True,
            use_container_width=True,
            key="editor_equity_mapping",
        )

        if st.button("💾 Sauvegarder le Mapping des Poches & Tickers", type="primary"):
            for _, r in edited_df.iterrows():
                upsert_asset_classification(
                    asset_key=r["asset_key"],
                    name=r["name"],
                    account=r["account"],
                    isin=r["isin"],
                    yahoo_ticker=r["yahoo_ticker"],
                    bucket=r["bucket"],
                )
            st.cache_data.clear()
            send_telegram_notify("🗂️ *MAPPING DES POCHES BOURSE MIS À JOUR VIA WEB*")
            st.success("✅ Classification et tickers Yahoo mis à jour en base de données !")
            st.rerun()

    st.markdown("---")
    st.subheader("⚙️ Paramètres Financiers & Matelas de Sécurité (`.env`)")
    env_c1, env_c2, env_c3 = st.columns(3)
    new_buffer = env_c1.number_input("🛡️ Matelas Livret A Sanctuarisé (€)", value=float(os.getenv("EMERGENCY_FUND_BUFFER_EUR", "13500.0")), step=500.0)
    new_sal = env_c2.number_input("💼 Salaire Net Socle Mensuel (€)", value=float(os.getenv("SALARY_BASE_NET_MONTHLY_EUR", "5500.0")), step=100.0)
    new_bonus = env_c3.number_input("🎯 Prime Annuelle Nette (€)", value=float(os.getenv("ANNUAL_BONUS_NET_EUR", "8000.0")), step=500.0)

    if st.button("💾 Mettre à jour les paramètres dans .env"):
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                env_lines = f.readlines()
            updates = {
                "EMERGENCY_FUND_BUFFER_EUR": f"{new_buffer:.2f}",
                "SALARY_BASE_NET_MONTHLY_EUR": f"{new_sal:.2f}",
                "ANNUAL_BONUS_NET_EUR": f"{new_bonus:.2f}",
            }
            new_env_lines = []
            seen_keys = set()
            for line in env_lines:
                k = line.split("=")[0].strip() if "=" in line else ""
                if k in updates:
                    new_env_lines.append(f"{k}={updates[k]}\n")
                    seen_keys.add(k)
                else:
                    new_env_lines.append(line)
            for k, v in updates.items():
                if k not in seen_keys:
                    new_env_lines.append(f"{k}={v}\n")
            with open(".env", "w", encoding="utf-8") as f:
                f.writelines(new_env_lines)
            st.cache_data.clear()
            send_telegram_notify(f"⚙️ *PARAMÈTRES PATRIMONIAUX MIS À JOUR*\n• Buffer Livret A : **{new_buffer:,.0f} €**")
            st.success("✅ Paramètres enregistrés dans `.env` !")
            st.rerun()

    st.markdown("---")
    st.subheader("📜 Administration des Prompts System SSOT (`.md`)")
    if PROMPT_LOADER_AVAILABLE:
        all_p = sorted(list(set(list_available_prompts() + ["equity_advisor_speculative", "equity_risk_manager", "equity_macro_arbitrage", "equity_auto_tpsl", "wealth_advisory"])))
        sel_p = st.selectbox("Fichier Prompt à éditer", options=all_p, format_func=lambda x: f"prompts/{x}.md", key="sel_eq_prompt_admin")
        raw_p_txt = get_raw_prompt(sel_p)
        edited_p_txt = st.text_area(f"Contenu — prompts/{sel_p}.md", value=raw_p_txt, height=280, key=f"ta_eq_{sel_p}")
        pc1, pc2 = st.columns(2)
        if pc1.button("💾 Enregistrer le Prompt", type="primary", key=f"sv_eq_{sel_p}"):
            if save_system_prompt(sel_p, edited_p_txt):
                st.cache_data.clear()
                send_telegram_notify(f"📜 *PROMPT SSOT BOURSE MODIFIÉ*\n• `prompts/{sel_p}.md` sauvegardé (backup `.bak` créé).")
                st.success(f"✅ Prompt `prompts/{sel_p}.md` sauvegardé (backup `.bak` créé) !")
                st.rerun()
        if pc2.button("🔄 Restaurer Backup (.bak)", key=f"rs_eq_{sel_p}"):
            if restore_backup_prompt(sel_p):
                st.cache_data.clear()
                send_telegram_notify(f"🔄 *PROMPT SSOT BOURSE RESTAURÉ*\n• `prompts/{sel_p}.md` restauré depuis `.bak`.")
                st.success(f"✅ Prompt `prompts/{sel_p}.md` restauré !")
                st.rerun()

    st.markdown("---")
    st.subheader("📜 Journaux Système en Direct (Systemd Logs)")
    log_srv = st.selectbox("Service à inspecter", ["equity-streamlit.service", "equity-cloudflare.service", "finary-bot.service"])
    if st.button("🔄 Rafraîchir les logs", key="btn_eq_logs"):
        st.rerun()
    try:
        res_log = subprocess.run(f"journalctl -u {log_srv} -n 25 --no-pager", shell=True, capture_output=True, text=True, timeout=3)
        out_log = res_log.stdout if res_log.returncode == 0 and res_log.stdout.strip() else f"Astuce SSH : sudo journalctl -u {log_srv} -n 25"
    except Exception as e:
        out_log = str(e)
    st.text_area(f"Logs pour {log_srv}", out_log, height=220)