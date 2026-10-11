# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v2.8`  
**Dernière mise à jour :** 11 octobre 2026  
**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)  
**Environnement :** VM Linux Debian (`finary-digest-server`)

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une plateforme autonome d'agrégation, de réconciliation comptable et de pilotage spéculatif. Il élimine la dépendance aux agrégateurs tiers pour le suivi en direct et applique la stratégie **Moonbag** avec une rigueur de *Risk Management* automatisé.

### Principes fondateurs
* **Source de vérité unique :** Connexion directe aux API publiques et privées des exchanges (Kraken Spot + Staking `ETH2.S`, OKX, MEXC) et wallets froids (Exodus BTC/ETH).
* **Règle Cash-Neutral strict :** Tout achat spéculatif recommandé par l'IA doit être financé par les liquidités disponibles (Fiat + Stablecoins USDT/USDC) ou par l'arbitrage/vente d'un actif existant dans la limite de son plafond strict en portefeuille.
* **Réconciliation & Audit Temps Réel :** Comparaison continue entre l'état théorique SQLite et la réalité des comptes (agrégation par actif/exchange, détection des lignes fantômes, écarts de volume > 30 % et onboarding des trades manuels en fenêtre glissante de 48h).
* **Dual Interface :**
  * **Dashboard Web Streamlit** : Pilotage visuel, suivi live, assistant IA conversationnel avec prompts rapides, monitoring et export fiscal.
  * **Bot Telegram & Crons** : Alertes urgentes 24/7 (TP/SL/Time-stop) et Daily Briefing de 19h avec arbitrage 1-click.

---

## 2. Cartographie des Fichiers & Composants

### 🎛️ Briques Principales (Production)

| Fichier | Rôle & Responsabilité | Version |
| :--- | :--- | :--- |
| **`dashboard.py`** | **Interface utilisateur Web (Streamlit)** : Cockpit à 8 onglets (Positions Live avec double camembert global + zoom spéculatif, Risk Management, Daily Briefing 19h, Centre d'Alertes & Réconciliation, Assistant IA Pro avec Prompts Rapides, Monitoring APIs, Analytics Win Rate, Export Fiscal). | `v2.8` |
| **`crypto_monitor.py`** | **Sentinelle Proactive 24/7 (Cron /15 min)** : Surveille le cours des positions ouvertes en BDD. Déclenche les alertes Telegram d'urgence (`TP1` x2, `TP2` x4, `Stop-Loss`, `Time-Stop 21j`) et met à jour les étapes en BDD (`ENTRY` ➔ `TP1_DONE`). | `v1.8.5` |
| **`crypto_speculative_advisor.py`** | **Moteur de Reporting & IA (Daily Briefing 19h)** : Génère le rapport quotidien via Google Gemini (`gemini-3.8-flash` / `gemini-3.7-flash`), calcule la répartition unifiée des poches et envoie les boutons d'arbitrage Telegram. | `v2.8` |
| **`reconciliation_engine.py`** | **Moteur d'Audit BDD vs Exchanges** : Agrège les positions par `(Actif, Exchange)` pour éviter les fausses alertes sur achats multiples. Détecte les `GHOST_POSITION`, `VOLUME_MISMATCH` et les `MANUAL_TRADE_DETECTED` (< 48h). | `v2.8` |
| **`exchange_connectors.py`** | **Infa & Ingestion de données** : Connecteur multi-sources (Kraken Spot + Staking, OKX, MEXC, Wallets froids Exodus). Fournit les soldes crypto, cash fiat et l'historique d'exécution (`get_all_trades_history`). | `v2.8` |
| **`db_manager.py`** | **Gestionnaire de Base de Données** : Interface CRUD SQLite pour `crypto_trading.db` (`add_recommendation`, `mark_trade_taken`, `close_trade`, `update_trade_amount`, `get_open_positions`). | `v2.8` |
| **`telegram_finary_bot.py`** | **Bot Telegram Interactif** : Traite les callbacks 1-click (`trade_take_X`, `trade_ignore_X`, `trade_close_X`) et gère les interactions Telegram en direct. | `v2.0` |
| **`pnl_engine.py`** | **Moteur de calcul PnL** : Calcule les plus/moins-values latentes et réalisées, le PRU et les performances. | `v1.8` |
| **`sl_audit_engine.py`** | **Moteur d'Audit Stop-Loss** : Vérifie la présence et la distance des ordres de protection (SL/TP) sur les positions ouvertes. | `v1.6` |
| **`crypto_trading.db`** | **Base SQLite Locale** : Stockage persistant des positions ouvertes/fermées, recommandations et logs d'audit. | Live |

### 🛠️ Outillage, Utilitaires & Fichiers Annexes

* **`auto_backfill_exchanges.py` / `activate_backfilled.py`** : Scripts de rattrapage d'historique des transactions.
* **`debug_majors.py` / `test_exchange_apis.py`** : Scripts de diagnostic technique (validations API, staking Kraken/Exodus).
* **`cloudflare`** : Binaire d'exécution Cloudflare Tunnel exposant le dashboard Streamlit de manière sécurisée en HTTPS.
* **`cron_crypto.log` / `cron_monitor.log` / `digest.log`** : Traces et journaux d'exécution des tâches planifiées.

---

## 3. Flux d'Information & Modèle d'Exécution
┌────────────────────────────────────────────────────────┐
                   │                 SOURCES DE DONNÉES LIVE                │
                   │  (Kraken Spot/Staking, OKX, MEXC, Exodus, API Prices)  │
                   └───────────────────────────┬────────────────────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ exchange_connectors.py  │
                                  └────────────┬────────────┘
                                               │
    ┌──────────────────────────────────────────┼──────────────────────────────────────────┐
    │                                          │                                          │
    ▼                                          ▼                                          ▼
┌───────────────────────┐              ┌───────────────────────┐              ┌───────────────────────┐
│  crypto_monitor.py    │              │reconciliation_engine.py│             │crypto_spec_advisor.py │
│ (Cron 24/7 /15 min)   │              └───────────┬───────────┘              │  (Daily Brief 19h)    │
└───────┬───────────────┘                          │                          └───────────┬───────────┘
│ (Alertes TP/SL)                          │ (Audit Ecarts)                       │ (Prompt Gemini)
▼                                          ▼                                      ▼
┌───────────────────────┐              ┌───────────────────────┐              ┌───────────────────────┐
│     Telegram Bot      │              │     Dashboard Web     │              │ Telegram Inline Buttons│
│   (Alertes Directes)  │              │  (Centre d'Alertes)   │              │   (Arbitrage 1-click) │
└───────┬───────────────┘              └───────────┬───────────┘              └───────────┬───────────┘
│                                          │                                      │
└──────────────────────────────────────────┼──────────────────────────────────────┘
│
▼
┌─────────────────────────┐
│   SQLite BDD Manager    │
│   (crypto_trading.db)   │
└─────────────────────────┘


---

## 4. Structure de la Base de Données (`crypto_trading.db`)

1. **`recommendations`** : Historique des opportunités d'arbitrage générées par l'IA.
   * `id`, `symbol`, `exchange`, `price`, `amount_eur`, `tp1_price`, `tp2_price`, `sl_price`, `sl_type`, `status` (`PENDING`, `TAKEN`, `IGNORED`), `created_at`.
2. **`trades`** : Suivi opérationnel des positions spéculatives ouvertes/fermées.
   * `id`, `symbol`, `exchange`, `entry_price`, `amount_eur`, `tp1_price`, `tp2_price`, `sl_price`, `sl_type`, `status` (`OPEN`, `CLOSED`), `step` (`ENTRY`, `TP1_DONE`, `TP2_DONE`), `taken_at`, `alert_status`.
3. **`reconciliation_logs`** : Historique des audits de réconciliation BDD vs Exchanges.

---

## 5. Automatisation & Infrastructure VM

* **Services Systemd :**
  * `streamlit.service` : Serveur Web Dashboard.
  * `cloudflare.service` : Tunnel sécurisé HTTPS.
  * `finary-bot.service` : Bot Telegram interactif.
* **Tâches Crontab :**
  * `*/15 * * * *` : Exécution de `crypto_monitor.py` (Surveillance 24/7 & alertes urgentes TP/SL).
  * `0 19 * * *` : Exécution de `crypto_speculative_advisor.py` (Rapport quotidien Telegram de 19h).
  * `0 3 * * *` : Backup automatique de `crypto_trading.db` vers `./save/`.
