#!/usr/bin/env python3
"""
Moteur de Découverte Dynamique des Marchés Actions & ETF (equity_discovery_engine.py).
Miroir de market_discovery_engine.py :
- Sépare strictement le Vivier Spéculatif ('Actions Chocs') et le Vivier CORE (ETF & Blue Chips MT/LT)
- Purge automatiquement les tickers déjà présents dans asset_classification (anti-doublon BDD).
"""

import time
from equity_db_manager import get_all_classifications
from equity_market_engine import fetch_bulk_equity_metrics

SPECULATIVE_UNIVERSE = {
    "SOI.PA": "Soitec (Semi-conducteurs FR)",
    "STMPA.PA": "STMicroelectronics (Semi-conducteurs)",
    "DSY.PA": "Dassault Systèmes (Logiciel/IA)",
    "CAP.PA": "Capgemini (Services Numériques)",
    "TEP.PA": "Teleperformance (Rebond Value)",
    "WLN.PA": "Worldline (Fintech / Retournement)",
    "OVH.PA": "OVHcloud (Cloud Souverain)",
    "BVI.PA": "Bureau Veritas (Qualité/Inspection)",
    "PLTR": "Palantir Technologies (IA/Défense US)",
    "CRWD": "CrowdStrike (Cybersécurité US)",
    "PANW": "Palo Alto Networks (Cybersécurité US)",
    "ARM": "Arm Holdings (Semi-conducteurs IA)",
    "SMCI": "Super Micro Computer (Serveurs IA)",
    "TSLA": "Tesla Inc (Volatilité / Momentum)",
    "UBER": "Uber Technologies (Croissance US)",
}

CORE_ETF_UNIVERSE = {
    "WPEA.PA": "iShares MSCI World Swap",
    "ESE.PA": "BNP Paribas Easy S&P 500",
    "PUST.PA": "Amundi Nasdaq-100 UCITS ETF",
    "MEUD.PA": "Amundi Stoxx Europe 600",
    "PAEEM.PA": "Amundi MSCI Emerging Markets",
    "LQQ.PA": "Amundi Nasdaq-100 Daily 2x (Tactique)",
    "AI.PA": "Air Liquide (Blue Chip Croissance LT)",
    "MC.PA": "LVMH (Blue Chip Luxe LT)",
    "SU.PA": "Schneider Electric (Électrification LT)",
    "SAF.PA": "Safran (Aéronautique/Défense LT)",
    "ASML.AS": "ASML Holding (Monopole Lithographie)",
    "MSFT": "Microsoft Corp (Cloud & IA CORE)",
}

_CACHE = {"ts": 0.0, "data": None}
CACHE_TTL_SEC = 900  # 15 minutes


def get_dynamic_equity_candidates(top_spec=8, top_core=8, force_refresh=False):
    now = time.time()
    if not force_refresh and _CACHE["data"] and (now - _CACHE["ts"] < CACHE_TTL_SEC):
        return _CACHE["data"]

    owned_tickers = {c["yahoo_ticker"].upper() for c in get_all_classifications() if c["yahoo_ticker"]}

    spec_pool = {t: name for t, name in SPECULATIVE_UNIVERSE.items() if t.upper() not in owned_tickers}
    core_pool = {t: name for t, name in CORE_ETF_UNIVERSE.items() if t.upper() not in owned_tickers}

    all_t = list(spec_pool.keys()) + list(core_pool.keys()) + ["CW8.PA"]
    metrics = fetch_bulk_equity_metrics(all_t)
    cw8_24h = metrics.get("CW8.PA", {}).get("change_24h_pct", 0.0)

    spec_candidates = []
    for t, label in spec_pool.items():
        m = metrics.get(t, {})
        price = m.get("price", 0.0)
        if price <= 0:
            continue
        rsi_1d = m.get("rsi_1d", 50.0)
        rsi_4h = m.get("rsi_4h", 50.0)
        chg_24h = m.get("change_24h_pct", 0.0)
        alpha_cw8 = round(chg_24h - cw8_24h, 2)

        if rsi_1d <= 35.0:
            opp_type = "🟢 Rebond Technique (RSI Survendu)"
            score = (40.0 - rsi_1d) * 2.0
        elif alpha_cw8 >= 2.0 and rsi_1d < 68.0:
            opp_type = "🚀 Momentum Alpha vs CW8"
            score = alpha_cw8 * 4.0
        else:
            opp_type = "⚖️ Surveillance Tactique"
            score = max(0.0, 50.0 - rsi_1d) * 0.5

        spec_candidates.append({
            "ticker": t,
            "name": label,
            "price": price,
            "change_24h_pct": chg_24h,
            "alpha_vs_cw8": alpha_cw8,
            "rsi_4h": rsi_4h,
            "rsi_1d": rsi_1d,
            "dist_sma200_pct": m.get("dist_sma200_pct", 0.0),
            "opportunity_type": opp_type,
            "score": round(score, 1),
        })

    core_candidates = []
    for t, label in core_pool.items():
        m = metrics.get(t, {})
        price = m.get("price", 0.0)
        if price <= 0:
            continue
        rsi_1d = m.get("rsi_1d", 50.0)
        dist_sma200 = m.get("dist_sma200_pct", 0.0)
        chg_24h = m.get("change_24h_pct", 0.0)

        if rsi_1d <= 40.0:
            opp_type = "💎 Point d'Entrée DCA / Repli"
            score = (45.0 - rsi_1d) * 2.0
        elif dist_sma200 > 0 and rsi_1d < 60.0:
            opp_type = "📈 Tendance Haussière Saine"
            score = 20.0 + dist_sma200
        else:
            opp_type = "⏳ Attendre Respiration"
            score = 5.0

        core_candidates.append({
            "ticker": t,
            "name": label,
            "price": price,
            "change_24h_pct": chg_24h,
            "rsi_1d": rsi_1d,
            "dist_sma200_pct": dist_sma200,
            "opportunity_type": opp_type,
            "score": round(score, 1),
        })

    res = {
        "speculative": sorted(spec_candidates, key=lambda x: x["score"], reverse=True)[:top_spec],
        "core": sorted(core_candidates, key=lambda x: x["score"], reverse=True)[:top_core],
    }
    _CACHE["ts"] = now
    _CACHE["data"] = res
    return res


if __name__ == "__main__":
    out = get_dynamic_equity_candidates(top_spec=5, top_core=5, force_refresh=True)
    print("Top Spéculatif :", [(x["ticker"], x["rsi_1d"], x["opportunity_type"]) for x in out["speculative"]])
    print("Top CORE       :", [(x["ticker"], x["rsi_1d"], x["opportunity_type"]) for x in out["core"]])
