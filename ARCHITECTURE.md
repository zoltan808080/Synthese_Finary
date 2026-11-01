# 📐 Dossier d'Architecture Technique — Cockpit Crypto & Moonbag

**Version du système :** `v3.2.1`  
**Dernière mise à jour :** 1er novembre 2026  
**Dépôt Git :** `origin/main` (`zoltan808080/Synthese_Finary`)  
**Environnement :** VM Linux Debian (`finary-digest-server`)

---

## 1. Vue d'ensemble & Philosophie Système

Le **Cockpit Crypto & Moonbag** est une plateforme serveur-client autonome d'agrégation multi-exchanges, de réconciliation comptable temps réel, d'analyse quanti-microstructure, de pilotage spéculatif, d'arbitrage macro-patrimonial et de gestion dynamique du risque pilotée par Intelligence Artificielle (Google Gemini).

Le système élimine la dépendance aux agrégateurs tiers pour le suivi en direct et applique la stratégie **Moonbag** avec une rigueur institutionnelle :
* **Prise de profit automatique (TP1 à x2 / TP2 à x4)** avec sécurisation en *Free Ride* (remboursement du capital initial).
* **Gestion stricte du risque** via Stop-Loss (invalidation technique ou niveau de prix) et ajustement automatique en *Break-Even* (BE).
* **Isolation stricte des poches d'investissement** : Majeures (BTC/ETH), CORE (conviction long terme), Spéculatif (Moonbags actifs sous Stop-Loss) et Cash (fiat & stablecoins).
* **Suivi comptable certifié FIFO (First-In, First-Out)** pour la comptabilisation des plus-values réalisées.

---

## 2. Principes Fondateurs & Choix d'Architecture

1. **Source Unique de Vérité (SSOT) & Anti-Doublon :**
   * Interrogation directe des API d'exchanges (Kraken Spot & Staking, OKX Europe/Vercel Proxy, MEXC) et des wallets froids (Exodus).
   * Isolation étanche dans la transmission du contexte à l'IA : la fonction `get_formatted_core_and_majors_breakdown` exclut automatiquement tous les symboles enregistrés en BDD spéculative (`open_pos`) pour empêcher toute confusion d'appartenance entre la poche CORE et la poche Spéculative.
2. **Architecture Prompts Externalisés SSOT (`prompts/`) :**
   * Séparation stricte de la logique métier (Python/Streamlit) et des consignes d'expertise IA. Les instructions système sont stockées sous forme de fichiers Markdown réutilisables dans le dossier `prompts/` et éditables à chaud depuis l'interface d'administration.
3. **Centralisation & Résilience de l'IA (`gemini_manager.py`) :**
   * Wrapper unifié gérant la liste de priorité des modèles Gemini (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-flash`), les tentatives (*retries*), les temporisations (*timeouts*) et la bascule automatique (*fallback*) en cas d'erreur API (429 / 503 / Surchauffe).
4. **Calcul Microstructure de Marché (`funding_oi_engine.py` & `rsi_engine.py`) :**
   * Analyse synchrone des Funding Rates (taux de financement 8h) et de l'Open Interest (OI) sur les dérivés (OKX Swaps) combinée à une heatmap RSI multi-timeframe (15m, 1h, 4h, 1D) pour anticiper les *Squeezes* (Long/Short) et repérer les zones de surchauffe.
5. **Principe Cash-Neutral Strict & Ajustement Dynamique :**
   * Tout achat recommandé doit être financé par les liquidités disponibles. La cible Cash de base (13 %) est ajustée dynamiquement selon l'indice *Fear & Greed* et le *Circuit Breaker BTC*.
6. **Réconciliation Continu de Portefeuille (`reconciliation_engine.py`) :**
   * Audit automatique entre la réalité des comptes sur les exchanges et les enregistrements SQLite pour identifier les positions fantômes, les écarts de volume et les nouveaux trades manuels (< 48h).

---

## 3. Cartographie Complète des Modules & Fichiers

```
Synthese_Finary/
├── dashboard.py                       # Interface Web Streamlit (Cockpit v3.2.1)
├── gemini_manager.py                  # Wrapper centralisé API Gemini avec Fallback
├── prompt_loader.py                   # Moteur de chargement des prompts SSOT Markdown
├── auto_tpsl_engine.py                # Moteur d'ajustement dynamique TP/SL (IA & Microstructure)
├── crypto_monitor.py                  # Sentinelle de surveillance 24/7 (Cron 15 min)
├── crypto_speculative_advisor.py      # Générateur du Daily Briefing 19h Telegram
├── reconciliation_engine.py           # Moteur d'audit BDD vs Exchanges
├── funding_oi_engine.py               # Collecteur Microstructure (Funding Rates & Open Interest)
├── rsi_engine.py                      # Calculateur Heatmap RSI Multi-Timeframe
├── pnl_engine.py                      # Calculateur PnL FIFO, Win Rate & Metrics
├── exchange_connectors.py             # Connecteurs API Kraken, OKX, MEXC, Exodus
├── db_manager.py                      # Couche d'accès et migrations SQLite
├── telegram_finary_bot.py             # Bot Telegram interactif (Commandes & Callbacks)
├── config/
│   └── gemini_config.json             # Configuration JSON de priorité des modèles Gemini
├── prompts/                           # Prompts Système SSOT (Markdown)
│   ├── advisor_speculative.md         # Consignes Rapport Spéculatif 19h
│   ├── risk_manager.md                # Consignes Audit de Sécurité & Risk Management
│   ├── macro_arbitrage.md             # Consignes Stratégie & Rééquilibrage Macro
│   ├── project_rules.md               # Règles fondamentales & Feuille de route
│   └── auto_tpsl.md                   # Directive de recalibrage automatique TP/SL
├── crypto_trading.db                  # Base de données SQLite de production
└── save/                              # Dossier des sauvegardes automatiques BDD (.db)
```

### 🎛️ Détail des Composants Clés

| Fichier / Module | Rôle & Responsabilité | Version |
| :--- | :--- | :--- |
| **`dashboard.py`** | **Interface utilisateur Streamlit** : Cockpit 7 onglets (Positions Live, Microstructure, Daily Briefing, Alertes, Assistant IA SSOT, Monitoring Systemd/Prompts, Export Fiscal). Gère la parallélisation multi-threading du fetch des prix. | `v3.2.1` |
| **`gemini_manager.py`** | **Wrapper Unifié Gemini** : Gère la chaîne de fallback des modèles Gemini, intercepte les timeouts et retourne la réponse ainsi que le nom du modèle réellement utilisé. | `v1.0` |
| **`prompt_loader.py`** | **Chargeur SSOT** : Charge les prompts `.md` du dossier `prompts/`. Gère également les sauvegardes automatiques (`.bak`) et les restaurations d'urgence. | `v1.0` |
| **`auto_tpsl_engine.py`** | **Ajusteur TP/SL Intelligente** : Exécuté toutes les 8h ou à la demande. Ajuste les paliers des positions en mode `Auto IA` selon le RSI 4h/1D, la microstructure et l'avis Gemini. | `v3.2` |
| **`crypto_monitor.py`** | **Daemon de Surveillance 24/7** : Scanne les prix toutes les 15 min via Crontab. Déclenche les alertes Telegram urgentes (TP1, TP2, SL, Time-Stop 21j) et fait évoluer les étapes de trade (`ENTRY` ➔ `TP1_DONE`). | `v1.8.5` |
| **`funding_oi_engine.py`** | **Screener Dérivés** : Analyse le marché OKX Swap (Funding Rate 8h / Annuel, Open Interest) pour qualifier le biais de marché (Sur-achat Longs vs Short Squeeze). | `v1.0` |
| **`pnl_engine.py`** | **Moteur Comptable FIFO** : Maintient le registre des plus-values réalisées, du Profit Factor, du Win Rate % et génère le journal certifié dans `closed_trades`. | `v3.0` |
| **`reconciliation_engine.py`** | **Audit de Cohérence** : Détecte les anomalies entre la BDD SQLite et les soldes réels des API d'exchanges (`GHOST_POSITION`, `VOLUME_MISMATCH`, `MANUAL_TRADE_DETECTED`). | `v3.0` |
| **`exchange_connectors.py`** | **Couche d'Ingestion API** : Connecteur multi-sources unifié (Kraken Spot, Kraken Staking `ETH2.S`, OKX Europe / Vercel Proxy, MEXC, Exodus Cold Wallet). | `v3.0` |
| **`db_manager.py`** | **Accès SQLite & Schéma** : Gère les connexions SQLite, les requêtes CRUD, les verrous et l'auto-migration des colonnes (`auto_tp_sl`, `sl_type`, `step`). | `v3.1` |
| **`telegram_finary_bot.py`** | **Interface Telegram Mobile** : Commandes interactives (`/status`, `/positions`, `/pnl`, `/funding`, `/rsi`, `/audit`) et gestionnaires de boutons 1-Click (`Break-Even`, `Clôturer`). | `v3.0` |

---

## 4. Diagramme de Flux & Architecture Système

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              SOURCES DE DONNÉES LIVE                                   │
│  (Kraken Spot/Staking, OKX Europe/Proxy, MEXC, Exodus Wallet, API Funding/OI, Prices) │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
                               ┌─────────────────────────┐
                               │ exchange_connectors.py  │
                               └────────────┬────────────┘
                                            │
       ┌────────────────────────────────────┼────────────────────────────────────┐
       ▼                                    ▼                                    ▼
┌───────────────────────┐        ┌───────────────────────┐          ┌───────────────────────┐
│  crypto_monitor.py    │        │reconciliation_engine  │          │ funding_oi_engine.py  │
│ (Cron 24/7 /15 min)   │        └───────────┬───────────┘          │(Microstructure Mkt)   │
└───────┬───────────────┘                    │                      └───────────┬───────────┘
        │ (TP/SL Alerts)                     │ (Audit Écarts)                   │
        ▼                                    ▼                                  │
┌───────────────────────┐        ┌───────────────────────┐                      │
│  telegram_finary_bot  │        │     Dashboard Web     │◄─────────────────────┤
│ (Commandes & Alerts)  │        │  (Streamlit v3.2.1)   │                      │
└───────┬───────────────┘        └───────────┬───────────┘                      │
        │                                    │                                  │
        └──────────────────┬─────────────────┘                                  │
                           │                                                    │
                           ▼                                                    ▼
                ┌───────────────────────┐                            ┌───────────────────────┐
                │     prompt_loader     │                            │  auto_tpsl_engine.py  │
                │  (Architecture SSOT)  │                            │ (Recalibrage IA 8h)   │
                └───────────┬───────────┘                            └───────────┬───────────┘
                            │                                                    │
                            └──────────────────────┬─────────────────────────────┘
                                                   │
                                                   ▼
                                        ┌───────────────────────┐
                                        │   gemini_manager.py   │◄─── config/gemini_config.json
                                        │ (Fallback & Resil.)   │
                                        └──────────┬────────────┘
                                                   │
                                                   ▼
                                        ┌───────────────────────┐
                                        │   db_manager.py       │
                                        │  (crypto_trading.db)  │
                                        └───────────────────────┘
```

---

## 5. Modèle de Données SQLite (`crypto_trading.db`)

### Table `speculative_trades` (Positions Actives & Historique Court Terme)
| Colonne | Type | Description / Contraintes |
| :--- | :--- | :--- |
| `id` | `INTEGER` | Clé primaire auto-incrémentée. |
| `symbol` | `TEXT` | Ticker de la cryptomonnaie (ex: `SOL`, `NEAR`, `ONDO`). |
| `exchange` | `TEXT` | Nom de la plateforme (`KRAKEN`, `OKX`, `MEXC`). |
| `price_suggested`| `REAL` | Prix initial détecté lors de la recommandation. |
| `price_entry` | `REAL` | PRU réel d'exécution de la position en Euros. |
| `amount_eur` | `REAL` | Capital investi initialement sur la position (€). |
| `tp_price` | `REAL` | Palier générique de Take-Profit. |
| `tp1_price` | `REAL` | Palier de premier Take-Profit (Prise de profit 50 % @ x2). |
| `tp2_price` | `REAL` | Palier de second Take-Profit (TP final @ x4). |
| `sl_price` | `REAL` | Niveau de prix de Stop-Loss (€). |
| `sl_type` | `TEXT` | Type de protection (`invalidation`, `hard_sl`, `trailing`). |
| `status` | `TEXT` | État opérationnel (`OPEN`, `CLOSED`, `IGNORED`). |
| `alert_status` | `TEXT` | Suivi des notifications émises. |
| `step` | `TEXT` | Étape du trade (`ENTRY`, `TP1_DONE`, `TP2_DONE`, `SL_ALERTED`). |
| `auto_tp_sl` | `INTEGER` | Mode de gestion (`0` = Manuel, `1` = Auto IA recalibré par Cron). |
| `created_at` | `TIMESTAMP`| Date de création de la recommandation. |
| `taken_at` | `TIMESTAMP`| Date de confirmation d'ouverture par l'utilisateur. |
| `closed_at` | `TIMESTAMP`| Date de clôture définitive de la position. |

### Table `closed_trades` (Registre FIFO & Journal Comptable Immuable)
| Colonne | Type | Description / Contraintes |
| :--- | :--- | :--- |
| `id` | `INTEGER` | Clé primaire auto-incrémentée. |
| `trade_id` | `INTEGER` | Clé étrangère liant à `speculative_trades.id`. |
| `symbol` | `TEXT` | Ticker de la cryptomonnaie. |
| `exchange` | `TEXT` | Plateforme d'exécution. |
| `entry_price` | `REAL` | Prix d'achat unitaire (€). |
| `exit_price` | `REAL` | Prix de vente unitaire réel (€). |
| `amount_eur` | `REAL` | Capital total engagé (€). |
| `pnl_eur` | `REAL` | Gain ou perte nette réalisée en Euros ($\text{Valeur Sortie} - \text{Valeur Entrée}$). |
| `pnl_pct` | `REAL` | Performance en pourcentage ($\frac{\text{Prix Sortie} - \text{Prix Entrée}}{\text{Prix Entrée}} \times 100$). |
| `opened_at` | `TIMESTAMP`| Date/Heure d'ouverture initiale. |
| `closed_at` | `TIMESTAMP`| Date/Heure d'exécution de la clôture. |
| `close_reason` | `TEXT` | Raison de la fermeture (`TP1_FREE_RIDE`, `SL_TRIGGERED`, `MANUAL_CLOSE`, `MACRO_REBALANCING`). |

### Table `ignored_manual_trades` (Filtre d'Exclusion CORE)
| Colonne | Type | Description |
| :--- | :--- | :--- |
| `id` | `INTEGER` | Clé primaire. |
| `symbol` | `TEXT` | Ticker de l'actif conservé hors spéculation. |
| `exchange` | `TEXT` | Exchange d'hébergement. |
| `cost_eur` | `REAL` | Montant d'achat (€). |
| `price_eur` | `REAL` | Prix d'entrée (€). |
| `reason` | `TEXT` | Statut attribué (`CORE`, `STAKING`, `PASSIVE`). |
| `created_at` | `TIMESTAMP`| Date de classification. |

---

## 6. Cadre Strategique d'Allocation & Risk Management

### 📊 Grille Cible des Poches de Capital
1. **Majeures (BTC / ETH) — Cible : 55 %**
   * Réserve de valeur patrimoniale long terme.
   * Sert de réservoir de liquidité prioritaire pour recapitaliser la poche Cash lors des surchauffes de marché.
2. **Poche CORE (Altcoins Conviction) — Cible : 22 %**
   * Sélection sélective des piliers d'infrastructure et protocoles générateurs de revenus (ex: SOL, LINK, BNB, SUI, ONDO).
   * **Plafond de concentration strict :** Aucun actif unique ne doit dépasser 35 % de la valeur totale de la poche CORE.
   * **Dégradation / Migration :** Tout actif CORE dont la thèse fondamentale s'effrite est dégradé vers la poche Spéculative (imposant l'application immédiate d'un Stop-Loss en BDD).
3. **Poche Spéculative (Moonbags & Opportunities) — Cible : 10 % (Seuil de Tolérance Max : 12 %)**
   * Actifs à haute volatilité gérés exclusivement avec des Stop-Loss stricts et des Take-Profits configurés.
   * Tout dépassement au-delà de 12 % impose un gel des nouveaux achats et la programmation d'allègements prioritaires.
4. **Cash & Stablecoins (Réserve Stratégique) — Cible de Base : 13 %**
   * Composé du Fiat réel (EUR/USD sur exchanges) et des stablecoins (USDT/USDC).
   * **Ajustement Dynamique selon le Fear & Greed Index :**
     * Si *Fear & Greed* < 25 (Peur Extrême) ➔ Cible abaissée temporairement à 8–10 % pour déployer du capital sur les dips.
     * Si *Fear & Greed* > 75 (Euphorie) ➔ Cible relevée temporairement à 18–20 % par des ventes défensives de sécurisation.

---

## 7. Moteur d'IA & Architecture SSOT Prompts

### 🤖 Chaîne de Résilience Gemini (`gemini_manager.py`)
L'application interroge l'API Gemini via un algorithme de basculement progressif défini dans `config/gemini_config.json` :

```json
{
  "models_priority": [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro"
  ],
  "timeout_seconds": 45,
  "max_retries_per_model": 1
}
```

```text
[Demande Analyse IA]
         │
         ▼
 ┌───────────────┐      Échec / Timeout
 │ Model 1 (2.5) ├──────────────────────────┐
 └───────┬───────┘                          │
         │ Succès                           ▼
         │                         ┌────────────────┐
         │                         │ Model 2 (2.0)  ├───────────┐
         │                         └───────┬────────┘           │
         │                                 │ Succès             ▼
         │                                 │           ┌────────────────┐
         │                                 │           │ Model 3 (1.5)  │
         │                                 │           └───────┬────────┘
         ▼                                 ▼                   ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │               Retour de la Réponse + Badge Modèle                    │
 └──────────────────────────────────────────────────────────────────────┘
```

### 📜 Administration des Prompts SSOT (`prompts/`)
* **`advisor_speculative.md`** : Pilotage du rapport quotidien de 19h (Screener d'opportunités spéculatives, règles Moonbag, Cash-Neutral strict).
* **`risk_manager.md`** : Audit de protection des positions ouvertes (Détection des SL manquants, calcul de la distance au SL, alertes de sur-exposition).
* **`macro_arbitrage.md`** : Audit qualitatif de la poche CORE, calcul des écarts aux cibles, vérification du plafond de 35 % intra-CORE, reclassement/migration d'actifs et arbitrages chiffrés.
* **`auto_tpsl.md`** : Consignes d'ajustement dynamique des paliers TP/SL exécutées toutes les 8h par le cron `auto_tpsl_engine.py`.
* **`project_rules.md`** : Règles fondamentales et feuille de route pas-à-pas pour les arbitrages sur les différentes plateformes.

---

## 8. Automatisation, Systemd & Crontab VM

Le système tourne 24/7 de manière autonome sur une machine virtuelle Linux Debian via des services Systemd isolés et un planning Crontab.

### ⚙️ Services Systemd

#### 1. `streamlit.service` (Dashboard Web)
```ini
[Unit]
Description=Streamlit Crypto Cockpit & Analytics
After=network.target

[Service]
User=finary
WorkingDirectory=/home/finary/Synthese_Finary
ExecStart=/home/finary/Synthese_Finary/venv/bin/streamlit run dashboard.py --server.port 8501 --server.address 127.0.0.1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 2. `cloudflare.service` (Tunnel Sécurisé HTTPS)
```ini
[Unit]
Description=Cloudflare Tunnel for Crypto Cockpit
After=network.target

[Service]
User=finary
ExecStart=/usr/local/bin/cloudflared tunnel run crypto-cockpit
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 3. `finary-bot.service` (Bot Telegram Interactif)
```ini
[Unit]
Description=Telegram Bot API Interactive Service
After=network.target

[Service]
User=finary
WorkingDirectory=/home/finary/Synthese_Finary
ExecStart=/home/finary/Synthese_Finary/venv/bin/python3 telegram_finary_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

### ⏰ Planning Crontab (`crontab -e`)

```cron
# -------------------------------------------------------------------
# PLANIFICATION DES TÂCHES AUTOMATISÉES - COCKPIT CRYPTO v3.2.1
# -------------------------------------------------------------------

# 1. Sentinelle de surveillance 24/7 (Exécution toutes les 15 minutes)
# Verifie les cours en direct, declenche les alertes d'urgence TP/SL et fait evoluer les etapes BDD.
*/15 * * * * cd /home/finary/Synthese_Finary && ./venv/bin/python3 crypto_monitor.py >> logs/cron_monitor.log 2>&1

# 2. Moteur d'Ajustement Automatique TP/SL IA (Toutes les 8 heures : 00h00, 08h00, 16h00)
# Alignee sur la cloture des Funding Rates 8h pour ajuster dynamiquement les paliers Auto.
0 0,8,16 * * * cd /home/finary/Synthese_Finary && ./venv/bin/python3 auto_tpsl_engine.py >> logs/cron_autotpsl.log 2>&1

# 3. Rapport d'Arbitrage & Daily Briefing Quotidien (Tous les jours a 19h00)
# Genere le bilan de 19h et envoie le rapport interactif Telegram avec boutons d'action.
0 19 * * * cd /home/finary/Synthese_Finary && ./venv/bin/python3 crypto_speculative_advisor.py >> logs/cron_advisor.log 2>&1

# 4. Sauvegarde automatique quotidienne de la Base de Données SQLite (Tous les jours a 03h00)
0 3 * * * cp /home/finary/Synthese_Finary/crypto_trading.db /home/finary/Synthese_Finary/save/crypto_trading_$(date +\%Y\%m\%d_\%H\%M\%S).db
```

---

## 9. Procédures de Sécurité, Backup & Reprise d'Activité

1. **Gestion des Secrets & Variables d'Environnement :**
   * Toutes les clés API d'exchanges (Kraken, OKX, MEXC), les tokens Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`), la clé API Google Gemini (`GEMINI_API_KEY`) et le mot de passe du cockpit (`STREAMLIT_PASSWORD`) sont isolés dans le fichier sécurisé `.env` (chmod `600`).
2. **Stratégie de Sauvegarde BDD (`.db`) :**
   * Sauvegarde quotidienne automatisée par cron dans le répertoire `./save/` avec horodatage ISO (`crypto_trading_YYYYMMDD_HHMMSS.db`).
   * Rotation automatique conservant les 30 derniers points de restauration.
3. **Sauvegarde et Restauration des Prompts (`.bak`) :**
   * À chaque modification effectuée depuis l'interface d'administration Web (`dashboard.py` Onglet 6), `prompt_loader.py` crée une copie de secours `.bak` du fichier Markdown modifié.
   * La restauration d'un prompt antérieur se fait en 1-Click via le bouton *Restaurer Backup (.bak)*.
4. **Procédure de Récupération après Crash VM :**
   ```bash
   # Clonage du depôt et deplacement
   git clone https://github.com/zoltan808080/Synthese_Finary.git
   cd Synthese_Finary

   # Restauration du fichier de configuration .env et de la derniere BDD SQLite
   cp /path/to/backup/.env .env
   cp /path/to/backup/save/crypto_trading_latest.db crypto_trading.db

   # Installation de l'environnement virtuel Python
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

   # Redémarrage des services systemd
   sudo systemctl restart streamlit.service finary-bot.service cloudflare.service
   ```