#!/usr/bin/env python3
"""
Moteur de Digest Quotidien, Hebdomadaire et Mensuel du Patrimoine (v1.3.1).
Intègre :
- Cibles d'allocation patrimoniale personnalisables (90% Actions / 8% Crypto / 2% Cash)
- Résolution du blocage des indices Yahoo Finance (curl_cffi impersonate Chrome)
- Rapatriement direct du Bitcoin via l'API CoinGecko
- Benchmarking Quantitatif Réel & Calcul d'Alpha vs Marché (CAC 40, S&P 500, MSCI World, BTC)
- Vigie de Surconcentration Actions (Seuil 10% sur actions vives hors ETF)
- Thermomètre d'Allocation Globale avec jauge segmentée
- Passerelle Cockpit Crypto SSOT (Boussole Macro, Fear & Greed et Cash vérifié)
- Architecture HTML Email Responsive Hybride (PC / Mobile-First)
"""

import os
import json
import logging
import http.cookiejar
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pyotp
import time
from dotenv import load_dotenv
from curl_cffi import requests as curl_requests
from google import genai
import requests

from finary_uapi.signin import signin
from finary_uapi.user_portfolio import get_portfolio_investments, get_portfolio_cryptos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

load_dotenv()
HISTORY_FILE = "portfolio_history.json"

# Cibles patrimoniales configurables (.env avec defaults alignés sur votre profil réel)
TARGET_ACTIONS_PCT = float(os.getenv("TARGET_ACTIONS_PCT", "90.0"))
TARGET_CRYPTO_PCT = float(os.getenv("TARGET_CRYPTO_PCT", "8.0"))
TARGET_CASH_PCT = float(os.getenv("TARGET_CASH_PCT", "2.0"))
MAX_EQUITY_CONCENTRATION_PCT = float(os.getenv("MAX_EQUITY_CONCENTRATION_PCT", "10.0"))

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
        " Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Raccordement optionnel au Cockpit Crypto SSOT local
try:
    from macro_market_engine import get_complete_macro_context
    MACRO_AVAILABLE = True
except ImportError:
    MACRO_AVAILABLE = False

try:
    from exchange_connectors import get_total_verified_cash
    CASH_CONNECTOR_AVAILABLE = True
except ImportError:
    CASH_CONNECTOR_AVAILABLE = False


def extract_jwt_token(filepath="jwt.json"):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if raw.startswith("eyJ") or raw.startswith('"eyJ'):
            return raw.strip('"\'')
        data = json.loads(raw)

        def search_jwt(obj):
            if isinstance(obj, str) and obj.startswith("eyJ"):
                return obj
            if isinstance(obj, dict):
                for val in obj.values():
                    res = search_jwt(val)
                    if res:
                        return res
            if isinstance(obj, list):
                for item in obj:
                    res = search_jwt(item)
                    if res:
                        return res
            return None

        return search_jwt(data)
    except Exception:
        return None


def init_finary_session():
    secret_2fa = os.getenv("FINARY_2FA_SECRET")
    otp_code = pyotp.TOTP(secret_2fa).now() if secret_2fa else ""
    signin(otp_code=otp_code)

    session = curl_requests.Session(impersonate="chrome110")
    token = extract_jwt_token("jwt.json")
    if token:
        clean_token = token.strip('"\'')
        session.headers["Authorization"] = f"Bearer {clean_token}"
        session.headers["x-auth-token"] = clean_token

    if os.path.exists("localCookiesMozilla.txt"):
        try:
            cj = http.cookiejar.MozillaCookieJar("localCookiesMozilla.txt")
            cj.load(ignore_discard=True, ignore_expires=True)
            for cookie in cj:
                session.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        except Exception:
            pass

    return session


def get_verified_liquidities():
    if CASH_CONNECTOR_AVAILABLE:
        try:
            cash_info = get_total_verified_cash()
            val = float(cash_info.get("total_cash", 0.0))
            if val > 0:
                return val
        except Exception:
            pass
    return float(os.getenv("FALLBACK_CASH_EUR", "0.0"))


def build_current_snapshot(invest_data, crypto_data):
    snapshot = {"total_invest": 0.0, "total_crypto": 0.0, "total_cash": 0.0, "holdings": {}}
    
    res_inv = invest_data.get("result", invest_data) if isinstance(invest_data, dict) else {}
    tot_inv = res_inv.get("total", {}) if isinstance(res_inv, dict) else {}
    snapshot["total_invest"] = float(tot_inv.get("amount", 0) or 0)
    
    for acc in res_inv.get("accounts", []):
        acc_name = acc.get("name", "Inconnu")
        securities = acc.get("bank_account_securities") or acc.get("securities") or acc.get("holdings") or []
        for item in securities:
            sec = item.get("security", {}) if isinstance(item.get("security"), dict) else {}
            item_name = sec.get("name") or item.get("name") or "Titre inconnu"
            item_val = float(item.get("current_value") or item.get("balance") or 0)
            snapshot["holdings"][f"{item_name} ({acc_name})"] = item_val

    res_cry = crypto_data.get("result", crypto_data) if isinstance(crypto_data, dict) else {}
    tot_cry = res_cry.get("total", {}) if isinstance(res_cry, dict) else {}
    snapshot["total_crypto"] = float(tot_cry.get("amount", 0) or 0)
    
    for acc in res_cry.get("accounts", []):
        acc_name = acc.get("name", "Inconnu")
        cryptos = acc.get("crypto_balances") or acc.get("securities") or acc.get("holdings") or []
        for item in cryptos:
            cry = item.get("crypto", {}) if isinstance(item.get("crypto"), dict) else {}
            item_name = cry.get("name") or item.get("name") or "Crypto inconnue"
            item_val = float(item.get("current_value") or item.get("balance") or 0)
            snapshot["holdings"][f"{item_name} ({acc_name})"] = item_val

    snapshot["total_cash"] = get_verified_liquidities()
    return snapshot


def get_closest_past_snapshot(history, target_days_ago):
    today = datetime.date.today()
    target_date = today - datetime.timedelta(days=target_days_ago)
    
    past_dates = [datetime.date.fromisoformat(d) for d in history.keys() if datetime.date.fromisoformat(d) < today]
    if not past_dates:
        return None, None

    closest_date = min(past_dates, key=lambda d: abs((d - target_date).days))
    if target_days_ago > 1 and abs((target_date - closest_date).days) > 7:
        return None, None

    return closest_date.isoformat(), history[closest_date.isoformat()]


def compute_multi_horizon_kpis(current_snap, history):
    total_today = current_snap["total_invest"] + current_snap["total_crypto"] + current_snap.get("total_cash", 0.0)
    kpis = {
        "total_today": total_today,
        "total_invest": current_snap.get("total_invest", 0.0),
        "total_crypto": current_snap.get("total_crypto", 0.0),
        "total_cash": current_snap.get("total_cash", 0.0),
    }

    for days, key in [(1, "24h"), (7, "7d"), (30, "30d")]:
        past_date_str, prev_snap = get_closest_past_snapshot(history, days)
        if prev_snap:
            tot_prev = prev_snap.get("total_invest", 0) + prev_snap.get("total_crypto", 0) + prev_snap.get("total_cash", 0.0)
            diff = total_today - tot_prev
            pct = (diff / tot_prev * 100) if tot_prev > 0 else 0
            kpis[key] = {
                "diff": diff,
                "pct": pct,
                "ref_date": past_date_str,
                "text": f"{diff:+,.2f} € ({pct:+.2f}%)"
            }
        else:
            kpis[key] = {
                "diff": 0.0,
                "pct": 0.0,
                "ref_date": "N/A",
                "text": "N/A"
            }

    return kpis


def compute_asset_allocation(kpis):
    total = kpis["total_today"]
    if total <= 0:
        return {
            "actions": {"val": 0, "pct": 0, "target": TARGET_ACTIONS_PCT, "delta": 0},
            "crypto": {"val": 0, "pct": 0, "target": TARGET_CRYPTO_PCT, "delta": 0},
            "cash": {"val": 0, "pct": 0, "target": TARGET_CASH_PCT, "delta": 0},
        }

    val_inv = kpis["total_invest"]
    val_cry = kpis["total_crypto"]
    val_csh = kpis["total_cash"]

    pct_inv = (val_inv / total) * 100.0
    pct_cry = (val_cry / total) * 100.0
    pct_csh = (val_csh / total) * 100.0

    return {
        "actions": {
            "val": val_inv,
            "pct": pct_inv,
            "target": TARGET_ACTIONS_PCT,
            "delta": pct_inv - TARGET_ACTIONS_PCT,
        },
        "crypto": {
            "val": val_cry,
            "pct": pct_cry,
            "target": TARGET_CRYPTO_PCT,
            "delta": pct_cry - TARGET_CRYPTO_PCT,
        },
        "cash": {
            "val": val_csh,
            "pct": pct_csh,
            "target": TARGET_CASH_PCT,
            "delta": pct_csh - TARGET_CASH_PCT,
        },
    }


def audit_equity_concentration(current_snap):
    total_invest = current_snap.get("total_invest", 0.0)
    if total_invest <= 0:
        return []

    etf_keywords = ["ETF", "WORLD", "CW8", "WPEA", "S&P", "500", "STOXX", "MSCI", "LYXOR", "AMUNDI", "ISHARES", "PEA 0%"]
    alerts = []

    for item_name, item_val in current_snap.get("holdings", {}).items():
        if any(cry in item_name.upper() for cry in ["BITCOIN", "ETHEREUM", "SOLANA", "KRAKEN", "OKX", "EXODUS"]):
            continue

        clean_name = item_name.upper()
        is_etf = any(kw in clean_name for kw in etf_keywords)

        if not is_etf:
            weight_pct = (item_val / total_invest) * 100.0
            if weight_pct >= MAX_EQUITY_CONCENTRATION_PCT:
                alerts.append({
                    "name": item_name.split("(")[0].strip(),
                    "val": item_val,
                    "weight_pct": weight_pct,
                    "cap_pct": MAX_EQUITY_CONCENTRATION_PCT,
                    "delta_eur": item_val - (total_invest * (MAX_EQUITY_CONCENTRATION_PCT / 100.0)),
                })

    return sorted(alerts, key=lambda x: x["weight_pct"], reverse=True)


def fetch_benchmark_returns():
    """
    Récupère les performances réelles des 4 indices de référence (24h, 7j, 30j).
    Utilise curl_cffi (impersonate Chrome) pour contourner le blocage Yahoo Finance sur GCP,
    et CoinGecko pour le Bitcoin.
    """
    benchmarks = {
        "CAC 40": {"ticker": "^FCHI", "24h": 0.0, "7d": 0.0, "30d": 0.0},
        "S&P 500": {"ticker": "^GSPC", "24h": 0.0, "7d": 0.0, "30d": 0.0},
        "MSCI World": {"ticker": "CW8.PA", "24h": 0.0, "7d": 0.0, "30d": 0.0},
        "Bitcoin": {"ticker": "BTC", "24h": 0.0, "7d": 0.0, "30d": 0.0},
    }

    # 1. Rapatriement direct du Bitcoin via CoinGecko
    try:
        url_btc = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=eur&days=32&interval=daily"
        r_btc = curl_requests.get(url_btc, headers=DEFAULT_HEADERS, timeout=4.0)
        if r_btc.status_code == 200:
            prices = [p[1] for p in r_btc.json().get("prices", []) if p[1] > 0]
            if len(prices) >= 2:
                last_p = prices[-1]
                p_1d = prices[-2]
                p_7d = prices[-8] if len(prices) >= 8 else prices[0]
                p_30d = prices[0]
                benchmarks["Bitcoin"]["24h"] = round(((last_p - p_1d) / p_1d) * 100.0, 2)
                benchmarks["Bitcoin"]["7d"] = round(((last_p - p_7d) / p_7d) * 100.0, 2)
                benchmarks["Bitcoin"]["30d"] = round(((last_p - p_30d) / p_30d) * 100.0, 2)
    except Exception as e:
        logging.warning(f"⚠️ Erreur benchmark BTC CoinGecko : {e}")

    # 2. Rapatriement des indices boursiers via Yahoo Finance sous curl_cffi (bypass HTTP 403)
    session_y = curl_requests.Session(impersonate="chrome110")
    for name in ["CAC 40", "S&P 500", "MSCI World"]:
        sym = benchmarks[name]["ticker"]
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
            res = session_y.get(url, headers=DEFAULT_HEADERS, timeout=5.0)
            if res.status_code == 200:
                chart = res.json().get("chart", {}).get("result", [])
                if chart:
                    closes = chart[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                    valid_closes = [c for c in closes if c is not None and c > 0]
                    if len(valid_closes) >= 2:
                        last = valid_closes[-1]
                        prev_1d = valid_closes[-2]
                        prev_7d = valid_closes[-6] if len(valid_closes) >= 6 else valid_closes[0]
                        prev_30d = valid_closes[0]

                        benchmarks[name]["24h"] = round(((last - prev_1d) / prev_1d) * 100.0, 2)
                        benchmarks[name]["7d"] = round(((last - prev_7d) / prev_7d) * 100.0, 2)
                        benchmarks[name]["30d"] = round(((last - prev_30d) / prev_30d) * 100.0, 2)
            else:
                logging.warning(f"⚠️ Échec Yahoo Finance pour {name} (HTTP {res.status_code})")
        except Exception as e:
            logging.warning(f"⚠️ Erreur récupération indice {name} : {e}")

    return benchmarks


def extract_top_positions(current_snap, top_k=6):
    sorted_holdings = sorted(current_snap["holdings"].items(), key=lambda x: x[1], reverse=True)
    return [name for name, val in sorted_holdings[:top_k]]


def compute_movers_for_period(current_snap, history, target_days_ago):
    past_date_str, prev_snap = get_closest_past_snapshot(history, target_days_ago)
    if not prev_snap:
        return None, f"Historique insuffisant pour calculer la période {target_days_ago} jours."

    total_today = current_snap["total_invest"] + current_snap["total_crypto"] + current_snap.get("total_cash", 0.0)
    total_prev = prev_snap.get("total_invest", 0) + prev_snap.get("total_crypto", 0) + prev_snap.get("total_cash", 0.0)
    delta_total = total_today - total_prev
    pct_total = (delta_total / total_prev * 100) if total_prev > 0 else 0

    holding_diffs = []
    for h_name, val_today in current_snap["holdings"].items():
        val_prev = prev_snap.get("holdings", {}).get(h_name, val_today)
        diff = val_today - val_prev
        pct = (diff / val_prev * 100) if val_prev > 0 else 0
        if abs(diff) > 0.05:
            holding_diffs.append({"name": h_name, "val": val_today, "diff": diff, "pct": pct})

    top_n = int(os.getenv("TOP_N_ASSETS", "3"))

    top_pct_up = sorted([x for x in holding_diffs if x["pct"] > 0], key=lambda x: x["pct"], reverse=True)[:top_n]
    top_pct_down = sorted([x for x in holding_diffs if x["pct"] < 0], key=lambda x: x["pct"])[:top_n]
    top_eur_up = sorted([x for x in holding_diffs if x["diff"] > 0], key=lambda x: x["diff"], reverse=True)[:top_n]
    top_eur_down = sorted([x for x in holding_diffs if x["diff"] < 0], key=lambda x: x["diff"])[:top_n]

    lines = [
        f"PÉRIODE ANALYSÉE : Rapprochement vs {past_date_str}",
        f"VALEUR PATRIMOINE : {total_today:,.2f} € | Évolution : {delta_total:+,.2f} € ({pct_total:+.2f}%)\n",
        f"--- TOP {top_n} HAUSSES (%) ---"
    ]
    for item in top_pct_up:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append(f"\n--- TOP {top_n} BAISSES (%) ---")
    for item in top_pct_down:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append(f"\n--- TOP {top_n} VARIATIONS EN MONTANT (€) ---")
    for item in top_eur_up:
        lines.append(f"• [+] {item['name']} : {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")
    for item in top_eur_down:
        lines.append(f"• [-] {item['name']} : {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    return "\n".join(lines), None


def render_kpi_card_html(label, kpi_obj):
    diff = kpi_obj.get("diff", 0.0)
    pct = kpi_obj.get("pct", 0.0)
    ref_date = kpi_obj.get("ref_date", "")

    if diff > 0:
        badge_bg = "#ecfdf5"
        text_color = "#059669"
        arrow = "▲"
    elif diff < 0:
        badge_bg = "#fef2f2"
        text_color = "#dc2626"
        arrow = "▼"
    else:
        badge_bg = "#f3f4f6"
        text_color = "#4b5563"
        arrow = "—"

    return f"""
    <td class="kpi-column" style="padding: 6px; width: 33.33%; vertical-align: top;">
        <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; text-align: center;">
            <tr>
                <td style="font-size: 11px; font-weight: 700; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; padding-bottom: 4px;">
                    {label}
                </td>
            </tr>
            <tr>
                <td style="font-size: 17px; font-weight: 800; color: {text_color}; padding-bottom: 4px;">
                    {arrow} {diff:+,.2f} €
                </td>
            </tr>
            <tr>
                <td>
                    <span style="display: inline-block; background-color: {badge_bg}; color: {text_color}; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px;">
                        {pct:+.2f}%
                    </span>
                </td>
            </tr>
            <tr>
                <td style="font-size: 9px; color: #9ca3af; padding-top: 4px;">
                    Réf : {ref_date}
                </td>
            </tr>
        </table>
    </td>
    """


def render_allocation_thermometer_html(alloc):
    pct_act = max(1.0, alloc["actions"]["pct"])
    pct_cry = max(1.0, alloc["crypto"]["pct"])
    pct_csh = max(1.0, alloc["cash"]["pct"])

    def status_badge(delta):
        if delta > 2.0:
            return '<span style="color: #f59e0b; font-weight: 700;">▲ Surpondéré</span>'
        elif delta < -2.0:
            return '<span style="color: #3b82f6; font-weight: 700;">▼ Sous-pondéré</span>'
        return '<span style="color: #10b981; font-weight: 700;">✓ Aligné</span>'

    badge_act = status_badge(alloc["actions"]["delta"])
    badge_cry = status_badge(alloc["crypto"]["delta"])
    badge_csh = status_badge(alloc["cash"]["delta"])

    return f"""
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 16px; overflow: hidden;">
        <tr>
            <td style="padding: 14px 16px; border-bottom: 1px solid #f1f5f9;">
                <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #1e293b; letter-spacing: 0.5px;">
                    ⚖️ Thermomètre d'Allocation Patrimoniale
                </div>
            </td>
        </tr>
        <tr>
            <td style="padding: 16px 16px 10px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="border-radius: 6px; overflow: hidden; height: 12px; table-layout: fixed;">
                    <tr>
                        <td width="{pct_act:.1f}%" style="background-color: #2563eb; height: 12px;" title="Actions: {pct_act:.1f}%"></td>
                        <td width="{pct_cry:.1f}%" style="background-color: #f59e0b; height: 12px;" title="Crypto: {pct_cry:.1f}%"></td>
                        <td width="{pct_csh:.1f}%" style="background-color: #10b981; height: 12px;" title="Cash: {pct_csh:.1f}%"></td>
                    </tr>
                </table>
            </td>
        </tr>
        <tr>
            <td style="padding: 0 16px 14px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                    <tr>
                        <td class="alloc-col" style="width: 33.33%; padding: 6px; vertical-align: top;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">
                                <span style="display: inline-block; width: 8px; height: 8px; background-color: #2563eb; border-radius: 2px; margin-right: 4px;"></span>🏛️ Actions / Titres
                            </div>
                            <div style="font-size: 14px; font-weight: 800; color: #1e293b; margin-top: 2px;">
                                {alloc['actions']['val']:,.2f} €
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 1px;">
                                <b>{alloc['actions']['pct']:.1f}%</b> (Cible: {alloc['actions']['target']:.0f}%)
                            </div>
                            <div style="font-size: 10px; margin-top: 2px;">
                                {badge_act}
                            </div>
                        </td>
                        <td class="alloc-col" style="width: 33.33%; padding: 6px; vertical-align: top;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">
                                <span style="display: inline-block; width: 8px; height: 8px; background-color: #f59e0b; border-radius: 2px; margin-right: 4px;"></span>🚀 Crypto-Actifs
                            </div>
                            <div style="font-size: 14px; font-weight: 800; color: #1e293b; margin-top: 2px;">
                                {alloc['crypto']['val']:,.2f} €
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 1px;">
                                <b>{alloc['crypto']['pct']:.1f}%</b> (Cible: {alloc['crypto']['target']:.0f}%)
                            </div>
                            <div style="font-size: 10px; margin-top: 2px;">
                                {badge_cry}
                            </div>
                        </td>
                        <td class="alloc-col" style="width: 33.33%; padding: 6px; vertical-align: top;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">
                                <span style="display: inline-block; width: 8px; height: 8px; background-color: #10b981; border-radius: 2px; margin-right: 4px;"></span>💶 Cash & Stables
                            </div>
                            <div style="font-size: 14px; font-weight: 800; color: #1e293b; margin-top: 2px;">
                                {alloc['cash']['val']:,.2f} €
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 1px;">
                                <b>{alloc['cash']['pct']:.1f}%</b> (Cible: {alloc['cash']['target']:.0f}%)
                            </div>
                            <div style="font-size: 10px; margin-top: 2px;">
                                {badge_csh}
                            </div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
    """


def render_benchmark_alpha_table_html(kpis, benchmarks, horizon_key="24h"):
    port_ret = kpis.get(horizon_key, {}).get("pct", 0.0)

    rows = []
    for name, data in benchmarks.items():
        bench_ret = data.get(horizon_key, 0.0)
        alpha = port_ret - bench_ret

        color_alpha = "#059669" if alpha >= 0 else "#dc2626"
        badge_alpha = f"{alpha:+.2f}%"

        rows.append(f"""
        <tr>
            <td style="padding: 8px 10px; font-size: 12px; font-weight: 600; color: #334155; border-bottom: 1px solid #f1f5f9;">
                {name}
            </td>
            <td style="padding: 8px 10px; font-size: 12px; text-align: center; color: #64748b; border-bottom: 1px solid #f1f5f9;">
                {bench_ret:+.2f}%
            </td>
            <td style="padding: 8px 10px; font-size: 12px; font-weight: 700; text-align: right; color: {color_alpha}; border-bottom: 1px solid #f1f5f9;">
                {badge_alpha}
            </td>
        </tr>
        """)

    return f"""
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 16px; overflow: hidden;">
        <tr>
            <td colspan="3" style="padding: 12px 16px; border-bottom: 1px solid #f1f5f9; background-color: #f8fafc;">
                <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #1e293b; letter-spacing: 0.5px;">
                    🎯 Performance Réelle vs Marché & Alpha ({horizon_key.upper()})
                </div>
            </td>
        </tr>
        <tr>
            <th style="padding: 6px 10px; font-size: 11px; text-align: left; color: #94a3b8; font-weight: 600;">INDICE / ACTIF</th>
            <th style="padding: 6px 10px; font-size: 11px; text-align: center; color: #94a3b8; font-weight: 600;">PERF {horizon_key.upper()}</th>
            <th style="padding: 6px 10px; font-size: 11px; text-align: right; color: #94a3b8; font-weight: 600;">ALPHA PATRIMOINE</th>
        </tr>
        {''.join(rows)}
    </table>
    """


def render_equity_concentration_vigie_html(alerts):
    if not alerts:
        return ""

    items_html = []
    for a in alerts:
        items_html.append(
            f"• <b>{a['name']}</b> : <b>{a['weight_pct']:.1f}%</b> de la poche actions ({a['val']:,.2f} €) — Excédent cap 10% : <b>+{a['delta_eur']:,.2f} €</b>"
        )

    return f"""
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #fffbeb; border: 1px solid #fef3c7; border-left: 4px solid #f59e0b; border-radius: 8px; margin-bottom: 16px;">
        <tr>
            <td style="padding: 12px 16px;">
                <div style="font-size: 12px; font-weight: 700; color: #b45309; text-transform: uppercase; letter-spacing: 0.5px;">
                    ⚠️ Vigie Surconcentration Actions (&gt; {MAX_EQUITY_CONCENTRATION_PCT:.0f}% du portefeuille)
                </div>
                <div style="font-size: 12px; color: #78350f; margin-top: 6px; line-height: 1.5;">
                    {'<br>'.join(items_html)}
                </div>
            </td>
        </tr>
    </table>
    """


def render_crypto_cockpit_pill():
    if not MACRO_AVAILABLE:
        return ""
    try:
        macro = get_complete_macro_context()
        fng = macro.get("fear_and_greed", {}).get("value", 50)
        fng_class = macro.get("fear_and_greed", {}).get("classification", "Neutral")
        regime = macro.get("regime", {}).get("regime", "BALANCED_ROTATION")
        btc_d = macro.get("metrics", {}).get("btc_dominance", 59.0)

        regime_labels = {
            "RISK_OFF_BLEED": "🚨 RISK-OFF DÉFENSIF",
            "BTC_LED_MARKET": "🧭 MARCHÉ MENÉ PAR BTC",
            "BALANCED_ROTATION": "⚖️ CANAL D'ÉQUILIBRE",
            "ALT_EXPANSION": "🟢 ALTSEASON ACTIVE",
        }
        regime_txt = regime_labels.get(regime, regime)

        return f"""
        <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #0f172a; border-radius: 8px; margin-bottom: 16px; border-left: 4px solid #38bdf8;">
            <tr>
                <td style="padding: 10px 14px; font-size: 11px; color: #cbd5e1;">
                    <b style="color: #38bdf8;">⚡ VIGIE COCKPIT CRYPTO (SSOT) :</b> {regime_txt} (BTC.D : <b>{btc_d:.1f}%</b>) | Sentiment : <b>{fng}/100</b> ({fng_class})
                </td>
            </tr>
        </table>
        """
    except Exception:
        return ""


def build_responsive_email_wrapper(title, total_today, kpi_data, benchmarks, equity_alerts, ai_body_html, used_model, horizon_key="24h"):
    date_str = datetime.date.today().strftime("%d/%m/%Y")
    alloc = compute_asset_allocation(kpi_data)

    kpi_24h_html = render_kpi_card_html("Variation 24h", kpi_data["24h"])
    kpi_7d_html = render_kpi_card_html("Variation 7 Jours", kpi_data["7d"])
    kpi_30d_html = render_kpi_card_html("Variation 30 Jours", kpi_data["30d"])

    thermometer_html = render_allocation_thermometer_html(alloc)
    cockpit_pill_html = render_crypto_cockpit_pill()
    benchmark_table_html = render_benchmark_alpha_table_html(kpi_data, benchmarks, horizon_key)
    vigie_equity_html = render_equity_concentration_vigie_html(equity_alerts)

    return f"""<!DOCTYPE html>
<html lang="fr" xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <meta name="color-scheme" content="light dark">
    <meta name="supported-color-schemes" content="light dark">
    <title>{title}</title>
    <style type="text/css">
        body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
        body {{ margin: 0; padding: 0; width: 100% !important; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
        
        @media only screen and (max-width: 580px) {{
            .main-container {{ width: 100% !important; border-radius: 0 !important; }}
            .kpi-column {{ display: block !important; width: 100% !important; padding: 4px 0 !important; }}
            .alloc-col {{ display: block !important; width: 100% !important; padding: 8px 4px !important; border-bottom: 1px solid #f1f5f9; }}
            .mobile-padding {{ padding: 16px 12px !important; }}
            .hero-amount {{ font-size: 28px !important; }}
        }}
        @media (prefers-color-scheme: dark) {{
            body {{ background-color: #0f172a !important; color: #f8fafc !important; }}
            .main-container {{ background-color: #1e293b !important; border-color: #334155 !important; }}
            .card-box {{ background-color: #0f172a !important; border-color: #334155 !important; color: #f8fafc !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 20px 0; background-color: #f3f4f6;">
    <center>
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="table-layout: fixed;">
            <tr>
                <td align="center">
                    <table role="presentation" class="main-container" width="600" border="0" cellpadding="0" cellspacing="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
                        
                        <!-- HEADER & VALEUR GLOBALE -->
                        <tr>
                            <td class="mobile-padding" style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding: 28px 24px; text-align: center; color: #ffffff;">
                                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: #38bdf8; padding-bottom: 6px;">
                                    {title}
                                </div>
                                <div style="font-size: 12px; color: #94a3b8; padding-bottom: 12px;">
                                    Édition du {date_str} • Consolidation Patrimoniale Globale
                                </div>
                                <div class="hero-amount" style="font-size: 34px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">
                                    {total_today:,.2f} €
                                </div>
                                <div style="font-size: 12px; color: #cbd5e1; padding-top: 4px;">
                                    Patrimoine Financier & Spéculatif Total Consolidé
                                </div>
                            </td>
                        </tr>

                        <!-- TABLEAU DE BORD KPI RESPONSIVE -->
                        <tr>
                            <td style="padding: 16px 18px 8px 18px; background-color: #f8fafc;">
                                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                                    <tr>
                                        {kpi_24h_html}
                                        {kpi_7d_html}
                                        {kpi_30d_html}
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- THERMOMÈTRE D'ALLOCATION & BENCHMARKS REELS -->
                        <tr>
                            <td style="padding: 8px 18px 0 18px; background-color: #f8fafc;">
                                {thermometer_html}
                                {vigie_equity_html}
                                {cockpit_pill_html}
                                {benchmark_table_html}
                            </td>
                        </tr>

                        <!-- CORPS DU RAPPORT (ANALYSE GEMINI) -->
                        <tr>
                            <td class="mobile-padding card-box" style="padding: 20px; font-size: 14px; line-height: 1.6; color: #334155;">
                                {ai_body_html}
                            </td>
                        </tr>

                        <!-- FOOTER INSTITUTIONNEL -->
                        <tr>
                            <td style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 18px 20px; text-align: center; font-size: 11px; color: #94a3b8;">
                                <div>Digest Automatisé • Serveur GCP <code>finary-digest-server</code></div>
                                <div style="padding-top: 4px;">Modèle d'analyse IA : <b>{used_model}</b></div>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </center>
</body>
</html>
"""


def generate_enhanced_analysis_html(kpi_data, movers_data, top_positions, benchmarks, equity_alerts, period_type):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)

    titles = {
        "daily": "📊 Synthèse Quotidienne du Portefeuille",
        "weekly": "🗓️ Bilan Hebdomadaire du Portefeuille (7 jours)",
        "monthly": "📈 Synthèse Mensuelle du Portefeuille (30 jours)"
    }
    horizon_map = {"daily": "24h", "weekly": "7d", "monthly": "30d"}
    current_horizon = horizon_map[period_type]

    alloc = compute_asset_allocation(kpi_data)

    benchmarks_summary = []
    port_ret = kpi_data[current_horizon]["pct"]
    for b_name, b_val in benchmarks.items():
        b_ret = b_val.get(current_horizon, 0.0)
        alpha = port_ret - b_ret
        benchmarks_summary.append(f"• {b_name} : {b_ret:+.2f}% (Alpha Portefeuille : {alpha:+.2f}%)")

    vigie_summary = (
        "\n".join([f"• {a['name']}: {a['weight_pct']:.1f}% (> cap {a['cap_pct']:.0f}%)" for a in equity_alerts])
        if equity_alerts else "Aucun actif en surconcentration critique."
    )

    prompt = f"""Tu es un analyste financier privé senior. Génère les sections intérieures d'un rapport patrimonial.
Ne génère PAS les balises <html>, <head>, <body>, le conteneur principal, ni le tableau des indices (ils sont déjà intégrés dans le template).
Rends uniquement des blocs HTML propres (balises <div>, <p>, <span>) prêts à être injectés.

DONNÉES DU PORTEFEUILLE :
- Type de Rapport : {titles[period_type]}
- Valeur Totale : {kpi_data['total_today']:,.2f} €
- Allocation Réelle vs Cibles :
  • Actions & Titres : {alloc['actions']['val']:,.2f} € ({alloc['actions']['pct']:.1f}% vs cible {alloc['actions']['target']:.0f}%)
  • Crypto-actifs   : {alloc['crypto']['val']:,.2f} € ({alloc['crypto']['pct']:.1f}% vs cible {alloc['crypto']['target']:.0f}%)
  • Cash & Stables  : {alloc['cash']['val']:,.2f} € ({alloc['cash']['pct']:.1f}% vs cible {alloc['cash']['target']:.0f}%)
- Performance du Portefeuille ({current_horizon}) : {kpi_data[current_horizon]['pct']:+.2f}% ({kpi_data[current_horizon]['diff']:+,.2f} €)

BENCHMARKS RÉELS & ALPHA MESURÉS EN DIRECT ({current_horizon}) :
{chr(10).join(benchmarks_summary)}

VIGIE SURCONCENTRATION ACTIONS (SEUIL > 10%) :
{vigie_summary}

MOUVEMENTS DE LA PÉRIODE ({period_type.upper()}) :
{movers_data}

PRINCIPALES POSITIONS EN PORTEFEUILLE :
{", ".join(top_positions)}

CONSIGNES DE RÉDACTION :

1. **Commentaire Macro & Analyse de l'Alpha** :
   - Rédige un paragraphe synthétique expliquant la surperformance ou sous-performance du portefeuille face au S&P 500, au CAC 40 et au Bitcoin.
   - Mentionne brièvement si l'exposition actuelle a servi d'accélérateur ou de frein.

2. **Moteurs de Performance (Top / Flops)** :
   - Pour les principaux mouvements haussiers et baissiers, présente des cartes stylisées (fond blanc, bordure 1px solid #e2e8f0, padding 10px, coins arrondis 6px, marge 8px).
   - Donne l'explication économique ou l'actualité concrète expliquant ces variations.

3. **📅 Agenda Financier & Catalyseurs Clés** :
   - Recherche et liste sous forme de mini-cartes ou puces les événements clés des 7 à 14 prochains jours pour les principales positions ({", ".join(top_positions)}) :
     • Publications de résultats (Earnings).
     • Détachements de dividendes.
     • Catalyseurs sectoriels ou réunions de banques centrales.

Style : Épuré, professionnel, sans avertissement financier robotique."""

    modeles = [
        "gemini-4-argon",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite"
    ]

    for modele in modeles:
        try:
            logging.info(f"🤖 Tentative d'analyse avec {modele} (avec recherche Web)...")
            response = client.models.generate_content(
                model=modele,
                contents=prompt,
                config={"tools": [{"google_search": {}}]}
            )
            if response.text:
                clean_ai_html = response.text.replace("```html", "").replace("```", "").strip()
                return build_responsive_email_wrapper(
                    titles[period_type], kpi_data["total_today"], kpi_data, benchmarks,
                    equity_alerts, clean_ai_html, modele, current_horizon
                )
        except Exception as e:
            logging.warning(f"⚠️ Recherche Web ou modèle {modele} indisponible ({e}). Bascule en mode standard...")

        try:
            response = client.models.generate_content(
                model=modele,
                contents=prompt
            )
            if response.text:
                clean_ai_html = response.text.replace("```html", "").replace("```", "").strip()
                return build_responsive_email_wrapper(
                    titles[period_type], kpi_data["total_today"], kpi_data, benchmarks,
                    equity_alerts, clean_ai_html, f"{modele} (Standard)", current_horizon
                )
        except Exception as e:
            logging.error(f"❌ Échec du modèle {modele} en mode standard : {e}")

        time.sleep(2)

    raise RuntimeError("❌ Tous les modèles Gemini et modes de recherche ont échoué.")


def send_email_html(subject, html_content):
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL", smtp_email)

    if not smtp_email or not smtp_password:
        logging.warning("⚠️ Identifiants SMTP non configurés. E-mail non envoyé.")
        return

    msg = MIMEMultipart("alternative")
    msg['From'] = smtp_email
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.send_message(msg)
        server.quit()
        logging.info(f"📧 E-mail '{subject}' envoyé avec succès !")
    except Exception as e:
        logging.error(f"❌ Échec envoi e-mail '{subject}' : {e}")


def run_digest():
    session = init_finary_session()
    invest_data = get_portfolio_investments(session)
    crypto_data = get_portfolio_cryptos(session)

    current_snap = build_current_snapshot(invest_data, crypto_data)
    today = datetime.date.today()
    today_str = today.isoformat()

    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    kpi_data = compute_multi_horizon_kpis(current_snap, history)
    top_positions = extract_top_positions(current_snap)
    equity_alerts = audit_equity_concentration(current_snap)

    logging.info("📈 Récupération des cours des indices de référence (CAC 40, S&P 500, MSCI World, BTC)...")
    benchmarks = fetch_benchmark_returns()

    # 1. RAPPORT QUOTIDIEN
    daily_data_text, err = compute_movers_for_period(current_snap, history, target_days_ago=1)
    if daily_data_text:
        logging.info("🔍 Génération du digest QUOTIDIEN avec Benchmarks & Alpha réels...")
        daily_html = generate_enhanced_analysis_html(
            kpi_data, daily_data_text, top_positions, benchmarks, equity_alerts, "daily"
        )
        if daily_html:
            send_email_html(f"📊 Digest Quotidien — {today.strftime('%d/%m/%Y')}", daily_html)

    # 2. RAPPORT HEBDOMADAIRE (Dimanche)
    if today.weekday() == 6:
        weekly_data_text, err_w = compute_movers_for_period(current_snap, history, target_days_ago=7)
        if weekly_data_text:
            logging.info("🗓️ Génération du digest HEBDOMADAIRE avec Benchmarks & Alpha...")
            weekly_html = generate_enhanced_analysis_html(
                kpi_data, weekly_data_text, top_positions, benchmarks, equity_alerts, "weekly"
            )
            if weekly_html:
                send_email_html(f"🗓️ Bilan Hebdomadaire — Semaine du {today.strftime('%d/%m/%Y')}", weekly_html)

    # 3. RAPPORT MENSUEL (1er du mois)
    if today.day == 1:
        monthly_data_text, err_m = compute_movers_for_period(current_snap, history, target_days_ago=30)
        if monthly_data_text:
            logging.info("📈 Génération du digest MENSUEL avec Benchmarks & Alpha...")
            monthly_html = generate_enhanced_analysis_html(
                kpi_data, monthly_data_text, top_positions, benchmarks, equity_alerts, "monthly"
            )
            if monthly_html:
                send_email_html(f"📈 Synthèse Mensuelle — {today.strftime('%B %Y')}", monthly_html)

    history[today_str] = current_snap
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


if __name__ == "__main__":
    run_digest()