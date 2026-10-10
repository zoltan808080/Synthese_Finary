import os
import json
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

from finary_uapi.signin import signin
from finary_uapi.user_portfolio import get_portfolio_investments, get_portfolio_cryptos

load_dotenv()
HISTORY_FILE = "portfolio_history.json"

def generate_digest_with_fallback(client, prompt):
    """
    Appelle l'API Gemini avec bascule automatique sur un modèle de secours
    en cas d'erreur de charge (429, 500, 503) ou de saturation.
    """
    modeles = [
        "gemini-3.6-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash"
    ]

    for modele in modeles:
        logging.info(f"Tentative de génération du digest avec : {modele}")
        
        for tentative in range(1, 3):
            try:
                response = client.models.generate_content(
                    model=modele,
                    contents=prompt
                )
                if response.text:
                    logging.info(f"Digest généré avec succès via {modele}")
                    return response.text
            except Exception as e:
                logging.warning(f"Surcharge ou erreur sur {modele} (tentative {tentative}/2) : {e}")
                time.sleep(tentative * 3) # Pause progressive de 3s puis 6s

        logging.info(f"Bascule sur le modèle de secours suivant...")

    raise RuntimeError("Tous les modèles Gemini de la chaîne ont échoué.")


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
                    if res: return res
            if isinstance(obj, list):
                for item in obj:
                    res = search_jwt(item)
                    if res: return res
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

def build_current_snapshot(invest_data, crypto_data):
    snapshot = {"total_invest": 0.0, "total_crypto": 0.0, "holdings": {}}
    
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

    return snapshot

def get_closest_past_snapshot(history, target_days_ago):
    """Retrouve la photo du portefeuille la plus proche de N jours dans le passé."""
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
    """Calcule le Tableau de Bord KPI Multi-Horizons (24h, 7d, 30d)."""
    total_today = current_snap["total_invest"] + current_snap["total_crypto"]
    kpis = {"total_today": total_today}

    for days, key in [(1, "24h"), (7, "7d"), (30, "30d")]:
        past_date_str, prev_snap = get_closest_past_snapshot(history, days)
        if prev_snap:
            tot_prev = prev_snap.get("total_invest", 0) + prev_snap.get("total_crypto", 0)
            diff = total_today - tot_prev
            pct = (diff / tot_prev * 100) if tot_prev > 0 else 0
            kpis[key] = f"{diff:+,.2f} € ({pct:+.2f}%) [ref: {past_date_str}]"
        else:
            kpis[key] = "N/A (Historique insuffisant)"

    return kpis

def extract_top_positions(current_snap, top_k=6):
    """Extrait les plus grosses positions en capital pour cibler le calendrier d'événements."""
    sorted_holdings = sorted(current_snap["holdings"].items(), key=lambda x: x[1], reverse=True)
    return [name for name, val in sorted_holdings[:top_k]]

def compute_movers_for_period(current_snap, history, target_days_ago):
    """Calcule les variations sur la période donnée (1, 7 ou 30 jours)."""
    past_date_str, prev_snap = get_closest_past_snapshot(history, target_days_ago)
    if not prev_snap:
        return None, f"Historique insuffisant pour calculer la période {target_days_ago} jours."

    total_today = current_snap["total_invest"] + current_snap["total_crypto"]
    total_prev = prev_snap.get("total_invest", 0) + prev_snap.get("total_crypto", 0)
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
        f"PERIODE : Comparaison avec le {past_date_str}",
        f"PATRIMOINE TOTAL : {total_today:,.2f} € | Variation : {delta_total:+,.2f} € ({pct_total:+.2f}%)\n",
        f"--- TOP {top_n} HAUSSES EN % ---"
    ]
    for item in top_pct_up:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | Var: {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append(f"\n--- TOP {top_n} BAISSES EN % ---")
    for item in top_pct_down:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | Var: {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append(f"\n--- TOP {top_n} HAUSSES EN VALEUR (€) ---")
    for item in top_eur_up:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | Var: {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    lines.append(f"\n--- TOP {top_n} BAISSES EN VALEUR (€) ---")
    for item in top_eur_down:
        lines.append(f"• {item['name']} : {item['val']:,.2f} € | Var: {item['diff']:+,.2f} € ({item['pct']:+.2f}%)")

    return "\n".join(lines), None

def generate_enhanced_analysis_html(kpi_data, movers_data, top_positions, period_type):
    """Génère la synthèse HTML enrichie (Multi-KPIs, Benchmarks et Calendrier Événements)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)

    titles = {
        "daily": "📊 Synthèse Quotidienne du Portefeuille",
        "weekly": "🗓️ Bilan Hebdomadaire du Portefeuille (7 jours)",
        "monthly": "📈 Synthèse Mensuelle du Portefeuille (30 jours)"
    }

    is_special_report = period_type in ["weekly", "monthly"]

    prompt = f"""Tu es un analyste financier privé senior. Rédige un rapport de patrimoine enrichi au format HTML pur (sans balises ```html```).

DONNÉES DU PORTEFEUILLE :
- Type de Rapport : {titles[period_type]}
- Valeur Totale Actuelle : {kpi_data['total_today']:,.2f} €
- Tableau de bord Multi-Horizons :
  • Variation 24h  : {kpi_data['24h']}
  • Variation 7j   : {kpi_data['7d']}
  • Variation 30j  : {kpi_data['30d']}

MOUVEMENTS DE LA PÉRIODE ({period_type.upper()}) :
{movers_data}

PRINCIPALES POSITIONS DU PORTEFEUILLE (Cibles pour événements) :
{", ".join(top_positions)}

DIRECTIVES DE RÉDACTION HTML :
1. **Header & Dashboard Multi-Horizons** :
   - Présente la valeur totale actuelle.
   - Crée un tableau/grid moderne à 3 colonnes comparant les variations **24h**, **7 Jours** et **30 Jours** (très lisible avec fonds gris/bleu clair).

2. **Comparaison aux Indices de Référence (Benchmarks)** :
   - Crée une section dédiée comparant la performance du portefeuille à celle des grands indices sur la période ({period_type}) : **S&P 500**, **MSCI World**, **CAC 40** et **Bitcoin**.
   - Précise si le portefeuille surperforme ou sous-performe la tendance marché.

3. **Analyse des Moteurs de Performance (Top / Flops)** :
   - Cartes stylisées par actif (cartes blanches, bordure `#e2e8f0`, badges vert `#dcfce7`/rouge `#fee2e2`).
   - Analyse factuelle et économique expliquant les variations.

4. **📅 Calendrier des Événements à Venir (Résultats & Dividendes)** :
   - Recherche et liste sous forme de mini-cartes ou de liste à puces les prochains événements majeurs des 7 à 14 prochains jours concernant les principales positions ({", ".join(top_positions)}) :
     • Publications de résultats trimestriels (Earnings).
     • Dates de détachement/paiement de dividendes.
     • Prochaines réunions centrales (FED / BCE) ou catalyseurs macro.

Style HTML : Propre, moderne, responsive, police sans-serif (-apple-system, Segoe UI, Roboto). Pas d'avertissement financier."""

    # Chaîne de repli robuste (Recherche Web + Modèles de secours)
    modeles = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite"
    ]

    for modele in modeles:
        # 1. Tentative AVEC recherche Web (Google Search)
        try:
            print(f"🤖 Tentative d'analyse avec {modele} (avec recherche Web)...")
            response = client.models.generate_content(
                model=modele,
                contents=prompt,
                config={"tools": [{"google_search": {}}]}
            )
            if response.text:
                return response.text
        except Exception as e:
            print(f"⚠️ Recherche Web ou modèle {modele} indisponible ({e}). Repli en mode standard...")

        # 2. Repli SANS recherche Web sur le même modèle
        try:
            response = client.models.generate_content(
                model=modele,
                contents=prompt
            )
            if response.text:
                return response.text
        except Exception as e:
            print(f"❌ Échec du modèle {modele} en mode standard : {e}")

        # Pause de 2 secondes avant de basculer sur le modèle de secours suivant
        time.sleep(2)

    raise RuntimeError("❌ Tous les modèles Gemini et modes de recherche ont échoué.")


def send_email_html(subject, html_content):
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL", smtp_email)

    if not smtp_email or not smtp_password:
        return

    clean_html = html_content.replace("```html", "").replace("```", "").strip()

    msg = MIMEMultipart("alternative")
    msg['From'] = smtp_email
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(clean_html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"📧 E-mail '{subject}' envoyé avec succès !")
    except Exception as e:
        print(f"❌ Échec envoi e-mail '{subject}' : {e}")

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

    # Extraction des KPIs Multi-Horizons et des Top Positions
    kpi_data = compute_multi_horizon_kpis(current_snap, history)
    top_positions = extract_top_positions(current_snap)

    # 1. RAPPORT QUOTIDIEN (Tous les jours)
    daily_data_text, err = compute_movers_for_period(current_snap, history, target_days_ago=1)
    if daily_data_text:
        print("🔍 Génération du digest QUOTIDIEN enrichi...")
        daily_html = generate_enhanced_analysis_html(kpi_data, daily_data_text, top_positions, "daily")
        if daily_html:
            send_email_html(f"📊 Digest Quotidien — {today.strftime('%d/%m/%Y')}", daily_html)

    # 2. RAPPORT HEBDOMADAIRE (Chaque Dimanche - weekday() == 6)
    if today.weekday() == 6:
        weekly_data_text, err_w = compute_movers_for_period(current_snap, history, target_days_ago=7)
        if weekly_data_text:
            print("🗓️ Génération du digest HEBDOMADAIRE enrichi...")
            weekly_html = generate_enhanced_analysis_html(kpi_data, weekly_data_text, top_positions, "weekly")
            if weekly_html:
                send_email_html(f"🗓️ Bilan Hebdomadaire — Semaine du {today.strftime('%d/%m/%Y')}", weekly_html)

    # 3. RAPPORT MENSUEL (Le 1er du mois - day == 1)
    if today.day == 1:
        monthly_data_text, err_m = compute_movers_for_period(current_snap, history, target_days_ago=30)
        if monthly_data_text:
            print("📈 Génération du digest MENSUEL enrichi...")
            monthly_html = generate_enhanced_analysis_html(kpi_data, monthly_data_text, top_positions, "monthly")
            if monthly_html:
                send_email_html(f"📈 Synthèse Mensuelle — {today.strftime('%B %Y')}", monthly_html)

    # Sauvegarde de l'historique
    history[today_str] = current_snap
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

if __name__ == "__main__":
    run_digest()
