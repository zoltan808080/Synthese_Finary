# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v3.0`  
**Dernière mise à jour :** 12 octobre 2026  
**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)  
**Environnement :** VM Linux Debian (`finary-digest-server`)

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une plateforme autonome d'agrégation, de réconciliation comptable, d'analyse quanti-microstructure et de pilotage spéculatif. Il élimine la dépendance aux agrégateurs tiers pour le suivi en direct et applique la stratégie **Moonbag** avec une rigueur de *Risk Management* automatisé et un suivi exact du PnL réalisé via la méthode FIFO.

### Principes fondateurs
* **Source de vérité unique & Journalisation FIFO :** Connexion directe aux API publiques et privées des exchanges (Kraken Spot + Staking `ETH2.S`, OKX Europe/Vercel Proxy, MEXC) et wallets froids (Exodus BTC/ETH). Les positions clôturées sont archivées de manière immuable dans la table SQLite `closed_trades`.
* **Règle Cash-Neutral strict :** Tout achat spéculatif recommandé par l'IA doit être financé par les liquidités disponibles (Fiat + Stablecoins USDT/USDC) ou par l'arbitrage/vente d'un actif existant dans la limite de son plafond strict en portefeuille.
* **Analyse Microstructure de Marché :** Évaluation en direct de la surchauffe et de la liquidité globale via les taux de financement (*Funding Rates*) et l'intérêt ouvert (*Open Interest*) pour anticiper les chasses à la liquidité (*Long/Short Squeezes*).
* **Réconciliation & Audit Temps Réel :** Comparaison continue entre l'état théorique SQLite et la réalité des comptes (agrégation par actif/exchange, détection des lignes fantômes, écarts de volume > 30 % et onboarding des trades manuels).
* **Dual Interface (v3.0) :**
  * **Dashboard Web Streamlit** : Pilotage visuel, journal de trading interactif, suivi live, assistant IA conversationnel avec prompts rapides, monitoring et export fiscal.
  * **Bot Telegram v3.0 & Crons** : Alertes urgentes 24/7 (TP/SL/Time-stop), Daily Briefing de 19h avec arbitrage 1-click, et commandes analytiques (`/status`, `/pnl`, `/funding`, `/rsi`, `/positions`).

---

## 2. Cartographie des Fichiers & Composants

### 🎛️ Briques Principales (Production)

| Fichier | Rôle & Responsabilité | Version |
| :--- | :--- | :--- |
| **`dashboard.py`** | **Interface utilisateur Web (Streamlit)** : Cockpit unifié avec positions actives, journal & historique des positions clôturées (métriques Win Rate, Profit Factor, PnL cumulé), Risk Management, Daily Briefing 19h, Centre d'Alertes & Réconciliation, Assistant IA Pro, Monitoring APIs et Export Fiscal. | `v3.0` |
| **`crypto_monitor.py`** | **Sentinelle Proactive 24/7 (Cron /15 min)** : Surveille le cours des positions ouvertes en BDD. Déclenche les alertes Telegram d'urgence (`TP1` x2, `TP2` x4, `Stop-Loss`, `Time-Stop 21j`) et met à jour les étapes en BDD (`ENTRY` ➔ `TP1_DONE`). | `v1.8.5` |
| **`funding_oi_engine.py`** | **Moteur Microstructure de Marché** : Analyse du Funding Rate et de la variation de l'Open Interest (OI) sur les paires majeures pour détecter les risques de Squeeze et surchauffe de marché. | `v1.0` |
| **`pnl_engine.py`** | **Moteur PnL FIFO & Metrics** : Calcule le PnL réalisé exact, le Win Rate %, le Profit Factor, les gains/pertes bruts et maintient la rétrocompatibilité 100 % avec `/audit` (`open_positions`). | `v3.0` |
| **`crypto_speculative_advisor.py`** | **Moteur de Reporting & IA (Daily Briefing 19h)** : Génère le rapport quotidien via Google Gemini (`gemini-3.8-flash`), calcule la répartition unifiée des poches et envoie les boutons d'arbitrage Telegram. | `v3.0` |
| **`reconciliation_engine.py`** | **Moteur d'Audit BDD vs Exchanges** : Agrège les positions par `(Actif, Exchange)` pour éviter les fausses alertes. Détecte les `GHOST_POSITION`, `VOLUME_MISMATCH` et les `MANUAL_TRADE_DETECTED` (< 48h). | `v3.0` |
| **`exchange_connectors.py`** | **Infra & Ingestion de données** : Connecteur multi-sources (Kraken Spot + Staking, OKX Europe/Proxy, MEXC, Wallets froids Exodus). Fournit les soldes crypto, cash fiat et l'historique d'exécution (`get_all_trades_history`). | `v3.0` |
| **`db_manager.py`** | **Gestionnaire de Base de Données** : Interface CRUD SQLite pour `crypto_trading.db` (`add_recommendation`, `mark_trade_taken`, `archive_closed_trade`, `get_closed_trades_history`, `get_open_positions`). | `v3.0` |
| **`telegram_finary_bot.py`** | **Bot Telegram Interactif v3.0** : Support des commandes `/status`, `/positions`, `/pnl`, `/funding`, `/rsi`, `/audit` et gestion des callbacks 1-click (`Passer en BE`, `Clôturer`). | `v3.0` |
| **`sl_audit_engine.py`** | **Moteur d'Audit Stop-Loss** : Vérifie la présence et la distance des ordres de protection (SL/TP) sur les positions ouvertes. | `v1.6` |
| **`backfill_closed_trades.py`** | **Script d'Archivage Rétroactif** : Rattrape les anciens trades fermés pour initialiser l'historique BDD dans `closed_trades`. | `v1.0` |
| **`crypto_trading.db`** | **Base SQLite Locale** : Stockage persistant des positions ouvertes/fermées, du journal `closed_trades`, des recommandations et des logs d'audit. | Live |

### 🛠️ Outillage, Utilitaires & Fichiers Annexes

* **`sync_exchange_closed_dates.py`** : Script de synchronisation des exécutions d'ordres avec les API exchanges.
* **`cloudflare`** : Binaire d'exécution Cloudflare Tunnel exposant le dashboard Streamlit de manière sécurisée en HTTPS.
* **`cron_crypto.log` / `cron_monitor.log` / `digest.log`** : Traces et journaux d'exécution des tâches planifiées.

---

## 3. Flux d'Information & Architecture Logicielle

```
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
    ┌─────────────────────────────────────┼─────────────────────────────────────┐
    │                                     │                                     │
    ▼                                     ▼                                     ▼
┌───────────────────────┐     ┌───────────────────────┐             ┌───────────────────────┐
│  crypto_monitor.py    │     │reconciliation_engine  │             │ funding_oi_engine.py  │
│ (Cron 24/7 /15 min)   │     └───────────┬───────────┘             │(Microstructure Mkt)   │
└───────┬───────────────┘                 │                         └───────────┬───────────┘
        │ (TP/SL Alerts)                  │ (Audit Écarts)                      │
        ▼                                 ▼                                     │
┌───────────────────────┐     ┌───────────────────────┐                         │
│  telegram_finary_bot  │     │     Dashboard Web     │◄────────────────────────┘
│ (Commandes & Alerts)  │     │  (Streamlit Cockpit)  │
└───────┬───────────────┘     └───────────┬───────────┘
        │                                 │
        └────────────────┬────────────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │     pnl_engine.py     │
             │   (Calculs FIFO/WR)   │
             └───────────┬───────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │   db_manager.py       │
             │  (crypto_trading.db)  │
             └───────────────────────┘
```

---

## 4. Structure de la Base de Données (`crypto_trading.db`)

1. **`closed_trades` (Nouveau v3.0)** : Journal immuable des positions clôturées pour calcul PnL FIFO.
   * `id` (INTEGER PK), `trade_id` (INT), `symbol` (TEXT), `exchange` (TEXT), `entry_price` (REAL), `exit_price` (REAL), `amount_eur` (REAL), `pnl_eur` (REAL), `pnl_pct` (REAL), `opened_at` (TIMESTAMP), `closed_at` (TIMESTAMP), `close_reason` (TEXT).
2. **`speculative_trades` / `trades`** : Suivi opérationnel des positions spéculatives ouvertes.
   * `id`, `symbol`, `exchange`, `entry_price`, `amount_eur`, `tp1_price`, `tp2_price`, `sl_price`, `sl_type`, `status` (`OPEN`, `CLOSED`), `step` (`ENTRY`, `TP1_DONE`, `TP2_DONE`), `taken_at`, `alert_status`.
3. **`recommendations`** : Historique des opportunités d'arbitrage générées par l'IA.
   * `id`, `symbol`, `exchange`, `price`, `amount_eur`, `tp1_price`, `tp2_price`, `sl_price`, `sl_type`, `status` (`PENDING`, `TAKEN`, `IGNORED`), `created_at`.
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

### 💻 Dashboard Web Streamlit (v3.0)
* **Cockpit Positions Actives** : Double camembert (Répartition globale vs Poche spéculative) et suivi PRU.
* **Journal & Historique des Positions Clôturées (Expander)** : Métriques globales (PnL Cumulé, Win Rate %, Ratio W/L, Total Clôturés) et tableau interactif avec colorisation dynamique des gains/pertes.
* **Onglets Analytiques & IA** : Daily Briefing 19h, Centre de Réconciliation/Alertes, Assistant IA Gemini Pro, Monitoring APIs et Export Fiscal.

---

## 6. Automatisation & Infrastructure VM

* **Services Systemd :**
  * `streamlit.service` : Serveur Web Dashboard Streamlit (Port local 8501).
  * `cloudflare.service` : Tunnel sécurisé HTTPS Cloudflare.
  * `finary-bot.service` : Service permanent Telegram Bot API.
* **Tâches Crontab :**
  * `*/15 * * * *` : Exécution de `crypto_monitor.py` (Surveillance 24/7 & alertes urgentes TP/SL).
  * `0 19 * * *` : Exécution de `crypto_speculative_advisor.py` (Rapport quotidien Telegram de 19h).
  * `0 3 * * *` : Backup automatique de `crypto_trading.db` vers `./save/`.