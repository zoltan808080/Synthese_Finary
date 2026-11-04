#!/usr/bin/env python3
"""Moteur d'auto-ajustement des paliers TP/SL par l'IA v3.4.0 (Benchmark BTC, SSOT Macro & Centralisation Gemini)."""

import json
import logging
import os
import re
import requests
from dotenv import load_dotenv

from db_manager import get_auto_tp_sl_status, get_open_positions, update_trade_levels
from gemini_manager import call_gemini_with_fallback
from prompt_loader import get_system_prompt

# Import du Moteur Macro SSOT
try:
    from macro_market_engine import format_macro_context_for_prompt
    MACRO_AVAILABLE = True
except ImportError:
    MACRO_AVAILABLE = False

try:
    from rsi_engine import build_rsi_heatmap_dataframe
    RSI_AVAILABLE = True
except ImportError:
    RSI_AVAILABLE = False

try:
    from funding_oi_engine import get_funding_oi_summary
    FOI_AVAILABLE = True
except ImportError:
    FOI_AVAILABLE = False

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def fetch_live_price(symbol, exchange="KRAKEN"):
    """Récupère le prix spot live d'un actif en EUR via Kraken ou OKX."""
    clean_sym = str(symbol).upper().replace("USDT", "").replace("EUR", "").strip()
    clean_exch = str(exchange).upper().strip() if exchange else "KRAKEN"
    headers = {"User-Agent": "Mozilla/5.0"}

    if clean_sym in ["EUR", "USD"]:
        return 1.0

    if "KRAKEN" in clean_exch:
        for pair in [f"{clean_sym}EUR", f"X{clean_sym}ZEUR"]:
            try:
                res = requests.get(f"https://api.kraken.com/0/public/Ticker?pair={pair}", headers=headers, timeout=4)
                if res.status_code == 200:
                    res_json = res.json().get("result", {})
                    if res_json:
                        k = list(res_json.keys())[0]
                        price = float(res_json[k]["c"][0])
                        if price > 0:
                            return price
            except Exception:
                pass

    if "OKX" in clean_exch:
        try:
            res = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-EUR", headers=headers, timeout=3)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    price = float(data[0]["last"])
                    if price > 0:
                        return price
        except Exception:
            pass
        try:
            res = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={clean_sym}-USDT", headers=headers, timeout=3)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    price = float(data[0]["last"])
                    if price > 0:
                        return price / 1.12
        except Exception:
            pass

    try:
        res = requests.get(f"https://api.kraken.com/0/public/Ticker?pair={clean_sym}EUR", headers=headers, timeout=3)
        if res.status_code == 200:
            res_json = res.json().get("result", {})
            if res_json:
                k = list(res_json.keys())[0]
                price = float(res_json[k]["c"][0])
                if price > 0:
                    return price
    except Exception:
        pass

    return 0.0


def run_auto_tpsl_cycle(manual_trigger=False):
    """Exécute un cycle de réévaluation et d'ajustement automatique des paliers TP/SL via Gemini (v3.4.0 Benchmark BTC)."""
    trigger_type = "MANUEL (Streamlit)" if manual_trigger else "AUTOMATIQUE (Cron 8h)"
    logging.info(f"🤖 Démarrage du cycle Auto TP/SL — Mode {trigger_type} v3.4.0 (Benchmark BTC)...")

    open_pos = get_open_positions()
    auto_map = get_auto_tp_sl_status()

    # Règle SSOT : Par défaut, si non spécifié, is_auto est à True
    target_positions = [p for p in open_pos if auto_map.get(p[0], True)]

    if not target_positions:
        logging.info("ℹ️ Aucune position active n'est configurée en mode Auto IA.")
        return {"updated_count": 0, "logs": ["Aucune position en mode Auto IA."]}

    symbols = [p[1] for p in target_positions]

    # Récupération du prix spot live du BTC pour le benchmarking Alpha (v3.4)
    btc_live_price = fetch_live_price("BTC", "KRAKEN") or fetch_live_price("BTC", "OKX") or 1.0

    # 1. Extraction du Contexte Macro SSOT
    macro_context = ""
    if MACRO_AVAILABLE:
        try:
            macro_context = f"\n{format_macro_context_for_prompt()}"
        except Exception as e:
            logging.warning(f"⚠️ Erreur chargement Macro : {e}")

    # 2. Extraction des indicateurs RSI
    rsi_context = ""
    if RSI_AVAILABLE:
        try:
            df_rsi = build_rsi_heatmap_dataframe(symbols)
            if not df_rsi.empty:
                rsi_context = f"\nINDICATEURS RSI EN DIRECT :\n{df_rsi.to_string(index=False)}"
        except Exception as e:
            logging.warning(f"⚠️ Erreur chargement RSI : {e}")

    # 3. Extraction de la Microstructure (Funding & OI)
    foi_context = ""
    if FOI_AVAILABLE:
        try:
            foi_data = get_funding_oi_summary(symbols)
            if foi_data and "pairs" in foi_data:
                foi_context = f"\nMICROSTRUCTURE & FUNDING RATES :\n{json.dumps(foi_data['pairs'], indent=2)}"
        except Exception as e:
            logging.warning(f"⚠️ Erreur chargement Funding/OI : {e}")

    # Structuration des données des positions (avec calcul d'Alpha vs BTC v3.4)
    positions_payload = []
    for p in target_positions:
        p_id, sym, exch, entry, amt, tp1, tp2, sl, sl_type, step = p[:10]
        entry = float(entry) if entry else 0.0
        amt = float(amt) if amt else 0.0
        tp1 = float(tp1) if tp1 else 0.0
        tp2 = float(tp2) if tp2 else 0.0
        sl = float(sl) if sl else 0.0

        # Extraction de btc_price_entry (Index 12 en BDD v3.4)
        btc_entry = float(p[12]) if (len(p) > 12 and p[12] is not None and float(p[12]) > 0) else btc_live_price

        current_price = fetch_live_price(sym, exch) or entry
        pnl_pct = (((current_price - entry) / entry) * 100.0) if entry > 0 else 0.0
        btc_pnl_pct = (((btc_live_price - btc_entry) / btc_entry) * 100.0) if btc_entry > 0 else 0.0
        alpha_vs_btc_pct = pnl_pct - btc_pnl_pct

        positions_payload.append({
            "id": p_id,
            "symbol": sym,
            "exchange": exch,
            "price_entry": round(entry, 4),
            "current_price": round(current_price, 4),
            "pnl_pct": round(pnl_pct, 2),
            "btc_price_entry": round(btc_entry, 2),
            "btc_current_price": round(btc_live_price, 2),
            "btc_pnl_pct": round(btc_pnl_pct, 2),
            "alpha_vs_btc_pct": round(alpha_vs_btc_pct, 2),
            "amount_eur": round(amt, 2),
            "current_tp1": round(tp1, 4),
            "current_tp2": round(tp2, 4),
            "current_sl": round(sl, 4),
            "step": step,
        })

    # Chargement du prompt SSOT (Project Rules + auto_tpsl.md)
    system_prompt = get_system_prompt("auto_tpsl")

    user_payload = f"""SITUATION DES POSITIONS À ÉVALUER (AVEC BENCHMARK BTC & ALPHA) :
{json.dumps(positions_payload, indent=2, ensure_ascii=False)}

{macro_context}

{rsi_context}

{foi_context}"""

    raw_response = None
    used_model = "Inconnu"

    try:
        raw_response, used_model = call_gemini_with_fallback(
            prompt_text=user_payload,
            system_instruction=system_prompt,
            json_mode=True,
        )
        logging.info(f"✅ Analyse Auto TP/SL générée avec succès via [{used_model}]")
    except Exception as e:
        logging.error(f"❌ Échec de l'appel Gemini dans auto_tpsl_engine : {e}")
        return {"updated_count": 0, "logs": [f"❌ Échec de l'analyse IA : {e}"]}

    if not raw_response:
        return {"updated_count": 0, "logs": ["❌ Échec de réponse des modèles Gemini."]}

    try:
        text = raw_response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        recs = data.get("recommendations", [])
    except Exception as e:
        logging.error(f"❌ Erreur de parsing JSON ({used_model}) : {e}")
        return {"updated_count": 0, "logs": [f"Erreur parsing JSON ({used_model}) : {e}"]}

    updated_count = 0
    logs = [f"🤖 Modèle utilisé : `{used_model}`"]

    for r in recs:
        p_id = r.get("id")
        tp1 = r.get("tp1_price")
        tp2 = r.get("tp2_price")
        sl = r.get("sl_price")
        justif = r.get("justification", "")

        if p_id and tp1 and sl:
            update_trade_levels(p_id, tp1_price=tp1, tp2_price=tp2, sl_price=sl)
            updated_count += 1
            log_line = f"✅ Position ID {p_id} ({r.get('symbol')}) réalignée -> TP1: {tp1}€ | SL: {sl}€ ({justif})"
            logs.append(log_line)
            logging.info(log_line)

    return {"updated_count": updated_count, "logs": logs}


if __name__ == "__main__":
    run_auto_tpsl_cycle(manual_trigger=False)