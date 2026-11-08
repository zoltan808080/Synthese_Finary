# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v3.6.0` (Institutional Risk, BTC.D Gatekeeper, Vigie Intra-CORE 35% & High-Water Mark)

**Dernière mise à jour :** 8 novembre 2026

**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)

**Environnement :** VM Linux Debian (`finary-digest-server`, Google Cloud Platform)

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une infrastructure autonome de surveillance, de réconciliation comptable temps réel, d'analyse quanti-microstructure, d'arbitrage macro-patrimonial et de gestion dynamique des risques assistée par Intelligence Artificielle (Gemini SSOT).

Le système assure l'indépendance vis-à-vis des agrégateurs tiers et applique les doctrines de gestion de portefeuille avec une rigueur institutionnelle :

* **Verrou Doctrinal "BTC.D Gatekeeper" (`v3.6.0`) :** Contrôle structurel des flux inter-poches basé sur la dominance du Bitcoin. Dès que $\text{BTC.D} \ge 55{,}0\%$, le système verrouille formellement tout basculement du surplus des Majeures ou du Cash vers les Altcoins. La rotation n'est déverrouillée que sous les $50{,}0\%$ de dominance avec expansion confirmée de TOTAL3.


* **Vigie de Concentration Intra-CORE 35% Max (`v3.6.0`) :** Plafond strict interdisant à un seul actif de dépasser 35% de la valeur marchande de la poche CORE. Le système intègre un pré-seuil de surveillance à 30%, calcule l'écrêtage exact requis en euros vers BTC/Cash et notifie l'utilisateur via Telegram avec anti-spam 24h et escalade immédiate.


* **Cycle de Vie & Qualification des Thèses CORE (`v3.6.0`) :** Stockage en base SQLite des métadonnées de conviction (`ACTIVE`, `WATCHLIST_TRIM`, `EXIT_RECOMMENDED`) avec saisie de notes stratégiques dans le dashboard et transmission directe aux prompts d'arbitrage IA.


* **Timing d'Exécution Technique RSI (`v3.6.0`) :** Interdiction doctrinale de liquider au marché un actif CORE taggé en sortie si son RSI journalier est en survente extrême ($RSI \le 30$) afin de temporiser l'ordre et capter le rebond technique.


* **Trailing Stop-Loss & High-Water Mark (`v3.5.0`) :** Capture atomique du prix le plus haut atteint (`highest_price_seen`) depuis l'ouverture de chaque position pour ancrer les Stop-Loss suiveurs sans violer le verrou immuable du *Ratchet* (interdiction stricte d'abaisser un Stop-Loss).


* **Benchmark Bitcoin & Calcul d'Alpha (`v3.4.0`) :** Mesure en direct du coût d'opportunité par rapport au Bitcoin ($\text{Alpha}_{\text{BTC}} = \text{PnL}_{\text{Alt}} - \text{PnL}_{\text{BTC}}$) grâce à l'enregistrement du cours BTC/EUR à l'entrée (`btc_price_entry`).


* **Routage Hybride Gemini (SSOT Flash-Lite) :** Optimisation des quotas API via l'aiguillage des alertes sentinelles haute fréquence vers `gemini-3.5-flash-lite`, réservant les modèles à large contexte (`gemini-3.8-flash`) aux audits macro et aux rapports stratégiques.


* **Isolation Stricte des 4 Poches :** Répartition doctrinale étanche : **Majeures** (BTC/ETH, cible 55%), **CORE** (conviction long terme, cible 22%), **Spéculatif** (Moonbags sous Stop-Loss, cible 10% / max 12%), et **Cash & Stables** (cible nominale 13%, ajustable dynamiquement selon le *Fear & Greed*).
* **Affichage Harmonisé 5 Colonnes :** Intégration d'une 5ᵉ carte dédiée à la poche *Stables & Cash* dans la rangée KPI du dashboard, garantissant une cohérence arithmétique rigoureuse à 100,0% sans divergence apparente.



---

## 2. Cartographie des Fichiers & Composants

### 🎛️ Briques du Système (Production)

| Fichier / Dossier | Rôle & Responsabilité | Version |
| --- | --- | --- |
| **`dashboard.py`** | **Interface utilisateur Web (Streamlit)** : Cockpit unifié en 5 onglets. Intègre le bandeau *BTC.D Gatekeeper*, la rangée d'allocations sur 5 colonnes (Total, Majeures, CORE, Spéculatif, Cash), les jauges de concentration intra-CORE à 35%, l'accordéon d'édition des thèses CORE et l'Alpha vs BTC.

 | `v3.6.0`

 |
| **`db_manager.py`** | **Gestionnaire de Base de Données SQLite** : Interface CRUD pour `crypto_trading.db`. Gère les positions ouvertes, trades fermés FIFO, filtres CORE ignorés, l'hydratation BTC, le High-Water Mark et la table `core_assets_metadata`.

 | `v3.6.0`

 |
| **`macro_market_engine.py`** | **Source Unique de Vérité Macro (SSOT)** : Calculateur centralisé du triplet Macro (BTC.D, TOTAL3, Stablecoins DefiLlama), Fear & Greed, Circuit Breaker BTC (-5.0%), évaluation formelle du statut *Gatekeeper* et calcul de la cible Cash dynamique.

 | `v3.6.0`

 |
| **`crypto_monitor.py`** | **Sentinelle Proactive 24/7 (Cron /15 min)** : Surveille le cours des Moonbags (TP1, TP2, SL, Alpha vs BTC, Time-Stop 21j) et pilote la vigie de surconcentration intra-CORE (Cap 35% / Seuil 30%) avec anti-spam 24h et conseils flash via `gemini-3.5-flash-lite`.

 | `v3.6.0` |
| **`prompts/macro_arbitrage.md`** | **Prompt Stratégique SSOT** : Consignes de rééquilibrage patrimonial inter et intra-poches intégrant le verrou BTC.D, les statuts de qualification CORE (`ACTIVE`, `WATCHLIST_TRIM`, `EXIT_RECOMMENDED`) et le timing RSI.

 | `v3.6.0` |
| **`auto_tpsl_engine.py`** | **Moteur d'Auto-Ajustement TP/SL (IA)** : Recalibrage automatique (Cron 8h / Manuel) des positions en mode `auto_tp_sl = 1`. Ingeste les données spot, l'Alpha vs BTC, le Contexte Macro SSOT, la Microstructure et le RSI.

 | `v3.5.0` |
| **`reconciliation_engine.py`** | **Moteur d'Audit BDD vs Exchanges** : Rapprochement automatique sans dépendance MEXC (100% Kraken/OKX). Détecte les `GHOST_POSITION`, `VOLUME_MISMATCH` et onboarde les `MANUAL_TRADE_DETECTED`.

 | `v3.5.0` |
| **`crypto_speculative_advisor.py`** | **Moteur de Reporting Quotidien (19h)** : Rapport d'analyse de marché via Gemini, synthèse macro-économique, filtrage anti-doublon CORE/Majeures et alertes Telegram interactives.

 | `v3.5.0` |
| **`prompts/`** | **Dossier des Prompts SSOT (.md)** : Centralisation des rôles IA (`project_rules.md`, `auto_tpsl.md`, `risk_manager.md`, `advisor_speculative.md`, `flash_advice.md`, `macro_arbitrage.md`).

 | `v3.6.0` |
| **`prompt_loader.py`** | **Gestionnaire & Chargeur de Prompts** : Importation dynamique des templates Markdown et gestion automatique des sauvegardes `.bak` lors des modifications.

 | `v1.0.0`

 |
| **`gemini_manager.py`** | **Connecteur Centralisé Gemini** : Gestion des appels API, mécanisme de fallback inter-modèles, gestion des timeouts et surcharge de modèles (`model_override`).

 | `v1.0.0`

 |
| **`config/gemini_config.json`** | **Configuration des Modèles IA** : Définition de la chaîne de priorité (`gemini-3.8-flash`, `gemini-3.6-flash`, etc.), retries et timeouts réseau.

 | `v1.0.0`

 |
| **`funding_oi_engine.py`** | **Moteur Microstructure de Marché** : Récupération des Funding Rates (8h) et de l'Open Interest (OI) sur les marchés dérivés OKX/Kraken.

 | `v1.0.0`

 |
| **`rsi_engine.py`** | **Moteur Screener RSI Multi-TF** : Calcul multi-timeframe (15m, 1h, 4h, 1D) pour la Heatmap Streamlit et la détection des zones de survente/surachat.

 | `v1.0.0`

 |
| **`exchange_connectors.py`** | **Connecteurs Exchanges Directs** : Requêtage API direct (Kraken Spot + Staking, OKX V5, Exodus) pour extraction des soldes bruts et historiques d'ordres.

 | `v3.5.0` |
| **`telegram_finary_bot.py`** | **Bot Telegram Interactif** : Interface de commande distante (`/status`, `/positions`, `/pnl`, `/funding`, `/rsi`, `/audit`) et callbacks de clôture 1-click.

 | `v3.0.0`

 |

---

## 3. Schéma Relationnel & Modèle de Données SQLite (`crypto_trading.db`)

Le stockage local repose sur SQLite (`~/Synthese_finary/crypto_trading.db`) et est structuré autour de 5 tables principales :

```
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│       speculative_trades        │       │      core_assets_metadata       │
├─────────────────────────────────┤       ├─────────────────────────────────┤
│ id (PK, AUTOINCREMENT)          │       │ symbol (PK, TEXT)               │
│ symbol (TEXT)                   │       │ thesis_status (TEXT)            │
│ exchange (TEXT)                 │       │ target_weight_pct (REAL)        │
│ price_suggested (REAL)          │       │ notes (TEXT)                    │
│ price_entry (REAL)              │       │ updated_at (TIMESTAMP)          │
│ amount_eur (REAL)               │       └─────────────────────────────────┘
│ tp_price / tp1_price (REAL)     │
│ tp2_price (REAL)                │       ┌─────────────────────────────────┐
│ sl_price (REAL)                 │       │   core_concentration_alerts     │
│ sl_type (TEXT)                  │       ├─────────────────────────────────┤
│ status (TEXT)                   │       │ symbol (PK, TEXT)               │
│ alert_status (TEXT)             │       │ level (TEXT)                    │
│ step (TEXT)                     │       │ last_alerted (TIMESTAMP)        │
│ auto_tp_sl (INTEGER: 0/1)       │       └─────────────────────────────────┘
│ btc_price_entry (REAL)          │
│ highest_price_seen (REAL)       │       ┌─────────────────────────────────┐
│ created_at / taken_at / closed  │       │      ignored_manual_trades      │
└────────────────┬────────────────┘       ├─────────────────────────────────┤
                 │                        │ id (PK, AUTOINCREMENT)          │
                 ▼                        │ symbol / exchange (TEXT)        │
┌─────────────────────────────────┐       │ cost_eur / price_eur (REAL)     │
│          closed_trades          │       │ reason (TEXT DEFAULT 'CORE')    │
├─────────────────────────────────┤       │ created_at (TIMESTAMP)          │
│ id (PK, AUTOINCREMENT)          │       └─────────────────────────────────┘
│ trade_id (INTEGER)              │
│ symbol / exchange (TEXT)        │
│ entry_price / exit_price (REAL) │
│ amount_eur / pnl_eur / pnl_pct  │
│ opened_at / closed_at           │
│ close_reason (TEXT)             │
└─────────────────────────────────┘

```

### Détail des Tables Clés

1. **`speculative_trades`** : Suivi intégral des Moonbags. Les champs `btc_price_entry` (cours du BTC lors de l'activation) et `highest_price_seen` (sommet local) permettent respectivement le calcul de l'Alpha dynamique et le pilotage du Trailing Stop.


2. **`core_assets_metadata` (v3.6.0)** : Répertoire des thèses d'investissement sur les actifs de conviction. Le champ `thesis_status` supporte les valeurs :


* `ACTIVE` : Pilier fondamental, thèse intacte (ex: SOL, BNB, LINK, SUI).
* `WATCHLIST_TRIM` : Surveillance renforcée / prise de profit à envisager suite à une surconcentration ou essoufflement.
* `EXIT_RECOMMENDED` : Actif sans thèse patrimoniale pérenne, pur jeton de flux ou asymétrie défavorable face au BTC (ex: TRX, XRP).


3. **`core_concentration_alerts` (v3.6.0)** : Table de filtrage anti-spam pour la sentinelle Telegram. Enregistre le dernier niveau d'alerte émis (`APPROACHING_CAP` pour 30-35% ou `CAP_EXCEEDED` pour $\ge 35\%$) et son horodatage ISO afin d'imposer un délai de rétention de 24 heures en cas de stagnation.
4. **`closed_trades`** : Journal comptable d'arbitrage FIFO. Consigne le PnL réalisé en euros et en pourcentage, alimentant le Win Rate et le Profit Factor global.


5. **`ignored_manual_trades`** : Table d'exclusion permettant de masquer les achats manuels détectés sur exchange lorsque l'utilisateur décide de les affecter à la poche CORE plutôt qu'au trading spéculatif.



---

## 4. Architecture des Onglets Streamlit (`dashboard.py` v3.6.0)

L'application web s'articule autour de 5 espaces de travail spécialisés :

```
====================================================================================================
 🚀 COCKPIT SPÉCULATIF & MOONBAG — DASHBOARD ANALYTICS (v3.6.0)
====================================================================================================
 [🔒 BANNIÈRE BTC.D GATEKEEPER : VERROU ACTIF / RÉGIME TRANSITION / ALTSEASON]
----------------------------------------------------------------------------------------------------
 Onglet 1 : 📊 Positions Actives (Live & BTC)
   ├─ Ligne 1 : Macro-Marché SSOT (Fear & Greed, Circuit Breaker, BTC.D, TOTAL3, Stables)
   ├─ Ligne 2 : Portefeuille Réel (Volume Total, Majeures, CORE, Spéculatif, Stables & Cash) [5 Cols]
   ├─ Camembert Global & Zooms par Poche (Majeures, CORE avec Jauge 35%, Spéculatif)
   ├─ Expander : 🏷️ Cycle de vie & Thèses CORE (Sélection du statut et notes BDD)
   ├─ Tableau des Positions Moonbag (PnL, Alpha vs BTC, TP1, SL, Statut, Mode Auto IA)
   └─ Actions 1-Click (Clôture BDD, Break-Even, Ajustement Paliers, Toggle Auto, Fusion Doublons)
----------------------------------------------------------------------------------------------------
 Onglet 2 : 🔥 Microstructure & Dérivés
   ├─ KPIs Microstructure (Funding Moyen 8h, Open Interest Global, Risques Squeeze)
   └─ Tableau détaillé des paires Swaps OKX avec coloration Coinglass
----------------------------------------------------------------------------------------------------
 Onglet 3 : 🔔 Centre d'Alertes
   ├─ Réconciliation BDD vs Exchanges (Détection Lignes Fantômes, Mismatches, Nouveaux Achats)
   ├─ Screener RSI Multi-Timeframe Heatmap (15m, 1h, 4h, 1D)
   └─ Vigie des Risques Globale (Surconcentration Spéculative >12%/>15%, SL Frôlés)
----------------------------------------------------------------------------------------------------
 Onglet 4 : 🤖 Assistant IA Pro (SSOT Gemini)
   ├─ Recalibrage Auto TP/SL manuel (1-Click)
   ├─ Prompts Rapides Spécialisés (Rapport Spéculatif, Audit Protection, Arbitrages Macro, Exécution)
   └─ Chat conversationnel en continu ancré sur le contexte live
----------------------------------------------------------------------------------------------------
 Onglet 5 : 🛠️ Administration & Monitoring
   ├─ État de santé et latence des APIs Kraken et OKX
   ├─ Configuration centralisée des modèles Gemini (Priorité, Timeout, Retries)
   ├─ Éditeur de Prompts Système SSOT (.md) avec sauvegarde et restauration .bak
   └─ Visualiseur de logs systemd (streamlit, finary-bot, cloudflare)
====================================================================================================

```

---

## 5. Logique Métier & Verrous Doctrinaux (SSOT v3.6.0)

### A. Le Verrou "BTC.D Gatekeeper" & Régimes Macro

Le moteur `macro_market_engine.py` calcule en continu la dominance de marché du Bitcoin et impose une discipline d'allocation inviolable :

```
          BTC.D >= 55.0%                     50.0% <= BTC.D < 55.0%                  BTC.D < 50.0%
     ┌───────────────────────┐            ┌──────────────────────────┐          ┌──────────────────────┐
     │     DRAINAGE_BTC      │            │        TRANSITION        │          │      ALTSEASON       │
     │  🔒 VERROU ACTIF      │            │   ⚖️ RÉGIME OBSERVATION  │          │  🟢 DÉVERROUILLÉ     │
     └──────────┬────────────┘            └────────────┬─────────────┘          └──────────┬───────────┘
                │                                      │                                   │
   INTERDICTION FORMELLE DE               Aucun arbitrage offensif sans             Rotation autorisée du
   rotation Majeures/Cash vers            breakout franc de TOTAL3.                 surplus Majeures/Cash vers
   les Altcoins. Surplus maintenu         Conservation des équilibres               poches CORE et Spéculative.
   en BTC/ETH ou sécurisé en Cash.        nominaux.

```

### B. Vigie de Concentration Intra-CORE & Timing RSI

* **Plafond Doctrinal (35%) :** Aucun actif de la poche CORE ne doit excéder $35{,}0\%$ du montant total valorisé de cette poche.


* **Seuil d'Alerte Précoce (30%) :** Dès qu'un token atteint $30{,}0\%$, la sentinelle `crypto_monitor.py` émet une notification d'approche de plafond.
* **Calcul Automatique d'Écrêtage :**

$$\text{Écrêtage Requis (€)} = \text{Valeur Actuelle Actif} - (\text{Valeur Totale CORE} \times 0{,}35)$$


* **Filtre de Temporisation RSI :** Si un actif est désigné pour une sortie ou un écrêtage partiel mais présente un **$RSI_{\text{1D}} \le 30$** (survente extrême), interdiction d'exécuter l'ordre au marché ; l'ordre doit être posé en limite sur rebond technique (zone RSI 50–60). Si **$RSI_{\text{1D}} \ge 70$**, exécution immédiate recommandée.

### C. Cibles de Cash Dynamiques (Fear & Greed)

La cible nominale de **13,0%** allouée à la poche Cash & Stables s'ajuste selon la psychologie de marché calculée par `macro_market_engine.py` :

* **Peur Extrême ($F\&G < 25$) :** Cible abaissée à **$8\text{--}10\%$** pour déployer les réserves sur les opportunités décotées.


* **Cupidité Extrême ($F\&G > 75$) :** Cible relevée à **$18\text{--}20\%$** par prises de profits défensives.


* **Régime Neutre ($25 \le F\&G \le 75$) :** Cible maintenue à **$13{,}0\%$**.



---

## 6. Flux de Données & Architecture Inter-Services

```
 ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
 │    Kraken REST API   │   │     OKX REST API     │   │  DefiLlama / Alt.me  │
 └──────────┬───────────┘   └──────────┬───────────┘   └──────────┬───────────┘
            │                          │                          │
            ▼                          ▼                          ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │               exchange_connectors.py & macro_market_engine.py              │
 │ - Ingestion soldes réels spot, dérivés & taux de change EUR/USD            │
 │ - Calcul du Triplet Macro (BTC.D, TOTAL3, Stables) & Détection Crash CB    │
 │ - Évaluation du statut BTC.D Gatekeeper & Cible Cash Dynamique             │
 └──────────────────────┬──────────────────────────────────┬──────────────────┘
                        │                                  │
                        ▼                                  ▼
 ┌──────────────────────────────────────────────┐ ┌───────────────────────────┐
 │                 dashboard.py                 │ │     crypto_monitor.py     │
 │ - Affichage 5 colonnes unifié                │ │ - Scan Moonbags 24/7      │
 │ - Jauge Intra-CORE 35% & Bannière Gatekeeper │ │ - Vigie Intra-CORE 35%    │
 │ - Expander qualification thèses BDD          │ │ - Anti-spam 24h SQLite    │
 └──────────────────────┬───────────────────────┘ └─────────────┬─────────────┘
                        │                                       │
                        ▼                                       ▼
 ┌──────────────────────────────────────────────┐ ┌───────────────────────────┐
 │          SQLite: crypto_trading.db           │ │   Telegram Bot API        │
 │ - speculative_trades (Alpha, HWM, Steps)     │ │ - Alertes urgentes TP/SL  │
 │ - core_assets_metadata (Statuts, Notes)      │ │ - Alertes concentration   │
 │ - core_concentration_alerts (Anti-spam 24h)  │ │ - Boutons clôture 1-click │
 └──────────────────────┬───────────────────────┘ └───────────────────────────┘
                        │
                        ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │               Moteurs IA Gemini (SSOT v3.6.0 via prompt_loader)             │
 │ - macro_arbitrage.md : Stratégie inter/intra-poches, Gatekeeper & Thèses   │
 │ - flash_advice.md    : Sentinelle rapide sur gemini-3.5-flash-lite         │
 │ - auto_tpsl.md       : Ajustements Stop-Loss suiveurs & Trailing HWM       │
 └────────────────────────────────────────────────────────────────────────────┘

```

---

## 7. Procédure de Déploiement & Maintenance VM

### Services Systemd Déployés

L'ensemble des services tourne sous Linux Debian sur la machine hôte :

* **`streamlit.service`** : Serveur d'interface web accessible en interne ou via Cloudflare Tunnel.
* **`finary-bot.service`** : Processus résident assurant l'écoute des commandes Telegram et les webhooks interactifs.
* **`cloudflare.service`** : Tunnel réseau sécurisé pour l'accès HTTPS distant.
* **Tâches Cron périodiques** :
* `*/15 * * * *` : Exécution de `crypto_monitor.py` (Sentinelle Moonbag & Vigie CORE).
* `0 */8 * * *` : Exécution de `auto_tpsl_engine.py` (Recalibrage Funding 8h).
* `0 19 * * *` : Exécution de `crypto_speculative_advisor.py` (Briefing institutionnel quotidien).



### Commandes de Validation Opérationnelle

Vérifier la compilation et l'état des services après toute mise à jour du code :

```bash
# Compilation et validation de syntaxe
./venv/bin/python3 -m py_compile dashboard.py db_manager.py macro_market_engine.py crypto_monitor.py

# Test unitaire de la sentinelle et de la vigie CORE
./venv/bin/python3 crypto_monitor.py

# Test unitaire du moteur macro
./venv/bin/python3 macro_market_engine.py

# Redémarrage à chaud de l'interface
sudo systemctl restart streamlit.service
sudo systemctl status streamlit.service --no-pager

```