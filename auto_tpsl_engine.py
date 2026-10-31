#!/usr/bin/env python3
"""Moteur d'auto-ajustement des paliers TP/SL par l'IA v3.2 (Architecture SSOT & Centralisation Gemini)."""

import json
import logging
import os
import re
from dotenv import load_dotenv

from db_manager import get_auto_tp_sl_status, get_open_positions, update_trade_levels
from exchange_connectors import get_all_exchange_crypto_balances
from gemini_manager import call_gemini_with_fallback
from prompt_loader import get_system_prompt

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


def run_auto_tpsl_cycle(manual_trigger=False):
  """Exécute un cycle de réévaluation et d'ajustement automatique des paliers TP/SL via Gemini."""
  trigger_type = "MANUEL (Streamlit)" if manual_trigger else "AUTOMATIQUE (Cron 8h)"
  logging.info(f"🤖 Démarrage du cycle Auto TP/SL — Mode {trigger_type} v3.2...")

  open_pos = get_open_positions()
  auto_map = get_auto_tp_sl_status()

  target_positions = [p for p in open_pos if auto_map.get(p[0], False)]

  if not target_positions:
    logging.info("ℹ️ Aucune position active n'est configurée en mode Auto IA.")
    return {"updated_count": 0, "logs": ["Aucune position en mode Auto IA."]}

  symbols = [p[1] for p in target_positions]

  # Extraction des indicateurs RSI & Microstructure
  rsi_context = ""
  if RSI_AVAILABLE:
    try:
      df_rsi = build_rsi_heatmap_dataframe(symbols)
      if not df_rsi.empty:
        rsi_context = f"\nINDICATEURS RSI EN DIRECT :\n{df_rsi.to_string(index=False)}"
    except Exception as e:
      logging.warning(f"⚠️ Erreur chargement RSI : {e}")

  foi_context = ""
  if FOI_AVAILABLE:
    try:
      foi_data = get_funding_oi_summary(symbols)
      if foi_data and "pairs" in foi_data:
        foi_context = f"\nMICROSTRUCTURE & FUNDING RATES :\n{json.dumps(foi_data['pairs'], indent=2)}"
    except Exception as e:
      logging.warning(f"⚠️ Erreur chargement Funding/OI : {e}")

  # Structuration des données des positions concernées
  positions_payload = []
  for p in target_positions:
    p_id, sym, exch, entry, amt, tp1, tp2, sl, sl_type, step = p[:10]
    positions_payload.append({
        "id": p_id,
        "symbol": sym,
        "exchange": exch,
        "price_entry": entry,
        "amount_eur": amt,
        "current_tp1": tp1,
        "current_tp2": tp2,
        "current_sl": sl,
        "step": step,
    })

  # Chargement du prompt SSOT
  system_prompt = get_system_prompt("auto_tpsl")

  user_payload = f"""SITUATION DES POSITIONS À ÉVALUER :
{json.dumps(positions_payload, indent=2, ensure_ascii=False)}

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