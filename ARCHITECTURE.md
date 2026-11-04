# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v3.3.1`  
**Dernière mise à jour :** 4 novembre 2026  
**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)  
**Environnement :** VM Linux Debian (`finary-digest-server`)

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une plateforme autonome d'agrégation multi-exchanges, de réconciliation comptable temps réel, d'analyse quanti-microstructure, de pilotage spéculatif, d'arbitrage macro-patrimonial et de gestion dynamique des risques pilotée par Intelligence Artificielle (Gemini).

Le système élimine la dépendance aux agrégateurs tiers et applique la stratégie **Moonbag** avec une rigueur institutionnelle :

* **Prise de profit automatique (TP1 à x2 / TP2 à x4)** avec sécurisation en *Free Ride* (remboursement du capital initial).
* **Gestion stricte du risque** via Stop-Loss et ajustement automatique en *Break-Even* (BE) sans violer le verrou immuable du *Ratchet* (interdiction d'abaisser un Stop-Loss).
* **Isolation stricte des poches d'investissement** : Majeures (BTC/ETH - Cible 55%), CORE (conviction long terme - Cible 22%), Spéculatif (Moonbags actifs sous Stop-Loss - Cible 10% / Max 12%) et Cash (fiat & stablecoins - Cible 13%).
* **Moteur Macro SSOT (`macro_market_engine.py` v3.3.1) :** Centralisation et partage d'un socle d'indicateurs macroéconomiques uniques (*Dominance BTC (BTC.D)*, *Capitalisation TOTAL3*, *Poudre Sèche Stablecoins Globales*, *Fear & Greed Index*, *Circuit Breaker BTC*) exploités de manière synchronisée sur l'IHM Web, le Moteur TP/SL, le Moteur de Recommandation Quotidien (19h) et l'ensemble des Prompts Système de l'IA.
* **Ventilation Unifiée sans Doublons :** Le calcul des 4 poches s'appuie sur une fonction SSOT commune (`get_dashboard_portfolio_categories` / `calculate_unified_crypto_categories`). Les positions actives enregistrées en base SQLite constituent l'unique source de valorisation de la poche Spéculative, et sont strictly filtrées des soldes directs d'exchanges/portefeuilles pour éviter tout double-comptage.
* **Isolation Étanche CORE vs Spéculatif :** La transmission du contexte à l'IA filtre et exclut automatiquement tous les symboles présents en poche CORE ou Majeures (> 200 €) pour supprimer tout risque de recommandation redondante.
* **Dashboard Web Assaini & Multi-threaded (`dashboard.py` v3.3.1) :** Double bandeau supérieur regroupant la Macro-Marché SSOT et les Allocations du Portefeuille Réel au sein de l'onglet **Positions Actives**, avec parallélisation du fetch des prix (`ThreadPoolExecutor`) et isolation des prix par exchange (Kraken Spot vs OKX).

---

## 2. Cartographie des Fichiers & Composants

### 🎛️ Briques Principales (Production)

| Fichier / Dossier | Rôle & Responsabilité | Version |
| :--- | :--- | :--- |
| **`dashboard.py`** | **Interface utilisateur Web (Streamlit)** : Cockpit unifié en 5 onglets (Positions Actives Live, Microstructure & Dérivés, Centre d'Alertes/Réconciliation, Assistant IA Pro SSOT, Administration & Monitoring). Double bandeau macro/allocations, parallélisation multi-threading du fetch des prix et édition dynamique des prompts. | `v3.3.1` |
| **`macro_market_engine.py`** | **Source Unique de Vérité Macro (SSOT)** : Module centralisé de récupération du triplet Macro (BTC.D, TOTAL3, Stablecoins Cap) combiné au Fear & Greed Index et au Circuit Breaker BTC (-8.0% sur 24h) avec cache hybride Streamlit/CLI (300s) et multi-fallback API (OKX ➔ Kraken ➔ CoinGecko). | `v3.3.0` |
| **`gemini_manager.py`** | **Connecteur & Wrapper Centralisé Gemini** : Module unique gérant les exécutions d'IA avec bascule de secours (*fallback*), retries, timeouts configurables (ex: 45s) et traçabilité du modèle ayant répondu. | `v1.0.0` |
| **`config/gemini_config.json`** | **Configuration JSON des Modèles IA** : Fichier de stockage centralisé de la liste prioritaire des modèles Gemini (`gemini-3.8-flash`, `gemini-3.6-flash`, etc.) et du timeout réseau. | `v1.0.0` |
| **`prompt_loader.py`** | **Chargeur SSOT de Prompts System** : Module centralisé d'importation dynamique et de concaténation des instructions d'expertise depuis `prompts/` avec gestion automatique de backups `.bak`. | `v1.0.0` |
| **`prompts/`** | **Répertoire des Prompts SSOT (.md)** : Stockage modulaire des consignes système de référence (`advisor_speculative.md`, `risk_manager.md`, `project_rules.md`, `auto_tpsl.md`, `macro_arbitrage.md`). | `v3.3.1` |
| **`auto_tpsl_engine.py`** | **Moteur d'Auto-Ajustement TP/SL (IA)** : Recalibrage automatique (Cron 8h / Manuel) des positions `auto_tp_sl = 1`. Intègre le Contexte Macro SSOT, la Microstructure (Funding/OI), le RSI et formalise des logs explicites distinguant positions *Inchangées* et *Vrais ajustements*. | `v3.3.1` |
| **`crypto_monitor.py`** | **Sentinelle Proactive 24/7 (Cron /15 min)** : Surveille le cours des positions ouvertes en BDD. Déclenche les alertes Telegram d'urgence (`TP1`, `TP2`, `Stop-Loss`, `Time-Stop 21j`) et met à jour les étapes en BDD. | `v3.3.1` |
| **`funding_oi_engine.py`** | **Moteur Microstructure de Marché** : Analyse du Funding Rate (8h) et de la variation de l'Open Interest (OI) sur les contrats Swaps OKX/Kraken. | `v1.0.0` |
| **`rsi_engine.py`** | **Moteur Screener RSI Multi-TF** : Calcul en temps réel des valeurs RSI (15m, 1h, 4h, 1D) pour la génération de la Heatmap Streamlit et la détection de surachats/surventes. | `v1.0.0` |
| **`pnl_engine.py`** | **Moteur PnL FIFO & Metrics** : Calcule le PnL réalisé exact, le Win Rate %, le Profit Factor et maintient le journal d'historique des positions clôturées. | `v3.0.0` |
| **`crypto_speculative_advisor.py`** | **Moteur de Reporting & IA (Daily Briefing 19h)** : Génère le rapport quotidien via Gemini v3.3.1, intègre le Contexte Macro SSOT (`macro_market_engine`), la ventilation unifiée des poches sans doublons et transmet les synthèses Telegram avec boutons d'action 1-click. | `v3.3.1` |
| **`reconciliation_engine.py`** | **Moteur d'Audit BDD vs Exchanges** : Agrège les positions par `(Actif, Exchange)`. Détecte les `GHOST_POSITION`, `VOLUME_MISMATCH` et `MANUAL_TRADE_DETECTED`. | `v3.0.0` |
| **`exchange_connectors.py`** | **Infra & Ingestion de données** : Connecteur multi-sources (Kraken Spot + Staking, OKX Europe/Proxy, Exodus). | `v3.3.1` |
| **`db_manager.py`** | **Gestionnaire de Base de Données** : Interface CRUD SQLite pour `crypto_trading.db`. | `v3.1.0` |
| **`telegram_finary_bot.py`** | **Bot Telegram Interactif v3.0** : Support des commandes `/status`, `/positions`, `/pnl`, `/funding`, `/rsi`, `/audit`. | `v3.0.0` |

---

## 3. Architecture des Onglets Streamlit (`v3.3.1`)

1. **📊 Positions Actives (Live) :**
   * **Ligne 1 : Sentiment & Macro-Marché Globale (5 métriques SSOT) :** Fear & Greed Index, Circuit Breaker BTC (24h), Dominance BTC (BTC.D %), TOTAL3 (Cap. Alts en Mds $), Poudre Sèche Stablecoins Globales (Mds $).
   * **Ligne 2 : Allocations & Portefeuille Réel (4 métriques unifiées) :** Volume Total Crypto, Majeures (BTC/ETH), Poche CORE, Poche Spéculative Total (avec PnL latent %/€ et % du portfolio).
   * **Pilotes de Risque :** Positions Spéculatives Actives (Free Ride / À Risque) et Buffer de Risque Global (% marge moyenne avant SL).
   * **Visualisation Graphique :** Camembert d'allocation globale et zooms interactifs par poche d'actifs (Majeures, CORE, Spéculatif).
   * **Suivi & Actions Dynamiques :** Tableau stylisé avec alertes visuelles, vue mobile tactile, sous-onglets d'action 1-click (Clôture, Break-Even, Édition SL/TP rapide, Mode Auto IA, Consolidation de doublons).
   * **Journal FIFO Clôturé :** Historique des trades fermés, Win Rate %, Profit Factor et PnL cumulé.

2. **🔥 Microstructure & Dérivés :**
   * Synthèse globale des Funding Rates moyens à 8h et Open Interest total sur le portefeuille.
   * Tableau de détails par paire avec détection visuelle des risques de *Long Squeeze* (FR > +0.03%) ou des opportunités de *Short Squeeze* (FR < -0.01%).

3. **🔔 Centre d'Alertes :**
   * **Rapprochement BDD vs Exchanges :** Détection d'anomalies de volume, de lignes fantômes nettoyables en 1 clic et d'intégration automatisée de nouveaux trades manuels en Moonbag ou Poche CORE.
   * **Heatmap RSI Multi-Timeframe :** Screener visuel temps réel (15m, 1h, 4h, 1D) avec style dégradé type Coinglass.
   * **Vigie des Risques :** Surveillance automatique des allocations spéculatives (>12% / >15%), des Stop-Loss manquants et des prix approchant les TP1/SL.

4. **🤖 Assistant IA Pro (SSOT) :**
   * Recalibrage manuel instantané de l'Auto TP/SL via Gemini.
   * **Prompts Rapides 1-Click :**
     * 📰 *Rapport & Évaluation Spéculative*
     * 🛡️ *Audit de Protection & SL*
     * 🔄 *Arbitrages & Rééquilibrage Macro*
     * 🧭 *Plan d'Exécution Pas-à-Pas*
   * Chat libre conversationnel en direct raccordé au contexte live du portefeuille et au bloc Macro SSOT.

5. **🛠️ Administration & Monitoring :**
   * **Santé APIs :** Latence des connecteurs Kraken et OKX (MEXC retiré).
   * **Configuration Gemini Centralisée :** Gestion dynamique de l'ordre de priorité des modèles Gemini et du timeout réseau (`gemini_config.json`).
   * **Administration Prompts SSOT (.md) :** Édition à chaud, validation explicite et restauration en 1-click depuis les sauvegardes `.bak` pour `project_rules.md`, `advisor_speculative.md`, `macro_arbitrage.md`, `risk_manager.md` et `auto_tpsl.md`.
   * **Systemd Logs Viewer :** Consultation directe des journaux de services Linux (`streamlit.service`, `finary-bot.service`, `cloudflare.service`).

---

## 4. Flux de Données & Moteur Intelligents (Pipeline IA)

```
                       ┌─────────────────────────────────────────┐
                       │          macro_market_engine.py         │
                       │   (BTC.D, TOTAL3, Stables, F&G, CB)   │
                       └────────────────────┬────────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
┌───────────────────────────────────────┐       ┌───────────────────────────────────────┐
│             dashboard.py              │       │     crypto_speculative_advisor.py     │
│ - Bandeau KPI Macro & Portefeuille    │       │ - Rapport quotidien de 19h            │
│ - Assistant IA Pro (Prompts Rapides) │       │ - Synthèses & Recommandations Telegram│
└───────────────────┬───────────────────┘       └───────────────────┬───────────────────┘
                    │                                               │
                    └───────────────────────┬───────────────────────┘
                                            │
                                            ▼
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                                  prompt_loader.py                                     │
│  - Assemblage : project_rules.md (Matrice Macro) + Template Rôle (.md)               │
└───────────────────────────────────────────┬───────────────────────────────────────────┘
                                            │
                                            ▼
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                                 gemini_manager.py                                     │
│  - Appel API Gemini avec gestion de Fallback (3.8-flash ➔ 3.6-flash) & Retry         │
└───────────────────────────────────────────────────────────────────────────────────────┘
```