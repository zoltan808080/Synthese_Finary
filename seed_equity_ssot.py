#!/usr/bin/env python3
"""
Initialise equity_wealth.db à partir de equities_raw_catalog.json :
- Corrige la classification PERCO (LBPAM Profil Dynamique Plus W -> LT_SALARIAL)
- Associe automatiquement les tickers Yahoo Finance exacts (.PA, .DE, .L, US) aux actions et ETF
- Initialise les positions actives de la Poche Spéculative dans speculative_equities avec PRU Finary
"""

import json
import os
from equity_db_manager import (
    init_equity_db,
    upsert_asset_classification,
    get_open_speculative_equities,
    add_speculative_equity,
)

ISIN_TO_YAHOO = {
    # --- Actions CTO LCL (ORD DE SENANT) ---
    "FR0013357621": "WAVE.PA",   # Wavestone
    "FR0010425595": "ALCLS.PA",  # Cellectis SA
    "NL0000235190": "AIR.PA",    # Airbus SE
    "AN8068571086": "SLB",       # Schlumberger Ltd
    "FR0000120578": "SAN.PA",    # Sanofi SA
    "FR0000133308": "ORA.PA",    # Orange
    "FR0004056851": "VLA.PA",    # Valneva SE
    "FR001401A9Z0": "ALSEN.PA",  # Sensorion
    "FR0010411983": "SCR.PA",    # Scor SE
    "FR0004163111": "GNFT.PA",   # Genfit SA
    "FR0011341205": "NANO.PA",   # Nanobiotix
    "US0079031078": "AMD",       # Advanced Micro Devices
    "US67066G1040": "NVDA",      # NVIDIA Corp
    "FR0000053324": "CDA.PA",    # Compagnie des Alpes
    "FR0000120628": "CS.PA",     # AXA
    "FR0000125486": "DG.PA",     # Vinci
    "FR0010282822": "VU.PA",     # Vusion
    "FR0012757854": "SPIE.PA",   # SPIE SA
    "FR0000120560": "QDT.PA",    # Quadient SA

    # --- ETFs & ETCs (CTO LCL & BoursoBank) ---
    "FR0010315770": "CW8.PA",    # Amundi MSCI World II
    "LU0496786574": "500.PA",    # Amundi S&P 500 II
    "LU1681038672": "RS2K.PA",   # Amundi Russell 2000
    "LU1598689153": "MMS.PA",    # Amundi MSCI EMU Small Cap
    "LU1681045370": "AEEM.PA",   # Amundi MSCI Emerging Markets
    "LU1681044480": "AASI.PA",   # Amundi MSCI EM Asia
    "LU2233156749": "JARI.PA",   # Amundi MSCI Japan SRI
    "LU1900066462": "CEU2.PA",   # Lyxor MSCI Eastern Europe ex Russia
    "LU1900068328": "AEJ.PA",    # Lyxor MSCI AC Asia Pacific ex Japan
    "IE000M7V94E1": "NUKL.DE",   # VanEck Uranium & Nuclear
    "IE00B579F325": "8PSG.DE",   # Invesco Physical Gold ETC
    "FR0013416716": "GOLD.PA",   # Amundi Physical Gold ETC
    "IE00B44CND37": "TRSY.L",    # SPDR US Treasury Bond
    "CH0454664001": "21BC.DE",   # 21Shares Bitcoin ETP
}


def run_seed():
    init_equity_db()
    if not os.path.exists("equities_raw_catalog.json"):
        print("❌ Fichier equities_raw_catalog.json introuvable.")
        return

    with open("equities_raw_catalog.json", "r", encoding="utf-8") as f:
        catalog = json.load(f)

    existing_spec = {(r[1], r[3]) for r in get_open_speculative_equities()}
    counts = {"LT_SALARIAL": 0, "MT_CORE_ETF": 0, "CT_SPECULATIVE": 0}
    seeded_spec_count = 0

    for item in catalog:
        acc = item["account"]
        name = item["name"]
        isin = item.get("isin", "N/A")
        val = float(item.get("current_value_eur", 0.0))
        pru = float(item.get("pru_eur", 0.0))
        qty = float(item.get("quantity", 0.0))

        asset_key = f"{isin}_{acc}" if isin and isin != "N/A" else f"{name}_{acc}"
        yahoo_ticker = ISIN_TO_YAHOO.get(isin, "")

        # Correction fine des règles de poche
        upper_all = f"{acc} {name}".upper()
        if "LBPAM" in upper_all or "RETRAITE" in upper_all or "PERCO" in upper_all or "PERO" in upper_all or "GROUPE EDF" in upper_all:
            bucket = "LT_SALARIAL"
        elif isin == "CH0454664001":  # 21Shares Bitcoin ETP
            bucket = "MT_CORE_ETF"
        elif "GREGOIRESENANT" in upper_all and ("CASH" in upper_all or "KORATRADES" in upper_all or "AROKCRYPTO" in upper_all):
            bucket = "CT_SPECULATIVE"
        else:
            bucket = item.get("bucket", "MT_CORE_ETF")

        upsert_asset_classification(
            asset_key=asset_key,
            name=name,
            account=acc,
            isin=isin if isin != "N/A" else "",
            yahoo_ticker=yahoo_ticker,
            bucket=bucket,
        )
        counts[bucket] = counts.get(bucket, 0) + 1

        # Si classé en Poche Spéculative avec un ticker boursier actif, onboarding auto dans speculative_equities
        if bucket == "CT_SPECULATIVE" and yahoo_ticker and val >= 50.0:
            if (name, acc) not in existing_spec:
                entry_p = pru if pru > 0 else (val / qty if qty > 0 else val)
                q_val = qty if qty > 0 else (val / entry_p if entry_p > 0 else 1.0)
                invested_eur = round(q_val * entry_p, 2) if pru > 0 else round(val, 2)
                add_speculative_equity(
                    symbol=name,
                    account=acc,
                    price_entry=round(entry_p, 4),
                    quantity=round(q_val, 4),
                    amount_eur=invested_eur,
                    yahoo_ticker=yahoo_ticker,
                    isin=isin if isin != "N/A" else "",
                )
                seeded_spec_count += 1

    print("✅ Synchronisation SSOT terminée avec succès !")
    print(f"   • Socle Salarial & Retraite (LT_SALARIAL) : {counts['LT_SALARIAL']} lignes")
    print(f"   • Cœur Indiciel & Fonds (MT_CORE_ETF)     : {counts['MT_CORE_ETF']} lignes")
    print(f"   • Poche Spéculative (CT_SPECULATIVE)      : {counts['CT_SPECULATIVE']} lignes ({seeded_spec_count} actions initialisées avec PRU & TP/SL)")


if __name__ == "__main__":
    run_seed()
