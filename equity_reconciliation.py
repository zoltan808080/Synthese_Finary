#!/usr/bin/env python3
"""
Moteur de Réconciliation & Audit Finary vs BDD (equity_reconciliation.py).
Miroir de reconciliation_engine.py :
- Détecte les NEW_SPECULATIVE_DETECTED (nouvel achat sur CTO absent de speculative_equities)
- Détecte les GHOST_POSITION (ligne vendue sur Finary mais toujours OPEN en BDD)
- Détecte les VOLUME_MISMATCH (renfort ou allègement partiel >= 15% sur Finary vs BDD)
"""

from equity_db_manager import (
    get_all_classifications,
    get_open_speculative_equities,
)


def audit_equity_database_vs_finary(finary_lines, yahoo_metrics_map):
    """Compare l'état Finary live avec la table speculative_equities."""
    discrepancies = []
    classifications = {c["asset_key"]: c for c in get_all_classifications()}
    open_spec = get_open_speculative_equities()

    db_map = {}
    for row in open_spec:
        t_id, sym, ytick, acc, isin, entry_p, qty, amt_eur, tp1, tp2, sl, step, auto_flag, cw8_e, hwm, taken_at = row
        db_map[(sym, acc)] = {
            "id": t_id,
            "symbol": sym,
            "yahoo_ticker": ytick,
            "account": acc,
            "isin": isin,
            "price_entry": entry_p,
            "quantity": qty,
            "amount_eur": amt_eur,
            "tp1": tp1,
            "sl": sl,
        }

    finary_map = {}
    for line in finary_lines:
        ak = line["asset_key"]
        meta = classifications.get(ak, {"bucket": "MT_CORE_ETF", "yahoo_ticker": "", "is_excluded": False})
        if meta.get("is_excluded"):
            continue
        finary_map[(line["name"], line["account"])] = {
            **line,
            "bucket": meta["bucket"],
            "yahoo_ticker": meta["yahoo_ticker"],
        }

    # 1. Nouveaux achats spéculatifs ou écarts de volume
    for (sym, acc), f_item in finary_map.items():
        val = float(f_item.get("value_eur", 0.0))
        pru = float(f_item.get("pru_eur", 0.0))
        qty = float(f_item.get("quantity", 0.0))
        ytick = f_item.get("yahoo_ticker", "")
        isin = f_item.get("isin", "")
        bucket = f_item.get("bucket", "MT_CORE_ETF")

        if (sym, acc) not in db_map:
            if bucket == "CT_SPECULATIVE" and val >= 50.0:
                entry_est = pru if pru > 0 else (val / qty if qty > 0 else val)
                discrepancies.append({
                    "type": "NEW_SPECULATIVE_DETECTED",
                    "id": None,
                    "symbol": sym,
                    "account": acc,
                    "isin": isin,
                    "yahoo_ticker": ytick,
                    "quantity": qty,
                    "price_entry": round(entry_est, 4),
                    "amount_eur": round(qty * entry_est, 2) if pru > 0 else round(val, 2),
                    "current_val": round(val, 2),
                    "desc": f"Nouvelle ligne détectée sur {acc} ({val:,.2f} €, PRU {entry_est:.2f} €) absente du suivi Play.",
                })
        else:
            db_item = db_map[(sym, acc)]
            db_qty = float(db_item["quantity"])
            if db_qty > 0 and qty > 0:
                qty_diff_pct = abs(qty - db_qty) / db_qty * 100.0
                if qty_diff_pct >= 15.0:
                    new_pru = pru if pru > 0 else db_item["price_entry"]
                    new_amt = round(qty * new_pru, 2)
                    discrepancies.append({
                        "type": "VOLUME_MISMATCH",
                        "id": db_item["id"],
                        "symbol": sym,
                        "account": acc,
                        "db_qty": db_qty,
                        "finary_qty": qty,
                        "adjusted_amount_eur": new_amt,
                        "adjusted_qty": qty,
                        "adjusted_pru": round(new_pru, 4),
                        "desc": f"Écart de quantité sur {acc} : BDD = {db_qty:.2f} titres vs Finary = {qty:.2f} titres ({qty_diff_pct:.1f}%).",
                    })

    # 2. Lignes fantômes (vendues sur Finary mais toujours OPEN en BDD)
    for (sym, acc), db_item in db_map.items():
        if (sym, acc) not in finary_map or finary_map[(sym, acc)].get("value_eur", 0.0) < 5.0:
            ym = yahoo_metrics_map.get(db_item["yahoo_ticker"], {})
            exit_p = ym.get("price", 0.0) or db_item["price_entry"]
            discrepancies.append({
                "type": "GHOST_POSITION",
                "id": db_item["id"],
                "symbol": sym,
                "account": acc,
                "exit_price": exit_p,
                "desc": f"Position vendue ou absente de Finary sur {acc}, mais toujours ouverte en BDD (ID {db_item['id']}).",
            })

    return discrepancies
