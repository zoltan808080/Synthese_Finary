# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v4.0.0` (Institutional Risk, Moteur de Découverte Dynamique du Marché, Boussole de Flux Macro, Tickets d'Ordres Monospace & Gouvernance CORE 1-Click)  
**Dernière mise à jour :** 9 novembre 2026  
**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)  
**Environnement :** VM Linux Debian (`finary-digest-server`, Google Cloud Platform)  

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une infrastructure autonome de surveillance, de réconciliation comptable temps réel, d'analyse quanti-microstructure, d'arbitrage macro-patrimonial et de gestion dynamique des risques assistée par Intelligence Artificielle (Gemini SSOT).

Le système élimine l'inertie décisionnelle et applique les doctrines de gestion de portefeuille institutionnelles :

* **Moteur de Découverte Dynamique du Marché (`v4.0.0`) :** Remplacement définitif de l'univers restreint des 17 cryptos codées en dur par un scanner temps réel multi-threadé (`market_discovery_engine.py`). Il ingeste l'ensemble des paires spot liquides OKX/Kraken ($\ge 4\text{ M}\$$ de volume 24h), élimine les actions tokenisées (`xStocks`), les commodités (`XAUT/PAXG`), les jetons CEX (`OKB`) et purge automatiquement les 4 poches réelles du portefeuille pour extraire le Top 15 des meilleures asymétries (Rebonds RSI et Momentum Alpha).
* **Boussole de Flux Macro "Market Compass" (`v3.9.1`) :** Remplacement de l'ancien verrou binaire rigide sur la dominance du Bitcoin (BTC.D $\ge 55\%$) par une lecture croisée intelligente ($\text{BTC.D} \times \Delta\text{BTC}_{24\text{h}}$) répartie en 4 régimes (`RISK_OFF_BLEED`, `BTC_LED_MARKET`, `BALANCED_ROTATION`, `ALT_EXPANSION`). La dominance élevée module la sélectivité Alpha sans interdire structurellement le trading d'opportunité.
* **Gouvernance CORE 1-Click (`v3.9.0`) :** Parser automatique des propositions de thèses structurées en JSON (`core_proposals`) générées lors des audits macro, permettant une mise à jour instantanée des statuts CORE en base SQLite via un bouton interactif dans le dashboard.
* **Sentinelle des Actifs Zombies & Anti-Bagholding (`v3.8.0`) :** Routine hebdomadaire (dimanche 20h) de détection de l'attrition d'Alpha face au Bitcoin ($\text{Alpha} \le -15{,}0\%$) avec émission de tickets d'éviction immédiats et respect de 3 garde-fous doctrinaux (verrou d'ancienneté $< 21\text{j}$, immunité des piliers CORE et veto technique de survente $RSI_{\text{1D}} \le 30$).
* **Fiches d'Ordres Standardisées "Order Tickets" (`v3.7.0`) :** Remplacement des consignes textuelles d'arbitrage par des encadrés pré-mâchés prêts à l'emploi (Exchange, Paire, Type d'ordre Limite, Volume en €, Destination et Confluence) avec stylage terminal monospace dans le dashboard.
* **Score de Confluence IA 1 à 5 ⭐ (`v3.7.0`) :** Évaluation algorithmique multi-couches (Macro & Flux 30%, RSI Multi-TF 25%, Microstructure 25%, Alpha Benchmark 20%). Seules les recommandations atteignant une note $\ge 4/5$ ⭐ sont exécutoires.
* **Vigie de Concentration Intra-CORE 35% Max (`v3.6.0`) :** Plafond strict interdisant à un seul actif de dépasser 35% de la valeur de la poche CORE, avec seuil de pré-alerte à 30%, calcul d'écrêtage exact en euros et anti-spam 24h.
* **Trailing Stop-Loss & High-Water Mark (`v3.5.0`) :** Capture atomique du prix le plus haut atteint (`highest_price_seen`) depuis l'ouverture de chaque trade pour ancrer les Stop-Loss suiveurs sans violer le verrou immuable du *Ratchet*.
* **Benchmark Bitcoin & Calcul d'Alpha (`v3.4.0`) :** Mesure en direct du coût d'opportunité par rapport au Bitcoin ($\text{Alpha}_{\text{BTC}} = \text{PnL}_{\text{Alt}} - \text{PnL}_{\text{BTC}}$) grâce à l'enregistrement du cours BTC/EUR à l'entrée (`btc_price_entry`).
* **Isolation Stricte des 4 Poches :** Répartition doctrinale étanche : **Majeures** (BTC/ETH, cible 55%), **CORE** (conviction long terme, cible 22%), **Spéculatif** (Moonbags sous Stop-Loss, cible 10% / max 12%), et **Cash & Stables** (cible nominale 13%, ajustable dynamiquement selon le *Fear & Greed*).

---

## 2. Cartographie des Fichiers & Composants

### 🎛️ Briques du Système (Production)

| Fichier / Dossier | Rôle & Responsabilité | Version |
| :--- | :--- | :--- |
| **`market_discovery_engine.py`** | **Moteur de Découverte Dynamique du Marché** : Scanner spot multi-threadé avec pooling de connexions HTTP (`requests.Session`), filtrage anti-doublon portefeuille BDD, élimination xStocks/CEX et scoring technique (RSI 4h/1D + Funding 8h) avec cache TTL 15 min. | `v4.0.1` |
| **`dashboard.py`** | **Interface utilisateur Web (Streamlit)** : Cockpit unifié en 5 onglets. Intègre les cartes d'allocations unifiées, la Boussole de Flux Macro, la supervision temps réel du vivier dynamique avec re-scan forcé, les jauges intra-CORE 35%, la Sentinelle Zombies et l'application 1-Click des thèses CORE. | `v4.0.0` |
| **`crypto_speculative_advisor.py`** | **Moteur de Reporting Quotidien (19h)** : Rapport d'analyse spéculative quotidien injectant le Top 15 découvert dynamiquement, notation de Confluence (1 à 5 ⭐), calcul d'Alpha live et envoi Telegram avec chunking 3900 car. et boutons 1-Click. | `v4.0.0` |
| **`db_manager.py`** | **Gestionnaire de Base de Données SQLite** : Interface CRUD pour `crypto_trading.db`. Gère les Moonbags, le journal FIFO, les exclusions manuelles, l'hydratation BTC, le High-Water Mark, les métadonnées CORE (`core_assets_metadata`) et l'attrition zombie (`zombie_assets_tracking`). | `v3.8.0` |
| **`zombie_sentinel.py`** | **Sentinelle Hebdomadaire Actifs Zombies (Cron dim. 20h)** : Analyse l'attrition d'Alpha à 30 jours vs BTC sur l'ensemble des avoirs réels ($\ge 30\text{ €}$), applique les 3 garde-fous doctrinaux (trades récents $<21\text{j}$, immunité piliers CORE, veto RSI 1D) et diffuse les tickets d'éviction sur Telegram. | `v3.8.1` |
| **`macro_market_engine.py`** | **Source Unique de Vérité Macro & Boussole de Flux (SSOT)** : Calcule le triplet Macro, Fear & Greed, Circuit Breaker BTC (-5.0%), pilote le statut de la Boussole de Flux (*Market Compass*) et calibre la note du Pilier Macro (30% de la Confluence). | `v3.9.1` |
| **`crypto_monitor.py`** | **Sentinelle Proactive 24/7 (Cron /15 min)** : Surveille le cours des Moonbags (TP1, TP2, SL, Alpha vs BTC, Time-Stop 21j) et pilote la vigie de surconcentration intra-CORE (Cap 35% / Seuil 30%) avec anti-spam 24h et conseils flash via `gemini-3.5-flash-lite`. | `v3.6.0` |
| **`auto_tpsl_engine.py`** | **Moteur d'Auto-Ajustement TP/SL (IA)** : Recalibrage automatique (Cron 8h / Manuel) des positions en mode `auto_tp_sl = 1`. Ingeste les données spot, l'Alpha vs BTC, le Contexte Macro SSOT, la Microstructure et le RSI. | `v3.5.0` |
| **`reconciliation_engine.py`** | **Moteur d'Audit BDD vs Exchanges** : Rapprochement automatique sans dépendance MEXC (100% Kraken/OKX). Détecte les `GHOST_POSITION`, `VOLUME_MISMATCH` et onboarde les `MANUAL_TRADE_DETECTED`. | `v3.5.0` |
| **`prompts/`** | **Dossier des Prompts SSOT (.md)** : Centralisation des rôles IA (`project_rules.md`, `auto_tpsl.md`, `risk_manager.md`, `advisor_speculative.md`, `flash_advice.md`, `macro_arbitrage.md`). | `v4.0.0` |
| **`prompt_loader.py`** | **Gestionnaire & Chargeur de Prompts** : Importation dynamique des templates Markdown et gestion automatique des sauvegardes `.bak` lors des modifications. | `v1.0.0` |
| **`gemini_manager.py`** | **Connecteur Centralisé Gemini** : Gestion des appels API, mécanisme de fallback inter-modèles, gestion des timeouts et surcharge de modèles (`model_override`). | `v1.0.0` |
| **`funding_oi_engine.py`** | **Moteur Microstructure de Marché** : Récupération des Funding Rates (8h) et de l'Open Interest (OI) sur les marchés dérivés OKX/Kraken. | `v1.0.0` |
| **`rsi_engine.py`** | **Moteur Screener RSI Multi-TF** : Calcul multi-timeframe (15m, 1h, 4h, 1D) pour la Heatmap Streamlit et la détection des zones de survente/surachat. | `v1.0.0` |
| **`exchange_connectors.py`** | **Connecteurs Exchanges Directs** : Requêtage direct (Kraken Spot + Staking, OKX V5, Exodus) pour extraction des soldes bruts et historiques d'ordres. | `v3.5.0` |
| **`telegram_finary_bot.py`** | **Bot Telegram Interactif** : Interface de commande distante (`/status`, `/positions`, `/pnl`, `/funding`, `/rsi`, `/audit`) et callbacks de clôture 1-click. | `v3.0.0` |

---

## 3. Procédure de Déploiement & Maintenance VM

### Tâches Cron Périodiques

```bash
# Sentinelle 24/7 des positions ouvertes et vigie de concentration CORE 35%
*/15 * * * * cd /home/gregoire_senant/Synthese_finary && nice -n 19 ./venv/bin/python3 crypto_monitor.py >> crypto_monitor.log 2>&1

# Recalibrage IA des Stop-Loss et Trailing (aux clôtures de Funding Rate 8h)
0 */8 * * * cd /home/gregoire_senant/Synthese_finary && nice -n 19 ./venv/bin/python3 auto_tpsl_engine.py >> auto_tpsl.log 2>&1

# Briefing spéculatif quotidien avec Découverte Dynamique, Confluence et Tickets d'Ordres
0 19 * * * cd /home/gregoire_senant/Synthese_finary && nice -n 19 ./venv/bin/python3 crypto_speculative_advisor.py >> cron_crypto.log 2>&1

# Sentinelle hebdomadaire des Actifs Zombies et Anti-Bagholding (Dimanche 20h00)
0 20 * * 0 cd /home/gregoire_senant/Synthese_finary && nice -n 19 ./venv/bin/python3 zombie_sentinel.py >> zombie_sentinel.log 2>&1
Commandes de Validation Opérationnelle
Bash
# Compilation et validation de syntaxe de tous les modules
./venv/bin/python3 -m py_compile dashboard.py market_discovery_engine.py crypto_speculative_advisor.py db_manager.py macro_market_engine.py crypto_monitor.py zombie_sentinel.py

# Test direct de la découverte dynamique
./venv/bin/python3 market_discovery_engine.py

# Redémarrage à chaud de l'interface utilisateur Streamlit
sudo systemctl restart streamlit.service
sudo systemctl status streamlit.service --no-pager