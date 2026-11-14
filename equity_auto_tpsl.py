#!/usr/bin/env python3
"""
Moteur d'Auto-Ajustement TP/SL Actions par IA (equity_auto_tpsl.py).
Miroir de auto_tpsl_engine.py :
- Recalibre automatiquement les paliers TP1 et Stop-Loss des positions marquées auto_tp_sl = 1
- Applique la règle du Ratchet (le Stop-Loss ne baisse jamais)
"""

import json
import re
import logging
from equity_db_manager import (
    init_equity_db,
    get_open_speculative_equities,
    update_equity_levels,
)
from equity_market_engine import (
    fetch_bulk_equity_metrics,
    get_equity_macro_compass,
)
from gemini_manager import call_gemini_with_fallback

try:
    from prompt_loader import get_system_prompt
except ImportError:
    def get_system_prompt(name):
        return ""

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def run_equity_auto_tpsl_cycle(manual_trigger=False):
    init_equity_db()
    positions = get_open_speculative_equities()
    auto_positions = [p for p in positions if int(p[12] or 0) == 1]

    logs = []
    updated_count = 0

    if not auto_positions:
        logs.append("ℹ️ Aucune action spéculative en mode 'Auto IA'.")
        return {"updated_count": 0, "logs": logs}

    tickers = [p[2] for p in auto_positions if p[2]]
    metrics_map = fetch_bulk_equity_metrics(tickers)
    compass = get_equity_macro_compass()

    payload = []
    for row in auto_positions:
        t_id, sym, ytick, acc, isin, entry_p, qty, amt_eur, tp1, tp2, sl, step, auto_flag, cw8_e, hwm, taken_at = row
        m = metrics_map.get(ytick, {})
        live_p = m.get("price", 0.0) or entry_p
        pnl_pct = ((live_p - entry_p) / entry_p * 100.0) if entry_p > 0 else 0.0
        payload.append({
            "id": t_id,
            "symbol": sym,
            "ticker": ytick,
            "pru_eur": round(entry_p, 4),
            "live_price_eur": round(live_p, 4),
            "high_water_mark_eur": round(max(float(hwm or 0.0), live_p), 4),
            "pnl_pct": round(pnl_pct, 2),
            "current_tp1": round(tp1, 4),
            "current_sl": round(sl, 4),
            "rsi_1d": m.get("rsi_1d", 50.0),
            "dist_sma200_pct": m.get("dist_sma200_pct", 0.0),
        })

    sys_inst = get_system_prompt("equity_auto_tpsl") or (
        "Tu es un Risk Manager Actions. Recalibre TP1 et SL pour chaque position. "
        "RÈGLE RATCHET : new_sl >= current_sl. Si pnl_pct >= 25.0, new_sl >= pru_eur * 1.01. "
        "Réponds UNIQUEMENT en JSON valide : {\"adjustments\": [{\"id\": 1, \"new_tp1\": 10.5, \"new_sl\": 8.2, \"reason\": \"...\"}]}"
    )

    prompt = (
        f"CONTEXTE MACRO BOURSE : Régime={compass['regime']}, VIX={compass['vix']}, CW8 vs SMA200={compass['cw8_dist_sma200_pct']}%\n"
        f"POSITIONS À RECALIBRER :\n{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )

    try:
        raw_resp, used_model = call_gemini_with_fallback(prompt_text=prompt, system_instruction=sys_inst)
        match = re.search(r"\{[\s\S]*\}", raw_resp)
        if not match:
            logs.append("⚠️ Réponse IA non structurée en JSON.")
            return {"updated_count": 0, "logs": logs}

        data = json.loads(match.group(0))
        adjustments = data.get("adjustments", [])
        pos_by_id = {p[0]: p for p in auto_positions}

        for adj in adjustments:
            pid = int(adj.get("id", 0))
            if pid not in pos_by_id:
                continue
            orig = pos_by_id[pid]
            orig_tp1 = float(orig[8] or 0.0)
            orig_tp2 = float(orig[9] or 0.0)
            orig_sl = float(orig[10] or 0.0)
            sym = orig[1]

            proposed_tp1 = float(adj.get("new_tp1", orig_tp1))
            proposed_sl = float(adj.get("new_sl", orig_sl))
            reason = adj.get("reason", "Ajustement technique IA")

            final_sl = max(orig_sl, proposed_sl)
            final_tp1 = proposed_tp1 if proposed_tp1 > 0 else orig_tp1

            if abs(final_sl - orig_sl) > 0.01 or abs(final_tp1 - orig_tp1) > 0.01:
                update_equity_levels(pid, final_tp1, orig_tp2, final_sl)
                updated_count += 1
                logs.append(f"✅ **{sym}** (ID {pid}) : SL {orig_sl:.2f}€ → **{final_sl:.2f}€** | TP1 {orig_tp1:.2f}€ → **{final_tp1:.2f}€** (*{reason}*)")

        if updated_count == 0:
            logs.append(f"🟢 Paliers déjà optimaux selon `{used_model}`.")
    except Exception as e:
        logs.append(f"❌ Erreur cycle Auto TP/SL : {e}")

    return {"updated_count": updated_count, "logs": logs}


if __name__ == "__main__":
    res = run_equity_auto_tpsl_cycle(manual_trigger=True)
    for l in res["logs"]:
        print(l)
