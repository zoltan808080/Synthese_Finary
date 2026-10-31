# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v3.2`  
**Dernière mise à jour :** 31 octobre 2026  
**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)  
**Environnement :** VM Linux Debian (`finary-digest-server`)

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une plateforme autonome d'agrégation, de réconciliation comptable, d'analyse quanti-microstructure, de pilotage spéculatif et d'ajustement dynamique des risques piloté par IA. Il élimine la dépendance aux agrégateurs tiers pour le suivi en direct et applique la stratégie **Moonbag** avec une rigueur de *Risk Management* automatisé, la recalibration intelligente des paliers (TP/SL) et un suivi exact du PnL réalisé via la méthode FIFO.

### Principes fondateurs
* **Source de vérité unique & Journalisation FIFO :** Connexion directe aux API publiques et privées des exchanges (Kraken Spot + Staking `ETH2.S`, OKX Europe/Vercel Proxy, MEXC) et wallets froids (Exodus BTC/ETH). Les positions clôturées sont archivées de manière immuable dans la table SQLite `closed_trades`.
* **Architecture Prompts SSOT (v3.2) :** Découplage strict entre la logique applicative (Python/Streamlit) et les consignes métier / rôles IA. Les consignes système sont stockées sous forme de fichiers Markdown réutilisables dans le dossier `prompts/` et chargées dynamiquement via `prompt_loader.py`.
* **Pilotage Dynamique TP/SL par l'IA (v3.1 / v3.2) :** Option d'automatisation individuelle des positions via le moteur `auto_tpsl_engine.py`. Recalibrage automatique des paliers TP1, TP2 et Stop-Loss toutes les 8 h (aligné sur les clôtures de Funding Rates) ou déclenché à la demande via l'IA Gemini.
* **Optimisation Multi-Threading des Prix (v3.2) :** Exécution parallélisée (`ThreadPoolExecutor`) de la récupération des cours sur OKX et Kraken pour éliminer toute latence d'affichage au chargement du Dashboard.
* **Règle Cash-Neutral strict :** Tout achat spéculatif recommandé par l'IA doit être financé par les liquidités disponibles (Fiat + Stablecoins USDT/USDC) ou par l'arbitrage/vente d'un actif existant dans la limite de son plafond strict en portefeuille.
* **Analyse Microstructure de Marché :** Évaluation en direct de la surchauffe et de la liquidité globale via les taux de financement (*Funding Rates*) et l'intérêt ouvert (*Open Interest*) pour anticiper les chasses à la liquidité (*Long/Short Squeezes*).
* **Réconciliation & Audit Temps Réel :** Comparaison continue entre l'état théorique SQLite et la réalité des comptes (agrégation par actif/exchange, détection des lignes fantômes, écarts de volume > 30 % et onboarding des trades manuels).

---

## 2. Cartographie des Fichiers & Composants

### 🎛️ Briques Principales (Production)

| Fichier / Dossier | Rôle & Responsabilité | Version |
| :--- | :--- | :--- |
| **`dashboard.py`** | **Interface utilisateur Web (Streamlit)** : Cockpit unifié avec positions actives (indicateur de mode `Auto IA` / `Manuel`), parallélisation multi-threading du fetch des prix, chargement des Prompts SSOT (Onglet 5), déclencheur manuel de recalibrage TP/SL par l'IA, journal & historique FIFO, Risk Management, Daily Briefing 19 h, Centre d'Alertes & Réconciliation, Monitoring APIs et Export Fiscal. | `v3.2` |
| **`prompt_loader.py`** | **Chargeur SSOT de Prompts System** : Module centralisé d'importation dynamique des instructions d'expertise et règles métier depuis le dossier `prompts/`. | `v1.0` |
| **`prompts/`** | **Répertoire des Prompts SSOT** : Stockage modulaire des consignes système de référence (`advisor_speculative.md`, `risk_manager.md`, `project_rules.md`). | `v1.0` |
| **`auto_tpsl_engine.py`** | **Moteur d'Auto-Ajustement TP/SL (IA)** : Analyse périodique (toutes les 8 h) ou manuelle des positions marquées `auto_tp_sl = 1`. Ajuste dynamiquement TP1, TP2 et Stop-Loss selon le RSI, la microstructure (Funding/OI) et les recommandations de Gemini. | `v1.0` |
| **`crypto_monitor.py`** | **Sentinelle Proactive 24/7 (Cron /15 min)** : Surveille le cours des positions ouvertes en BDD. Déclenche les alertes Telegram d'urgence (`TP1` x2, `TP2` x4, `Stop-Loss`, `Time-Stop 21j`) et met à jour les étapes en BDD (`ENTRY` ➔ `TP1_DONE`). | `v1.8.5` |
| **`funding_oi_engine.py`** | **Moteur Microstructure de Marché** : Analyse du Funding Rate et de la variation de l'Open Interest (OI) sur les paires majeures pour détecter les risques de Squeeze et surchauffe de marché. | `v1.0` |
| **`pnl_engine.py`** | **Moteur PnL FIFO & Metrics** : Calcule le PnL réalisé exact, le Win Rate %, le Profit Factor, les gains/pertes bruts et maintient la rétrocompatibilité 100 % avec `/audit` (`open_positions`). | `v3.0` |
| **`crypto_speculative_advisor.py`** | **Moteur de Reporting & IA (Daily Briefing 19h)** : Génère le rapport quotidien via Google Gemini en chargeant ses prompts via `prompt_loader.py`, calcule la répartition unifiée des poches et envoie les boutons d'arbitrage Telegram. | `v3.2` |
| **`reconciliation_engine.py`** | **Moteur d'Audit BDD vs Exchanges** : Agrège les positions par `(Actif, Exchange)` pour éviter les fausses alertes. Détecte les `GHOST_POSITION`, `VOLUME_MISMATCH` et les `MANUAL_TRADE_DETECTED` (< 48h). | `v3.0` |
| **`exchange_connectors.py`** | **Infra & Ingestion de données** : Connecteur multi-sources (Kraken Spot + Staking, OKX Europe/Proxy, MEXC, Wallets froids Exodus). Fournit les soldes crypto, cash fiat et l'historique d'exécution (`get_all_trades_history`). | `v3.0` |
| **`db_manager.py`** | **Gestionnaire de Base de Données** : Interface CRUD SQLite pour `crypto_trading.db`. Intègre la migration douce pour `auto_tp_sl` ainsi que les helpers `toggle_auto_tp_sl` et `get_auto_tp_sl_status`. | `v3.1` |
| **`telegram_finary_bot.py`** | **Bot Telegram Interactif v3.0** : Support des commandes `/status`, `/positions`, `/pnl`, `/funding`, `/rsi`, `/audit` et gestion des callbacks 1-click (`Passer en BE`, `Clôturer`). | `v3.0` |
| **`crypto_trading.db`** | **Base SQLite Locale** : Stockage persistant des positions ouvertes/fermées (avec flag `auto_tp_sl`), du journal `closed_trades`, des recommandations et des logs d'audit. | Live |

---

## 3. Flux d'Information & Architecture Logicielle

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│                              SOURCES DE DONNÉES LIVE                              │
│   (Kraken Spot/Staking, OKX Europe/Proxy, MEXC, Exodus, API Funding/OI, Prices)   │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
                             ┌─────────────────────────┐
                             │ exchange_connectors.py  │
                             └────────────┬────────────┘
                                          │
       ┌──────────────────────────────────┼──────────────────────────────────┐
       ▼                                  ▼                                  ▼
┌───────────────────────┐      ┌───────────────────────┐          ┌───────────────────────┐
│  crypto_monitor.py    │      │reconciliation_engine  │          │ funding_oi_engine.py  │
│ (Cron 24/7 /15 min)   │      └───────────┬───────────┘          │(Microstructure Mkt)   │
└───────┬───────────────┘                  │                      └───────────┬───────────┘
        │ (TP/SL Alerts)                   │ (Audit Écarts)                   │
        ▼                                  ▼                                  │
┌───────────────────────┐      ┌───────────────────────┐                      │
│  telegram_finary_bot  │      │     Dashboard Web     │◄─────────────────────┤
│ (Commandes & Alerts)  │      │  (Streamlit v3.2)     │                      │
└───────┬───────────────┘      └───────────┬───────────┘                      │
        │                                  │                                  │
        └────────────────┬─────────────────┘                                  │
                         │                                                    │
                         ▼                                                    ▼
              ┌───────────────────────┐                            ┌───────────────────────┐
              │     prompt_loader     │                            │  auto_tpsl_engine.py  │
              │  (Architecture SSOT)  │                            │(Recalibrage IA 8h)    │
              └───────────┬───────────┘                            └───────────┬───────────┘
                          │                                                    │
                          └──────────────────────┬─────────────────────────────┘
                                                 │
                                                 ▼
                                      ┌───────────────────────┐
                                      │   db_manager.py       │
                                      │  (crypto_trading.db)  │
                                      └───────────────────────┘
```

---

## 4. Structure de la Base de Données (`crypto_trading.db`)

1. **`speculative_trades`** : Suivi opérationnel des positions spéculatives ouvertes.
   * `id` (INTEGER PK), `symbol` (TEXT), `exchange` (TEXT), `price_suggested` (REAL), `price_entry` (REAL), `amount_eur` (REAL), `tp_price` (REAL), `tp1_price` (REAL), `tp2_price` (REAL), `sl_price` (REAL), `sl_type` (TEXT), `status` (`OPEN`, `CLOSED`, `IGNORED`), `alert_status` (TEXT), `step` (`ENTRY`, `TP1_DONE`, `TP2_DONE`), `auto_tp_sl` (INTEGER DEFAULT 0 — **v3.1**), `created_at` (TIMESTAMP), `taken_at` (TIMESTAMP), `closed_at` (TIMESTAMP).
2. **`closed_trades`** : Journal immuable des positions clôturées pour calcul PnL FIFO.
   * `id` (INTEGER PK), `trade_id` (INT), `symbol` (TEXT), `exchange` (TEXT), `entry_price` (REAL), `exit_price` (REAL), `amount_eur` (REAL), `pnl_eur` (REAL), `pnl_pct` (REAL), `opened_at` (TIMESTAMP), `closed_at` (TIMESTAMP), `close_reason` (TEXT).
3. **`ignored_manual_trades`** : Trades manuels filtrés et classés en CORE.
   * `id`, `symbol`, `exchange`, `cost_eur`, `price_eur`, `reason`, `created_at`.
4. **`reconciliation_logs`** : Historique des audits de réconciliation BDD vs Exchanges.

---

## 5. Matrice des Interfaces (Telegram Bot & Streamlit Web)

### 📱 Telegram Bot (v3.0)
* `/status` : Bilan instantané (Fear & Greed Index, Circuit Breaker, Liquidités Fiat/Stablecoins).
* `/positions` : Liste des positions ouvertes avec boutons d'action 1-click (`🛡️ Passer en BE`, `🔒 Clôturer`).
* `/pnl` : Rapport de PnL réalisé FIFO, Win Rate %, Profit Factor et 5 derniers trades fermés.
* `/funding` : Scan de microstructure (Funding Rates & Open Interest) pour détecter la surchauffe.
* `/rsi` : Screener technique multi-timeframe (1H, 4H, 1D).
* `/audit` : Contrôle de cohérence BDD vs Ordres de protection SL/TP sur exchanges.

### 💻 Dashboard Web Streamlit (v3.2)
* **Cockpit Positions Actives** : Ventilation globale du capital, sous-camemberts par poche (Majeures, CORE, Spéculatif), suivi du PRU, de la valeur marché et indicateur de mode par position (`🤖 Auto IA` vs `👤 Manuel`).
* **Actions & Gestion Dynamique** : Sous-onglets permettant la bascule 1-click du mode `Auto TP/SL (IA)`, la clôture, le passage en Break-Even, l'édition des paliers et la fusion de doublons.
* **Journal & Historique des Positions Clôturées (Expander)** : Métriques globales (PnL Cumulé, Win Rate %, Ratio W/L, Total Clôturés) et tableau interactif.
* **Assistant IA Pro (Onglet 5 — SSOT v3.2)** : Déclencheur manuel de recalibrage `🤖 Lancer le Recalibrage Auto TP/SL (IA)`, 4 prompts d'analyse rapide exploitant l'architecture SSOT (`prompts/`) et chat libre Gemini multi-modèles avec fallback.
* **Onglets Complémentaires** : Microstructure & Dérivés, Daily Briefing 19 h, Centre de Réconciliation/Alertes, Monitoring APIs et Export Fiscal.

---

## 6. Automatisation & Infrastructure VM

* **Services Systemd :**
  * `streamlit.service` : Serveur Web Dashboard Streamlit (Port local 8501).
  * `cloudflare.service` : Tunnel sécurisé HTTPS Cloudflare.
  * `finary-bot.service` : Service permanent Telegram Bot API.
* **Tâches Crontab :**
  * `*/15 * * * *` : Exécution de `crypto_monitor.py` (Surveillance 24/7 & alertes urgentes TP/SL).
  * `0 0,8,16 * * *` : Exécution automatique du cycle de recalibrage `auto_tpsl_engine.py` (Clôtures Funding Rate 8 h).
  * `0 19 * * *` : Exécution de `crypto_speculative_advisor.py` (Rapport quotidien Telegram de 19 h).
  * `0 3 * * *` : Backup automatique de `crypto_trading.db` vers `./save/`.