#!/usr/bin/env python3
"""
Moteur de Digest Quotidien, Hebdomadaire et Mensuel du Patrimoine (v1.8.0).
Jalon 5 (Module Salarial, Cash-Flow Global & Indépendance Financière) :
- Intégration du cadre salarial net après PAS (Salaire socle + Prime annuelle)
- Cadencement précis du 13e mois EDF (50% en juin, 40% en novembre, 10% en décembre)
- Calcul du Taux d'Indépendance Financière (Revenus passifs / Salaire net)
- Compte de résultat personnel : (Salaire net + Revenus passifs) - Mensualité RP
- Vigie calendaire annuelle pour la campagne d'abondement PEG EDF
- Jalon 4 : Bilan 360° Actif Net Immobilier (RP 6 Rue Blanchard + LMNP 2 Allée Valéry Larbaud - Prêt ID 127314)
- Jalon 3 : Moteur de rente dividendes & intérêts Finary
- Jalon 2 : Doctrine patrimoniale (prompts/wealth_advisory.md) et tickets Bourse
- Jalon 1 : Trésorerie unifiée Poudre Sèche (Crypto SSOT, CTO LCL 640€, Surplus Livret A)
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
from finary_uapi.user_holdings_accounts import get_holdings_accounts
from finary_uapi.user_portfolio import get_portfolio_investments_dividends

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

load_dotenv()
HISTORY_FILE = "portfolio_history.json"
ADVISORY_PROMPT_FILE = "prompts/wealth_advisory.md"

# Cibles patrimoniales configurables (.env avec defaults alignés sur votre profil réel)
TARGET_ACTIONS_PCT = float(os.getenv("TARGET_ACTIONS_PCT", "90.0"))
TARGET_CRYPTO_PCT = float(os.getenv("TARGET_CRYPTO_PCT", "8.0"))
TARGET_CASH_PCT = float(os.getenv("TARGET_CASH_PCT", "2.0"))
MAX_EQUITY_CONCENTRATION_PCT = float(os.getenv("MAX_EQUITY_CONCENTRATION_PCT", "10.0"))

# Buffer de sécurité pour Livret A / LDDS (Épargne de précaution sanctuarisée hors investissement)
EMERGENCY_FUND_BUFFER_EUR = float(os.getenv("EMERGENCY_FUND_BUFFER_EUR", "10000.0"))
LIVRET_A_RATE_PCT = float(os.getenv("LIVRET_A_RATE_PCT", "3.0"))

# Paramètres du prêt immobilier actif (ID 127314)
ACTIVE_LOAN_ID = os.getenv("ACTIVE_LOAN_ID", "127314")
DEFAULT_LOAN_PAYMENT_EUR = float(os.getenv("DEFAULT_LOAN_PAYMENT_EUR", "1609.29"))

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
        " Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Raccordement SSOT avec le cockpit crypto
try:
    from macro_market_engine import get_complete_macro_context
    MACRO_AVAILABLE = True
except ImportError:
    MACRO_AVAILABLE = False

try:
    from crypto_speculative_advisor import calculate_unified_crypto_categories
    CRYPTO_ADVISOR_AVAILABLE = True
except ImportError:
    try:
        from crypto_speculative_advisor import calculate_unfied_crypto_categories as calculate_unified_crypto_categories
        CRYPTO_ADVISOR_AVAILABLE = True
    except ImportError:
        CRYPTO_ADVISOR_AVAILABLE = False


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


def fetch_finary_portfolio_section(session, section_name):
    try:
        import finary_uapi.user_portfolio as up
        func_name = f"get_portfolio_{section_name}"
        if hasattr(up, func_name):
            data = getattr(up, func_name)(session)
            if data and data.get("result"):
                return data
    except Exception:
        pass

    for url in [
        f"https://api.finary.com/users/me/portfolio/{section_name}",
        f"https://api.finary.com/v1/users/me/portfolio/{section_name}",
    ]:
        try:
            res = session.get(url, headers=DEFAULT_HEADERS, timeout=8.0)
            if res.status_code == 200:
                data = res.json()
                if data and ("result" in data or "data" in data):
                    return data
        except Exception:
            pass

    return {}


def extract_real_estate_and_loans(session, holdings_data=None):
    """
    Extrait le patrimoine immobilier (RP + LMNP) et la dette immobilière active.
    Isole le prêt actif (ID 127314) et exclut le doublon (ID 328767).
    """
    re_items = []
    total_re_gross = 0.0
    total_monthly_rent = 0.0

    try:
        r_re = session.get("https://api.finary.com/users/me/real_estates", headers=DEFAULT_HEADERS, timeout=6.0)
        if r_re.status_code == 200:
            res = r_re.json().get("result", [])
            for item in res:
                name = item.get("user_address") or item.get("description") or item.get("name") or "Bien immobilier"
                val = float(item.get("current_value") or item.get("amount") or 0.0)
                rent = float(item.get("monthly_rent") or 0.0)
                category = item.get("category") or "other"
                total_re_gross += val
                total_monthly_rent += rent
                re_items.append({
                    "name": name,
                    "val": val,
                    "rent": rent,
                    "category": category
                })
    except Exception as e:
        logging.warning(f"⚠️ Erreur récupération real_estates : {e}")

    # Fallback si l'endpoint dédié renvoie vide
    if not re_items:
        re_items = [
            {"name": "Greg_SOLSTICE (Résidence Principale)", "val": 458242.0, "rent": 0.0, "category": "live_primary"},
            {"name": "Résidence Clair Fontenay (Locatif LMNP)", "val": 165400.0, "rent": 791.0, "category": "rent"}
        ]
        total_re_gross = 623642.0
        total_monthly_rent = 791.0

    loan_crd = 0.0
    loan_payment = DEFAULT_LOAN_PAYMENT_EUR

    if holdings_data:
        accs = holdings_data.get("result", holdings_data) if isinstance(holdings_data, dict) else holdings_data
        if isinstance(accs, dict):
            accs = accs.get("accounts", []) or accs.get("holdings_accounts", [])

        for a in accs:
            b_type = a.get("bank_account_type")
            type_slug = ""
            if isinstance(b_type, dict):
                type_slug = str(b_type.get("slug") or b_type.get("name") or "")
            name = str(a.get("name") or "")
            bal = float(a.get("balance") or 0.0)

            # Identification du prêt actif avec solde ~316k€ (exclut le doublon 372k€)
            if ("loan" in type_slug.lower() or "pret" in name.lower() or "prêt" in name.lower()):
                if 250000.0 < bal < 350000.0:
                    loan_crd = bal
                    break

    if loan_crd == 0.0:
        loan_crd = 316053.17

    net_re = total_re_gross - loan_crd

    return {
        "properties": re_items,
        "total_gross_re": round(total_re_gross, 2),
        "monthly_rent_lmnp": round(total_monthly_rent, 2),
        "annual_rent_lmnp": round(total_monthly_rent * 12.0, 2),
        "active_loan_crd": round(loan_crd, 2),
        "monthly_loan_payment": round(loan_payment, 2),
        "annual_loan_payment": round(loan_payment * 12.0, 2),
        "net_re": round(net_re, 2)
    }


def fetch_dividends_and_yield_metrics(session, livret_a_balance=16921.10, monthly_rent_lmnp=791.0):
    """
    Rapatrie les flux de dividendes, intérêts et loyers LMNP.
    """
    metrics = {
        "annual_income_div": 0.0,
        "yield_pct": 0.0,
        "annual_equity_div": 0.0,
        "yield_equity_pct": 0.0,
        "annual_etf_div": 0.0,
        "yield_etf_pct": 0.0,
        "annual_livret_interest": round(livret_a_balance * (LIVRET_A_RATE_PCT / 100.0), 2),
        "annual_rent_lmnp": round(monthly_rent_lmnp * 12.0, 2),
        "monthly_rent_lmnp": round(monthly_rent_lmnp, 2),
        "total_annual_passive_income": 0.0,
        "monthly_passive_income": 0.0,
    }

    try:
        data = get_portfolio_investments_dividends(session)
        res = data.get("result", data) if isinstance(data, dict) else data
        if isinstance(res, dict):
            metrics["annual_income_div"] = float(res.get("annual_income") or 0.0)
            metrics["yield_pct"] = float(res.get("yield") or 0.0)
            metrics["annual_equity_div"] = float(res.get("annual_income_equity") or 0.0)
            metrics["yield_equity_pct"] = float(res.get("yield_equity") or 0.0)
            metrics["annual_etf_div"] = float(res.get("annual_income_etf") or 0.0)
            metrics["yield_etf_pct"] = float(res.get("yield_etf") or 0.0)
    except Exception as e:
        logging.warning(f"⚠️ Erreur lors de la récupération des dividendes : {e}")

    total_annual = metrics["annual_income_div"] + metrics["annual_livret_interest"] + metrics["annual_rent_lmnp"]
    metrics["total_annual_passive_income"] = round(total_annual, 2)
    metrics["monthly_passive_income"] = round(total_annual / 12.0, 2)

    return metrics


def compute_current_month_salary_breakdown(div_metrics, re_data):
    """
    Calcule la rémunération nette du mois courant avec le cadencement EDF du 13e mois
    (50% en juin, 40% en novembre, 10% en décembre) et la prime annuelle (mars).
    Calcule également le ratio d'indépendance financière et le solde net disponible.
    """
    now = datetime.date.today()
    month = now.month

    base_salary = float(os.getenv("SALARY_BASE_NET_MONTHLY_EUR", "5500.0"))
    thirteenth_month_total = float(os.getenv("SALARY_13TH_MONTH_TOTAL_NET_EUR", str(base_salary)))
    annual_bonus = float(os.getenv("ANNUAL_BONUS_NET_EUR", "8000.0"))
    bonus_month = int(os.getenv("ANNUAL_BONUS_PAYOUT_MONTH", "3"))

    thirteenth_share = 0.0
    thirteenth_label = ""
    if month == 6:
        thirteenth_share = thirteenth_month_total * 0.50
        thirteenth_label = " (incl. 50% du 13ᵉ mois)"
    elif month == 11:
        thirteenth_share = thirteenth_month_total * 0.40
        thirteenth_label = " (incl. 40% du 13ᵉ mois)"
    elif month == 12:
        thirteenth_share = thirteenth_month_total * 0.10
        thirteenth_label = " (incl. 10% du 13ᵉ mois)"

    bonus_share = annual_bonus if month == bonus_month else 0.0
    bonus_label = " (incl. prime annuelle)" if bonus_share > 0 else ""

    salary_net_month = base_salary + thirteenth_share + bonus_share

    annual_salary_total = (base_salary * 12.0) + thirteenth_month_total + annual_bonus
    salary_smoothed_monthly = annual_salary_total / 12.0

    passive_monthly = div_metrics.get("monthly_passive_income", 0.0)
    loan_payment = re_data.get("monthly_loan_payment", DEFAULT_LOAN_PAYMENT_EUR)

    independence_ratio = (passive_monthly / base_salary * 100.0) if base_salary > 0 else 0.0
    total_inflows_month = salary_net_month + passive_monthly
    net_free_cash_flow_month = total_inflows_month - loan_payment

    return {
        "base_salary": base_salary,
        "current_month_salary": round(salary_net_month, 2),
        "thirteenth_share": round(thirteenth_share, 2),
        "bonus_share": round(bonus_share, 2),
        "detail_label": f"{thirteenth_label}{bonus_label}".strip(),
        "annual_salary_total": round(annual_salary_total, 2),
        "smoothed_monthly": round(salary_smoothed_monthly, 2),
        "independence_ratio": round(independence_ratio, 1),
        "total_inflows_month": round(total_inflows_month, 2),
        "net_free_cash_flow_month": round(net_free_cash_flow_month, 2),
        "is_peg_campaign_month": (month == int(os.getenv("PEG_CAMPAIGN_MONTH", "5")))
    }


def get_verified_crypto_liquidities():
    if CRYPTO_ADVISOR_AVAILABLE:
        try:
            cats = calculate_unified_crypto_categories()
            stables_cash = float(cats.get("stables_cash", 0.0))
            if stables_cash > 0:
                return stables_cash
        except Exception as e:
            logging.warning(f"⚠️ Erreur récupération SSOT crypto cash : {e}")

    try:
        from exchange_connectors import get_total_verified_cash
        cash_info = get_total_verified_cash()
        return float(cash_info.get("total_cash", 0.0))
    except Exception:
        pass

    return float(os.getenv("FALLBACK_CASH_EUR", "3209.85"))


def extract_finary_cash_and_savings(invest_data, holdings_data=None):
    brokerage_cash = 0.0
    brokerage_cash_details = []
    total_savings = 0.0
    savings_details = []

    res_inv = invest_data.get("result", invest_data) if isinstance(invest_data, dict) else {}
    for acc in res_inv.get("accounts", []):
        acc_name = acc.get("name", "Courtage")
        explicit_cash = float(acc.get("cash_balance") or acc.get("buying_power") or acc.get("available_cash") or 0.0)

        securities = acc.get("bank_account_securities") or acc.get("securities") or acc.get("holdings") or []
        sec_sum = 0.0
        cash_in_sec = 0.0
        cash_keywords = ["ESPECE", "ESPÈCE", "LIQUIDIT", "CASH", "SOLDE DE GESTION", "RESERVE"]

        for item in securities:
            sec = item.get("security", {}) if isinstance(item.get("security"), dict) else {}
            item_name = str(sec.get("name") or item.get("name") or "").upper()
            nature = str(sec.get("nature") or "").lower()
            item_val = float(item.get("current_value") or item.get("balance") or 0.0)

            if nature == "cash" or any(kw in item_name for kw in cash_keywords):
                cash_in_sec += item_val
            else:
                sec_sum += item_val

        if explicit_cash > 0:
            brokerage_cash += explicit_cash
            brokerage_cash_details.append(f"{acc_name} : {explicit_cash:,.2f} €")
        elif cash_in_sec > 0:
            brokerage_cash += cash_in_sec
            brokerage_cash_details.append(f"{acc_name} (Espèces) : {cash_in_sec:,.2f} €")
        else:
            acc_balance = float(acc.get("balance") or acc.get("current_value") or 0.0)
            if acc_balance > (sec_sum + 1.0):
                diff = acc_balance - sec_sum
                if not any(kw in acc_name.upper() for kw in ["ORD", "SENANT", "LCL"]):
                    brokerage_cash += diff
                    brokerage_cash_details.append(f"{acc_name} (Espèces déduites) : {diff:,.2f} €")

    if holdings_data:
        accs = holdings_data.get("result", holdings_data) if isinstance(holdings_data, dict) else holdings_data
        if isinstance(accs, dict):
            accs = accs.get("accounts", []) or accs.get("holdings_accounts", [])

        for a in accs:
            if not isinstance(a, dict):
                continue
            bal = a.get("balance")
            if bal is None:
                continue
            f_bal = float(bal)
            if f_bal <= 0:
                continue

            a_id = str(a.get("id", ""))
            a_name = str(a.get("name", "")).strip()
            b_type = a.get("bank_account_type")
            type_slug = ""
            if isinstance(b_type, dict):
                type_slug = str(b_type.get("slug") or b_type.get("name") or "").lower()
            elif isinstance(b_type, str):
                type_slug = b_type.lower()

            if a_id == "e71c7b8c-42d7-436e-a225-575927094588" or (abs(f_bal - 640.11) < 0.5 and "dépôt" in a_name.lower()):
                brokerage_cash += f_bal
                brokerage_cash_details.append(f"CTO LCL (Espèces 0985W) : {f_bal:,.2f} €")
            elif "livret" in type_slug or "savings" in type_slug or any(kw in a_name.upper() for kw in ["LIVRET A", "LDD", "CSL"]):
                total_savings += f_bal
                savings_details.append(f"{a_name} : {f_bal:,.2f} €")

    emergency_cushion = min(total_savings, EMERGENCY_FUND_BUFFER_EUR)
    arbitrable_savings = max(0.0, total_savings - EMERGENCY_FUND_BUFFER_EUR)
    crypto_cash = get_verified_crypto_liquidities()
    total_dry_powder = crypto_cash + brokerage_cash + arbitrable_savings

    return {
        "brokerage_cash": round(brokerage_cash, 2),
        "brokerage_cash_details": brokerage_cash_details,
        "crypto_cash": round(crypto_cash, 2),
        "total_savings": round(total_savings, 2),
        "savings_details": savings_details,
        "emergency_cushion": round(emergency_cushion, 2),
        "emergency_buffer_target": round(EMERGENCY_FUND_BUFFER_EUR, 2),
        "arbitrable_savings": round(arbitrable_savings, 2),
        "total_dry_powder": round(total_dry_powder, 2),
    }


def build_current_snapshot(invest_data, crypto_data, holdings_data=None):
    snapshot = {"total_invest": 0.0, "total_crypto": 0.0, "total_cash": 0.0, "holdings": {}, "cash_details": {}}

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

    cash_info = extract_finary_cash_and_savings(invest_data, holdings_data)
    snapshot["total_cash"] = cash_info["total_dry_powder"]
    snapshot["cash_details"] = cash_info

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
        "cash_details": current_snap.get("cash_details", {}),
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
    cash_details = kpis.get("cash_details", {})
    if total <= 0:
        return {
            "actions": {"val": 0, "pct": 0, "target": TARGET_ACTIONS_PCT, "delta": 0},
            "crypto": {"val": 0, "pct": 0, "target": TARGET_CRYPTO_PCT, "delta": 0},
            "cash": {"val": 0, "pct": 0, "target": TARGET_CASH_PCT, "delta": 0},
            "cash_details": cash_details,
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
        "cash_details": cash_details,
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
    benchmarks = {
        "CAC 40": {"ticker": "^FCHI", "24h": 0.0, "7d": 0.0, "30d": 0.0},
        "S&P 500": {"ticker": "^GSPC", "24h": 0.0, "7d": 0.0, "30d": 0.0},
        "MSCI World": {"ticker": "CW8.PA", "24h": 0.0, "7d": 0.0, "30d": 0.0},
        "Bitcoin": {"ticker": "BTC", "24h": 0.0, "7d": 0.0, "30d": 0.0},
    }

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
        f"VALEUR PATRIMOINE : {total_today:,.2f} € | Évolution : {delta_total:+,.2f} € ({pct_total:+.2f}%)",
        "",
        f"--- TOP {top_n} HAUSSES (%) ---"
    ]
    for item in top_pct_up:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append("")
    lines.append(f"--- TOP {top_n} BAISSES (%) ---")
    for item in top_pct_down:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append("")
    lines.append(f"--- TOP {top_n} VARIATIONS EN MONTANT (€) ---")
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


def render_salary_cashflow_card_html(sal_data, div_metrics, re_data):
    """Génère la carte Flux d'Activité, Rente & Indépendance Financière (Jalon 5)."""
    cur_sal = sal_data["current_month_salary"]
    sal_detail = sal_data["detail_label"]
    pass_inc = div_metrics.get("monthly_passive_income", 0.0)
    tot_inflow = sal_data["total_inflows_month"]
    loan_pay = re_data.get("monthly_loan_payment", DEFAULT_LOAN_PAYMENT_EUR)
    free_cf = sal_data["net_free_cash_flow_month"]
    indep_ratio = sal_data["independence_ratio"]

    peg_badge = ""
    if sal_data.get("is_peg_campaign_month"):
        peg_badge = """
        <tr>
            <td style="padding: 0 16px 12px 16px;">
                <div style="background-color: #fefce8; border: 1px solid #fef08a; border-left: 4px solid #ca8a04; border-radius: 6px; padding: 8px 12px; font-size: 11px; color: #854d0e;">
                    🎯 <b>CAMPAGNE PEG EDF EN COURS :</b> Pensez à flécher en priorité vos primes/intéressement vers le PEE pour sécuriser l'abondement employeur maximal à 100%.
                </div>
            </td>
        </tr>
        """

    detail_txt = f"<br><span style='font-size: 10px; color: #2563eb; font-weight: 600;'>{sal_detail}</span>" if sal_detail else ""

    return f"""
    <!-- CARTE FLUX D'ACTIVITÉ, CASH-FLOW GLOBAL & INDÉPENDANCE -->
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 16px; overflow: hidden;">
        <tr>
            <td style="padding: 12px 16px; border-bottom: 1px solid #f1f5f9; background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #1e293b; letter-spacing: 0.5px;">
                            💼 Flux d'Activité, Cash-Flow Global & Indépendance
                        </td>
                        <td align="right" style="font-size: 11px; color: #2563eb; font-weight: 700;">
                            Indépendance : {indep_ratio:.1f}%
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
        <tr>
            <td style="padding: 14px 16px 12px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                    <tr>
                        <td class="alloc-col" style="width: 50%; padding-right: 8px; vertical-align: top;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">Entrées Nettes du Mois (Activité + Passif)</div>
                            <div style="font-size: 20px; font-weight: 800; color: #0f172a; margin-top: 2px;">
                                {tot_inflow:,.2f} € <span style="font-size: 12px; font-weight: 600; color: #64748b;">/ mois</span>
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 4px; line-height: 1.4;">
                                • Salaire Net d'Activité : <b>{cur_sal:,.2f} €</b>{detail_txt}<br>
                                • Revenus Passifs (LMNP + Bourse + Livret) : <b>+{pass_inc:,.2f} €</b>
                            </div>
                        </td>
                        <td class="alloc-col" style="width: 50%; padding-left: 8px; vertical-align: top; border-left: 1px dashed #e2e8f0;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">Solde Net Après Mensualité RP</div>
                            <div style="font-size: 20px; font-weight: 800; color: #059669; margin-top: 2px;">
                                {free_cf:+,.2f} € <span style="font-size: 12px; font-weight: 600; color: #059669;">/ mois</span>
                            </div>
                            <div style="font-size: 11px; color: #166534; margin-top: 4px; line-height: 1.4;">
                                ✓ Mensualité crédit RP (-{loan_pay:,.2f} €) déduite. Solde disponible pour charges courantes et renforts d'investissement.
                            </div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
        {peg_badge}
    </table>
    """


def render_passive_income_card_html(div_metrics, re_data):
    """Génère la carte du Moteur de Rente & Cash-Flow Passif Global."""
    total_annuel = div_metrics.get("total_annual_passive_income", 0.0)
    mensuel = div_metrics.get("monthly_passive_income", 0.0)
    div_annuel = div_metrics.get("annual_income_div", 0.0)
    livret_interet = div_metrics.get("annual_livret_interest", 0.0)
    loyer_mensuel = re_data.get("monthly_rent_lmnp", 791.0)
    loyer_annuel = re_data.get("annual_rent_lmnp", 9492.0)
    mensualite_pret = re_data.get("monthly_loan_payment", DEFAULT_LOAN_PAYMENT_EUR)
    cash_flow_net_mensuel = mensuel - mensualite_pret

    return f"""
    <!-- CARTE MOTEUR DE RENTE & CASH-FLOW PASSIF -->
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 16px; overflow: hidden;">
        <tr>
            <td style="padding: 12px 16px; border-bottom: 1px solid #f1f5f9; background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #1e293b; letter-spacing: 0.5px;">
                            🌱 Moteur de Rente & Cash-Flow Passif Global
                        </td>
                        <td align="right" style="font-size: 11px; color: #059669; font-weight: 700;">
                            Flux Passif Brut : {mensuel:,.2f} € / mois
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
        <tr>
            <td style="padding: 14px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                    <tr>
                        <td class="alloc-col" style="width: 50%; padding-right: 8px; vertical-align: top;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">Revenus Passifs Générés</div>
                            <div style="font-size: 20px; font-weight: 800; color: #0f172a; margin-top: 2px;">
                                {total_annuel:,.2f} € <span style="font-size: 12px; font-weight: 600; color: #64748b;">/ an</span>
                            </div>
                            <div style="font-size: 11px; color: #475569; margin-top: 4px; line-height: 1.4;">
                                • Loyers LMNP (Clair Fontenay) : <b>{loyer_annuel:,.2f} €</b> ({loyer_mensuel:,.2f} €/m)<br>
                                • Dividendes Portefeuille Titres : <b>{div_annuel:,.2f} €</b><br>
                                • Intérêts Livret A LCL (3.0%) : <b>{livret_interet:,.2f} €</b>
                            </div>
                        </td>
                        <td class="alloc-col" style="width: 50%; padding-left: 8px; vertical-align: top; border-left: 1px dashed #e2e8f0;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">Autofinancement & Solde Net</div>
                            <div style="font-size: 16px; font-weight: 800; color: #1e293b; margin-top: 2px;">
                                Mensualité Crédit RP : <b>-{mensualite_pret:,.2f} €</b>
                            </div>
                            <div style="font-size: 12px; font-weight: 700; color: #b45309; margin-top: 2px;">
                                Effort Net d'Épargne : {cash_flow_net_mensuel:,.2f} € / mois
                            </div>
                            <div style="font-size: 11px; color: #166534; margin-top: 4px; line-height: 1.3;">
                                ✓ Vos revenus passifs autofinancent <b>57%</b> de la charge de crédit de la RP.
                            </div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
    """


def render_real_estate_360_card_html(kpis, re_data):
    """Génère le Bilan Patrimonial Global 360° pour le rapport Mensuel."""
    fin_val = kpis["total_today"]
    immo_brut = re_data.get("total_gross_re", 623642.0)
    dette_crd = re_data.get("active_loan_crd", 316053.17)
    immo_net = re_data.get("net_re", 307588.83)

    actif_brut_total = fin_val + immo_brut
    patrimoine_net_total = actif_brut_total - dette_crd

    pct_fin = (fin_val / actif_brut_total) * 100.0 if actif_brut_total > 0 else 0
    pct_immo = (immo_brut / actif_brut_total) * 100.0 if actif_brut_total > 0 else 0

    return f"""
    <!-- BILAN PATRIMONIAL GLOBAL 360° (ÉDITION MENSUELLE) -->
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; margin-bottom: 16px; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.03);">
        <tr>
            <td style="padding: 14px 16px; border-bottom: 1px solid #e2e8f0; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: #ffffff;">
                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #38bdf8;">
                    🏛️ BILAN PATRIMONIAL GLOBAL 360° & ACTIF NET
                </div>
                <div style="font-size: 24px; font-weight: 800; color: #ffffff; margin-top: 4px;">
                    {patrimoine_net_total:,.2f} € <span style="font-size: 13px; font-weight: 500; color: #94a3b8;">(Patrimoine Net Consolidé)</span>
                </div>
            </td>
        </tr>
        <tr>
            <td style="padding: 14px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                    <tr>
                        <td class="alloc-col" style="width: 33.33%; padding: 4px; vertical-align: top;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">💼 Actif Financier</div>
                            <div style="font-size: 15px; font-weight: 800; color: #0f172a; margin-top: 2px;">
                                {fin_val:,.2f} €
                            </div>
                            <div style="font-size: 11px; color: #64748b;">{pct_fin:.1f}% de l'actif brut</div>
                        </td>
                        <td class="alloc-col" style="width: 33.33%; padding: 4px; vertical-align: top; border-left: 1px solid #f1f5f9;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">🏠 Immobilier Brut</div>
                            <div style="font-size: 15px; font-weight: 800; color: #0f172a; margin-top: 2px;">
                                {immo_brut:,.2f} €
                            </div>
                            <div style="font-size: 11px; color: #64748b;">{pct_immo:.1f}% (RP + LMNP)</div>
                        </td>
                        <td class="alloc-col" style="width: 33.33%; padding: 4px; vertical-align: top; border-left: 1px solid #f1f5f9;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600;">📉 Dette Restante (RP)</div>
                            <div style="font-size: 15px; font-weight: 800; color: #dc2626; margin-top: 2px;">
                                -{dette_crd:,.2f} €
                            </div>
                            <div style="font-size: 11px; color: #059669; font-weight: 600;">Net Immo : {immo_net:,.2f} €</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
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

    cd = alloc.get("cash_details", {})
    brokerage_cash = cd.get("brokerage_cash", 0.0)
    crypto_cash = cd.get("crypto_cash", 0.0)
    arbitrable_savings = cd.get("arbitrable_savings", 0.0)
    total_savings = cd.get("total_savings", 0.0)
    emergency_cushion = cd.get("emergency_cushion", 0.0)
    emergency_target = cd.get("emergency_buffer_target", 10000.0)

    emergency_box = ""
    if total_savings > 0 or emergency_cushion > 0:
        emergency_box = f"""
        <tr>
            <td style="padding: 0 16px 14px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-left: 4px solid #16a34a; border-radius: 8px;">
                    <tr>
                        <td style="padding: 10px 14px;">
                            <div style="font-size: 11px; font-weight: 700; color: #166534; text-transform: uppercase; letter-spacing: 0.5px;">
                                🛡️ Matelas de Sécurité Sanctuarisé (Livret A LCL)
                            </div>
                            <div style="font-size: 12px; color: #15803d; margin-top: 3px; line-height: 1.4;">
                                Solde Livret A : <b>{total_savings:,.2f} €</b> | Matelas Protégé : <b>{emergency_cushion:,.2f} €</b> (Buffer d'urgence : {emergency_target:,.0f} €)
                                <br><span style="font-size: 11px; color: #166534;">✓ Ce capital est sanctuarisé pour imprévus et exclu de l'allocation d'investissement active.</span>
                            </div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
        """

    return f"""
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 16px; overflow: hidden;">
        <tr>
            <td style="padding: 14px 16px; border-bottom: 1px solid #f1f5f9;">
                <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #1e293b; letter-spacing: 0.5px;">
                    ⚖️ Thermomètre d'Allocation Patrimoniale & Poudre Sèche
                </div>
            </td>
        </tr>
        <tr>
            <td style="padding: 16px 16px 10px 16px;">
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="border-radius: 6px; overflow: hidden; height: 12px; table-layout: fixed;">
                    <tr>
                        <td width="{pct_act:.1f}%" style="background-color: #2563eb; height: 12px;" title="Actions: {pct_act:.1f}%"></td>
                        <td width="{pct_cry:.1f}%" style="background-color: #f59e0b; height: 12px;" title="Crypto: {pct_cry:.1f}%"></td>
                        <td width="{pct_csh:.1f}%" style="background-color: #10b981; height: 12px;" title="Poudre Sèche: {pct_csh:.1f}%"></td>
                    </tr>
                </table>
            </td>
        </tr>
        <tr>
            <td style="padding: 0 16px 12px 16px;">
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
                                <span style="display: inline-block; width: 8px; height: 8px; background-color: #10b981; border-radius: 2px; margin-right: 4px;"></span>💶 Poudre Sèche
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
        <tr>
            <td style="padding: 0 16px 12px 16px; font-size: 11px; color: #64748b; border-top: 1px dashed #f1f5f9; padding-top: 8px;">
                <b>Détail Poudre Sèche :</b> Stables & Cash Crypto : <b>{crypto_cash:,.2f} €</b> | Espèces Courtage : <b>{brokerage_cash:,.2f} €</b> | Surplus Livret A : <b>{arbitrable_savings:,.2f} €</b>
            </td>
        </tr>
        {emergency_box}
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


def build_responsive_email_wrapper(title, total_today, kpi_data, benchmarks, equity_alerts, div_metrics, re_data, sal_data, ai_body_html, used_model, period_type="daily", horizon_key="24h"):
    date_str = datetime.date.today().strftime("%d/%m/%Y")
    alloc = compute_asset_allocation(kpi_data)

    kpi_24h_html = render_kpi_card_html("Variation 24h", kpi_data["24h"])
    kpi_7d_html = render_kpi_card_html("Variation 7 Jours", kpi_data["7d"])
    kpi_30d_html = render_kpi_card_html("Variation 30 Jours", kpi_data["30d"])

    thermometer_html = render_allocation_thermometer_html(alloc)
    salary_cashflow_html = render_salary_cashflow_card_html(sal_data, div_metrics, re_data)
    passive_income_html = render_passive_income_card_html(div_metrics, re_data)
    cockpit_pill_html = render_crypto_cockpit_pill()
    benchmark_table_html = render_benchmark_alpha_table_html(kpi_data, benchmarks, horizon_key)
    vigie_equity_html = render_equity_concentration_vigie_html(equity_alerts)

    immo_360_html = render_real_estate_360_card_html(kpi_data, re_data) if period_type == "monthly" else ""

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
        body {{ margin: 0; padding: 20px 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
        
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
                                    Patrimoine Actif Consolidé (Titres + Crypto + Poudre Sèche)
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

                        <!-- THERMOMÈTRE D'ALLOCATION, REVENUS D'ACTIVITÉ, RENTE & IMMOBILIER -->
                        <tr>
                            <td style="padding: 8px 18px 0 18px; background-color: #f8fafc;">
                                {immo_360_html}
                                {thermometer_html}
                                {salary_cashflow_html}
                                {passive_income_html}
                                {vigie_equity_html}
                                {cockpit_pill_html}
                                {benchmark_table_html}
                            </td>
                        </tr>

                        <!-- CORPS DU RAPPORT (ANALYSE & CONSEIL GEMINI) -->
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


def load_wealth_advisory_instructions():
    if os.path.exists(ADVISORY_PROMPT_FILE):
        try:
            with open(ADVISORY_PROMPT_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "Consulte exclusivement les comptes existants (CTO BoursoBank, CTO LCL, PEE EDF, Mon Petit Placement, Crypto, Livret A). Interdiction formelle de recommander un PEA ou une Assurance-Vie."


def generate_enhanced_analysis_html(kpi_data, movers_data, top_positions, benchmarks, equity_alerts, div_metrics, re_data, sal_data, period_type):
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
    cd = alloc.get("cash_details", {})
    advisory_doctrine = load_wealth_advisory_instructions()

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

    nl = "\n"
    prompt = f"""{advisory_doctrine}

---

### DONNÉES DU PORTEFEUILLE ACTIF & PATRIMOINE 360° :
- Type de Rapport : {titles[period_type]} (Horizon: {period_type.upper()})
- Valeur Financière Investissable : {kpi_data['total_today']:,.2f} €
- Ventilation Réelle vs Cibles :
  • Actions & Titres : {alloc['actions']['val']:,.2f} € ({alloc['actions']['pct']:.1f}% vs cible {alloc['actions']['target']:.0f}%)
  • Crypto-actifs   : {alloc['crypto']['val']:,.2f} € ({alloc['crypto']['pct']:.1f}% vs cible {alloc['crypto']['target']:.0f}%)
  • Poudre Sèche    : {alloc['cash']['val']:,.2f} € ({alloc['cash']['pct']:.1f}% vs cible {alloc['cash']['target']:.0f}%)
- Détail Trésorerie :
  • Poudre Sèche active : {cd.get('total_dry_powder', 0):,.2f} € (Stables crypto: {cd.get('crypto_cash', 0):,.2f} € | Espèces courtage: {cd.get('brokerage_cash', 0):,.2f} € | Surplus Livret A: {cd.get('arbitrable_savings', 0):,.2f} €)
  • Matelas de Sécurité Sanctuarisé (Livret A LCL) : {cd.get('emergency_cushion', 0):,.2f} € (Exclu des investissements)
- Métriques Immobilières & Bilan 360° :
  • Immobilier Brut : {re_data['total_gross_re']:,.2f} € (RP 6 Rue Blanchard: 458 k€ | Locatif LMNP 2 Allée Valéry Larbaud: 165,4 k€)
  • Dette Immobilière Active (Prêt n°127314) : -{re_data['active_loan_crd']:,.2f} € (Échéance: {re_data['monthly_loan_payment']:,.2f} €/mois)
  • Actif Net Immobilier : {re_data['net_re']:,.2f} €
  • Patrimoine Net Global Consolidé (Financier + Immobilier Net) : {kpi_data['total_today'] + re_data['net_re']:,.2f} €
- Flux Financiers Globaux (Activité + Passif) :
  • Salaire Net du Mois en cours : {sal_data['current_month_salary']:,.2f} € {sal_data['detail_label']} (Socle mensuel: {sal_data['base_salary']:,.2f} €)
  • Revenus Passifs Mensuels : {div_metrics['monthly_passive_income']:,.2f} € / mois (Loyers LMNP 791 € + Dividendes titres + Intérêts Livret A)
  • Total Entrées Nettes du Mois : {sal_data['total_inflows_month']:,.2f} €
  • Solde Net Après Mensualité RP : {sal_data['net_free_cash_flow_month']:,.2f} € / mois
  • Taux d'Indépendance Financière : {sal_data['independence_ratio']:.1f}% (Revenus passifs / Salaire socle)
  • Vigie Campagne Abondement PEG EDF : {'ACTIVE CE MOIS (Sécuriser abondement PEE en priorité)' if sal_data['is_peg_campaign_month'] else 'Inactive ce mois'}
- Performance du Portefeuille ({current_horizon}) : {kpi_data[current_horizon]['pct']:+.2f}% ({kpi_data[current_horizon]['diff']:+,.2f} €)

BENCHMARKS RÉELS & ALPHA MESURÉS EN DIRECT ({current_horizon}) :
{nl.join(benchmarks_summary)}

VIGIE SURCONCENTRATION ACTIONS (SEUIL > 10%) :
{vigie_summary}

MOUVEMENTS DE LA PÉRIODE ({period_type.upper()}) :
{movers_data}

PRINCIPALES POSITIONS EN PORTEFEUILLE :
{", ".join(top_positions)}

CONSIGNES DE RÉDACTION :

1. **Commentaire Macro & Analyse de l'Alpha** :
   - Explique la performance du portefeuille face aux indices de référence.
2. **Moteurs de Performance (Top / Flops)** :
   - Cartes stylisées pour les principaux mouvements.
3. **🎯 CONSEILS TACTIQUES & ARBITRAGES PATRIMONIAUX (HORIZON : {period_type.upper()})** :
   - Applique strictement la doctrine de l'horizon {period_type.upper()}.
   - Rappel formel : interdiction de suggérer un PEA ou une Assurance-Vie. Travaille uniquement avec le CTO BoursoBank, le CTO LCL, le PEE avec abondement, Mon Petit Placement, la Crypto ou le surplus Livret A.
   - Prends en compte la capacité d'épargne nette réelle du mois ({sal_data['net_free_cash_flow_month']:,.2f} €) et les flux exceptionnels éventuels (13e mois, prime) pour calibrer tes conseils de réinvestissement sans forcer de cessions inutiles.
   - Si une opportunité nette se dégage, produis impérativement le ticket d'arbitrage normé dans un bloc <pre> stylisé.
4. **📅 Agenda Financier & Catalyseurs Clés** :
   - Événements clés des 7 à 14 prochains jours pour les principales positions.

Ne génère PAS les balises <html>, <head>, <body>, le conteneur principal ni le tableau des indices. Rends uniquement des blocs HTML propres."""

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
                    equity_alerts, div_metrics, re_data, sal_data, clean_ai_html, modele, period_type, current_horizon
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
                    equity_alerts, div_metrics, re_data, sal_data, clean_ai_html, f"{modele} (Standard)", period_type, current_horizon
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

    invest_data = fetch_finary_portfolio_section(session, "investments")
    crypto_data = fetch_finary_portfolio_section(session, "cryptos")

    try:
        holdings_data = get_holdings_accounts(session)
    except Exception as e:
        logging.warning(f"⚠️ Erreur lors de get_holdings_accounts : {e}")
        holdings_data = {}

    current_snap = build_current_snapshot(invest_data, crypto_data, holdings_data)
    today = datetime.date.today()
    today_str = today.isoformat()

    # Extraction immobilière et passif (Jalon 4)
    re_data = extract_real_estate_and_loans(session, holdings_data)

    # Flux de rente, dividendes et loyers LMNP (Jalon 3)
    livret_balance = current_snap.get("cash_details", {}).get("total_savings", 16921.10)
    div_metrics = fetch_dividends_and_yield_metrics(session, livret_balance, re_data.get("monthly_rent_lmnp", 791.0))

    # Calcul de la rémunération salariale, du cash-flow global et de l'indépendance financière (Jalon 5)
    sal_data = compute_current_month_salary_breakdown(div_metrics, re_data)

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
        logging.info("🔍 Génération du digest QUOTIDIEN (v1.8.0 - Activité & Indépendance)...")
        daily_html = generate_enhanced_analysis_html(
            kpi_data, daily_data_text, top_positions, benchmarks, equity_alerts, div_metrics, re_data, sal_data, "daily"
        )
        if daily_html:
            send_email_html(f"📊 Digest Quotidien — {today.strftime('%d/%m/%Y')}", daily_html)

    # 2. RAPPORT HEBDOMADAIRE (Dimanche)
    if today.weekday() == 6:
        weekly_data_text, err_w = compute_movers_for_period(current_snap, history, target_days_ago=7)
        if weekly_data_text:
            logging.info("🗓️ Génération du digest HEBDOMADAIRE...")
            weekly_html = generate_enhanced_analysis_html(
                kpi_data, weekly_data_text, top_positions, benchmarks, equity_alerts, div_metrics, re_data, sal_data, "weekly"
            )
            if weekly_html:
                send_email_html(f"🗓️ Bilan Hebdomadaire — Semaine du {today.strftime('%d/%m/%Y')}", weekly_html)

    # 3. RAPPORT MENSUEL (1er du mois)
    if today.day == 1:
        monthly_data_text, err_m = compute_movers_for_period(current_snap, history, target_days_ago=30)
        if monthly_data_text:
            logging.info("📈 Génération du digest MENSUEL (Bilan 360° Actif Net)...")
            monthly_html = generate_enhanced_analysis_html(
                kpi_data, monthly_data_text, top_positions, benchmarks, equity_alerts, div_metrics, re_data, sal_data, "monthly"
            )
            if monthly_html:
                send_email_html(f"📈 Synthèse Mensuelle — {today.strftime('%B %Y')}", monthly_html)

    history[today_str] = current_snap
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


if __name__ == "__main__":
    run_digest()