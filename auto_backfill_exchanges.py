import datetime
import logging
import os
from dotenv import load_dotenv
from db_manager import init_db, get_open_positions, add_recommendation
from exchange_connectors import get_all_trades_history
from daily_digest_engine import init_finary_session

load_dotenv()
init_db()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

IGNORED_ASSETS = {"BTC", "XBT", "ETH", "USDT", "USDC", "EUR", "USD", "WBTC", "STETH"}
CORE_ASSETS = {
    "SOL", "ADA", "LINK", "AVAX", "ATOM", "UNI", "NEAR", "DOT", 
    "MATIC", "POL", "XRP", "LTC", "BCH", "ETC", "ALGO", "FIL", 
    "TRX", "APT", "SUI", "RENDER", "RNDR", "TAO", "FET", "INJ"
}

def get_exchange_holdings_and_cost():
    trades = get_all_trades_history()
    asset_tracking = {}

    if not isinstance(trades, list):
        return []

    for t in trades:
        exchange = str(t.get("exchange", "UNKNOWN")).upper()
        raw_pair = str(t.get("pair") or t.get("symbol", "")).upper()
        symbol = raw_pair.replace("-", "").replace("/", "")

        # Nettoyage des suffixes et préfixes
        for suffix in ["ZEUR", "ZUSD", "USDT", "USDC", "EUR", "USD"]:
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                symbol = symbol[:-len(suffix)]
        if symbol.startswith("XX") and len(symbol) == 5:
            symbol = symbol[2:]
        elif symbol.startswith("X") and len(symbol) == 4 and symbol not in ["XRP", "XLM"]:
            symbol = symbol[1:]

        # On ignore les actifs majeurs et les actifs Core
        if symbol in IGNORED_ASSETS or symbol in CORE_ASSETS:
            continue

        side = str(t.get("type") or t.get("side", "buy")).lower()
        price = float(t.get("price", 0.0) or 0.0)
        qty = float(t.get("vol") or t.get("amount") or t.get("qty") or t.get("volume", 0.0) or 0.0)
        cost = float(t.get("cost", 0.0) or (price * qty) or 0.0)

        if price <= 0 or qty <= 0:
            continue

        key = (symbol, exchange)
        if key not in asset_tracking:
            asset_tracking[key] = {"total_qty": 0.0, "total_cost": 0.0, "last_price": price}

        if "buy" in side or "bid" in side or "in" in side:
            asset_tracking[key]["total_qty"] += qty
            asset_tracking[key]["total_cost"] += cost
            asset_tracking[key]["last_price"] = price
        elif "sell" in side or "ask" in side or "out" in side:
            curr = asset_tracking[key]
            if curr["total_qty"] > 0:
                avg_pru = curr["total_cost"] / curr["total_qty"]
                curr["total_qty"] = max(0.0, curr["total_qty"] - qty)
                curr["total_cost"] = max(0.0, curr["total_cost"] - (qty * avg_pru))

    active_positions = []
    for (symbol, exchange), data in asset_tracking.items():
        if data["total_qty"] > 0.0001:
            pru = data["total_cost"] / data["total_qty"] if data["total_qty"] > 0 else data["last_price"]
            amount_eur = data["total_cost"] if data["total_cost"] > 0 else (data["total_qty"] * data["last_price"])
            active_positions.append({
                "symbol": symbol,
                "exchange": exchange,
                "price_entry": pru,
                "amount_eur": amount_eur
            })

    return active_positions

def run_auto_backfill():
    logging.info("🔍 Démarrage de la rétro-ingénierie (filtrage strict de la poche spéculative)...")
    
    existing_open = get_open_positions()
    existing_symbols = {f"{row[1].upper()}_{row[2].upper()}" for row in existing_open}

    detected_positions = get_exchange_holdings_and_cost()

    backfilled_success = []
    skipped_existing = []
    manual_required = []

    for pos in detected_positions:
        key = f"{pos['symbol']}_{pos['exchange']}"
        if key in existing_symbols:
            skipped_existing.append(pos)
            continue

        if pos["price_entry"] > 0 and pos["amount_eur"] > 1.0:
            tp1 = pos["price_entry"] * 2.0
            tp2 = pos["price_entry"] * 4.0
            sl = pos["price_entry"] * 0.55

            rec_id = add_recommendation(
                symbol=pos["symbol"],
                exchange=pos["exchange"],
                price=pos["price_entry"],
                amount_eur=pos["amount_eur"],
                tp1_price=tp1,
                tp2_price=tp2,
                sl_price=sl,
                sl_type="invalidation"
            )
            backfilled_success.append((pos, rec_id))
        else:
            manual_required.append(pos)

    # --- RAPPORT FINAL ---
    print("\n" + "="*50)
    print("📊 BILAN DE LA RÉTRO-INGÉNIERIE (Poche Spéculative Pure)")
    print("="*50)
    
    print(f"\n✅ RÉTRO-INGÉNIERIE RÉUSSIE (Importés en BDD) : {len(backfilled_success)}")
    for p, rid in backfilled_success:
        print(f"   • {p['symbol']} ({p['exchange']}) : ~{p['amount_eur']:.2f} € | PRU : {p['price_entry']:.4f} € (ID BDD: {rid})")

    print(f"\n⏭️ DÉJÀ SUIVIS EN BDD (Ignorés) : {len(skipped_existing)}")
    
    print(f"\n⚠️ NÉCESSITANT UNE SAISIE MANUELLE : {len(manual_required)}")
    for p in manual_required:
        print(f"   • {p['symbol']} ({p['exchange']}) : ~{p['amount_eur']:.2f} €")
    
    print("="*50 + "\n")

if __name__ == "__main__":
    run_auto_backfill()
