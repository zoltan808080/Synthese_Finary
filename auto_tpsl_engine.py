import os
import json
import requests
from dotenv import load_dotenv
from google import genai

from db_manager import get_open_positions, update_trade_levels, get_auto_tp_sl_status
from exchange_connectors import get_all_exchange_crypto_balances
from dashboard import build_market_tech_micro_context, fetch_live_price, call_gemini_with_fallback

load_dotenv()

def send_telegram_notify(text: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if not bot_token or not user_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": user_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception:
        pass

def run_auto_tpsl_cycle(manual_trigger: bool = False):
    """Exécute l'analyse et la mise à jour automatique des TP/SL pour les positions autorisées."""
    auto_flags = get_auto_tp_sl_status()
    all_positions = get_open_positions()
    
    # Filtrer uniquement les positions avec auto_tp_sl == True
    target_positions = [p for p in all_positions if auto_flags.get(p[0], False)]
    
    if not target_positions:
        return {"updated_count": 0, "logs": ["Aucune position en mode Auto IA."]}
    
    balances = get_all_exchange_crypto_balances()
    tech_context = build_market_tech_micro_context(open_pos=target_positions, balances=balances)
    
    pos_data = []
    for p in target_positions:
        p_id, sym, exch, entry, amt, tp1, tp2, sl = p[0], p[1], p[2], float(p[3] or 0), float(p[4] or 0), float(p[5] or 0), float(p[6] or 0), float(p[7] or 0)
        lp = fetch_live_price(sym, exch) or entry
        pnl_pct = ((lp - entry) / entry) * 100.0 if entry > 0 else 0.0
        
        pos_data.append({
            "id": p_id,
            "symbole": sym,
            "exchange": exch,
            "prix_entree": round(entry, 4),
            "prix_actuel": round(lp, 4),
            "pnl_pct": round(pnl_pct, 2),
            "tp1_actuel": round(tp1, 4),
            "tp2_actuel": round(tp2, 4),
            "sl_actuel": round(sl, 4)
        })

    prompt = f"""Tu es un Risk Manager institutionnel. Ajuste les paliers TP1, TP2 et Stop-Loss (SL) pour les positions ci-dessous.

POSITIONS À ANALYSER :
{json.dumps(pos_data, indent=2, ensure_ascii=False)}

CONTEXTE TECHNIQUE & MICROSTRUCTURE :
{tech_context}

REGLES STRICTES DE PROTECTION :
1. **RÈGLE DU TRAILING STOP** : Interdiction ABSOLUE de BAISSER un Stop-Loss (`new_sl` < `sl_actuel`). Le SL peut uniquement REMONTER (Sécurisation / Break-Even) ou RESTER INCHANGÉ.
2. Si TP1 est atteint ou proche, remonte le SL sur le prix d'entrée (Break-Even) ou sous le dernier creux relatif.
3. Ajuste TP1 et TP2 à la hausse ou à la baisse selon le RSI et la surchauffe du Funding Rate.
4. Réponds STRICTEMENT et UNIQUEMENT avec un objet JSON valide suivant ce schéma exact :
{{
  "updates": [
    {{
      "id": 12,
      "symbole": "SUI",
      "new_sl": 1.58,
      "new_tp1": 3.10,
      "new_tp2": 4.50,
      "reason": "Mise en place Trailing Stop suite à la hausse du RSI 1h"
    }}
  ]
}}"""

    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    
    try:
        response_text, model_used = call_gemini_with_fallback(
            client=client,
            prompt_text=prompt,
            system_instruction="Tu es un moteur d'exécution algorithmique qui répond exclusivement en JSON valide.",
            models=["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.7-flash"]
        )
        
        clean_json = response_text.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean_json)
        
        updates = result.get("updates", [])
        modified_count = 0
        summary_logs = []
        
        for up in updates:
            p_id = up["id"]
            new_sl = float(up["new_sl"])
            new_tp1 = float(up["new_tp1"])
            new_tp2 = float(up.get("new_tp2", 0))
            reason = up.get("reason", "Ajustement IA")
            
            # Recherche des anciennes valeurs
            orig = next((p for p in pos_data if p["id"] == p_id), None)
            if not orig:
                continue
            
            old_sl = orig["sl_actuel"]
            old_tp1 = orig["tp1_actuel"]
            old_tp2 = orig["tp2_actuel"]
            
            # GARDE-FOU CODE : Rejet si tentation de descendre le SL
            if old_sl > 0 and new_sl < old_sl:
                summary_logs.append(f"⚠️ {orig['symbole']} (ID {p_id}) : Tentative de baisser le SL bloquée par le garde-fou.")
                continue
            
            # Vérification s'il y a un réel changement
            if new_sl != old_sl or new_tp1 != old_tp1 or new_tp2 != old_tp2:
                update_trade_levels(p_id, tp1_price=new_tp1, tp2_price=new_tp2, sl_price=new_sl)
                modified_count += 1
                
                log_msg = f"🤖 *AJUSTEMENT AUTOMATIQUE IA*\n• **{orig['symbole']}** ({orig['exchange']} - ID {p_id})\n"
                if new_sl != old_sl:
                    log_msg += f"• **SL** : {old_sl:.4f} € ➔ *{new_sl:.4f} €*\n"
                if new_tp1 != old_tp1:
                    log_msg += f"• **TP1** : {old_tp1:.4f} € ➔ *{new_tp1:.4f} €*\n"
                log_msg += f"• **Motif** : _{reason}_"
                
                send_telegram_notify(log_msg)
                summary_logs.append(f"✅ {orig['symbole']} : SL {new_sl:.4f} € | TP1 {new_tp1:.4f} €")

        return {"updated_count": modified_count, "logs": summary_logs}

    except Exception as e:
        return {"updated_count": 0, "logs": [f"Erreur d'exécution IA : {e}"]}
