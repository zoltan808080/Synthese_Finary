# CADRE ET RÈGLES MÉTIER IMMUABLES DU PORTEFEUILLE CRYPTO

## 1. Philosophie & Stratégie globale
- **Nature du portefeuille** : Trading et investissement Spot à faible levier / levier 1. Pas de contrats Futures à fort levier ni de Scalping haute fréquence.
- **Gestion des Poches** :
  - **Majeures** : BTC, ETH (Stockage de valeur long terme).
  - **Poche CORE** : Actifs de conviction long terme conservés en Spot sans Stop-Loss spéculatif.
  - **Poche Spéculative (Moonbag)** : Positions tactiques à haute volatilité suivies 24/7 en BDD (`crypto_trading.db`).

## 2. Invalidation & Stop-Loss (Spécificité Moonbag)
- **Nature des Stop-Loss** : Ce sont des **seuils d'invalidation structurelle larges** (typiquement -30% à -40% sous le prix d'entrée) surveillés en BDD par `crypto_monitor.py`.
- **Règle absolue pour l'Analyse de Risque** : Ne JAMAIS qualifier un Stop-Loss à -35% d'anomalie, d'erreur ou d'imprudence. Ces stops larges sont volontaires pour encaisser la volatilité des alts et les mèches baissières (*wicks*).
- **Vraies anomalies à signaler dans les audits** :
  - Cours actuel inférieur à un SL Break-Even remonté en BDD sans clôture enregistrée.
  - Absence totale de Stop-Loss en BDD (`sl_price = 0`).
  - Sur-exposition critique d'une seule ligne (> 25% du capital spéculatif).

## 3. Stratégie de Prise de Profit (Take-Profit)
- **TP1 (+100% / x2)** : Vente systématique de 50% de la position pour récupérer le capital initial (passage en **Free Ride**). Le Stop-Loss est remonté au prix d'entrée (Break-Even).
- **TP2 (+300% / x4)** : Vente de 25% supplémentaires pour verrouiller des plus-values nettes. Les 25% restants deviennent un Moonbag pur.

## 4. Gestion des Liquidités & Règle Cash-Neutral
- **Anti-Double Comptage** : Les liquidités mobilisables sont STRICTEMENT constituées du Fiat (EUR/USD) + Stablecoins (USDT/USDC).
- Ne ré-additionnez JAMAIS les stablecoins au cash disponible lors d'un calcul de solde après arbitrage.
