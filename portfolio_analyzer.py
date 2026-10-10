import os
import requests
import ccxt

# ==========================================
# CONFIGURATION ET CLASSIFICATION
# ==========================================

# 1. Cartographie élargie des dominantes (y compris Staking / Wrapped)
DOMINANTS_TICKERS = {
    "BTC", "ETH", "WBTC", "STETH", "CBETH", 
    "BETH", "ETH2", "CBBTC", "BTCB"
}

# 2. Liste des actifs Core / Majeurs non-spéculatifs
CORE_TICKERS = {
    "SOL", "ADA", "LINK", "AVAX", "ATOM", "UNI", 
    "NEAR", "DOT", "POL", "MATIC", "SUI", "APT",
    "USDT", "USDC", "DAI", "FDUSD"  # Stablecoins inclus dans le socle
}

# 3. Configuration des adresses On-Chain (Exodus / Cold Wallet)
EXODUS_WALLETS = {
    "BTC": os.getenv("EXODUS_BTC_ADDRESS", ""),
    "ETH": os.getenv("EXODUS_ETH_ADDRESS", "")
}

# API Keys Explorateurs On-Chain (optionnel mais recommandé pour ETH)
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")


# ==========================================
# 1.RÉCUPÉRATION DES SOLDES ON-CHAIN (EXODUS)
# ==========================================

def get_onchain_balances():
    """Récupère les soldes On-Chain (Exodus) via des API d'explorateurs publics."""
    balances = {}
    
    # Solde BTC via Blockstream API (gratuit sans clé)
    btc_addr = EXODUS_WALLETS.get("BTC")
    if btc_addr:
        try:
            res = requests.get(f"https://blockstream.info/api/address/{btc_addr}", timeout=10)
            if res.status_code == 200:
                data = res.json()
                stats = data.get("chain_stats", {})
                satoshis = stats.get("funded_txo_sum", 0) - stats.get("spent_txo_sum", 0)
                balances["BTC"] = satoshis / 1e8
        except Exception as e:
            print(f"[ERROR] Impossible de récupérer le solde BTC Exodus: {e}")

    # Solde ETH via Etherscan / Blockscout
    eth_addr = EXODUS_WALLETS.get("ETH")
    if eth_addr:
        try:
            url = f"https://api.etherscan.io/api?module=account&action=balance&address={eth_addr}&tag=latest"
            if ETHERSCAN_API_KEY:
                url += f"&apikey={ETHERSCAN_API_KEY}"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                result = res.json().get("result", "0")
                balances["ETH"] = float(result) / 1e18
        except Exception as e:
            print(f"[ERROR] Impossible de récupérer le solde ETH Exodus: {e}")

    return balances


# ==========================================
# 2. RÉCUPÉRATION CEX (SPOT + FUNDING / EARN)
# ==========================================

def get_cex_balances():
    """Interroge Kraken, OKX et MEXC en incluant les sous-comptes."""
    combined_balances = {}

    exchanges = {
        "kraken": ccxt.kraken({'apiKey': os.getenv('KRAKEN_KEY'), 'secret': os.getenv('KRAKEN_SECRET')}),
        "okx": ccxt.okx({'apiKey': os.getenv('OKX_KEY'), 'secret': os.getenv('OKX_SECRET'), 'password': os.getenv('OKX_PASSPHRASE')}),
        "mexc": ccxt.mexc({'apiKey': os.getenv('MEXC_KEY'), 'secret': os.getenv('MEXC_SECRET')})
    }

    for name, ex in exchanges.items():
        if not ex.apiKey:
            continue
        try:
            # 1. Solde Spot principal
            bal = ex.fetch_balance()
            for asset, amount in bal.get('total', {}).items():
                if amount and amount > 0:
                    asset_clean = asset.replace("LD", "").split(":")[0] # Nettoyage des préfixes Earn
                    combined_balances[asset_clean] = combined_balances.get(asset_clean, 0.0) + float(amount)
            
            # 2. Vérification des comptes Funding/Staking spécifiques si supporté
            if name == "okx" and hasattr(ex, 'private_get_asset_balances'):
                funding_bal = ex.private_get_asset_balances()
                for item in funding_bal.get('data', []):
                    asset = item['ccy']
                    avail = float(item['bal'])
                    if avail > 0:
                        combined_balances[asset] = combined_balances.get(asset, 0.0) + avail

        except Exception as e:
            print(f"[ERROR] Échec lors de la récupération des soldes sur {name}: {e}")

    return combined_balances


# ==========================================
# 3. VALORISATION ET CLASSIFICATION
# ==========================================

def analyze_portfolio():
    # Fusion des soldes CEX + On-Chain
    total_assets = get_cex_balances()
    onchain_assets = get_onchain_balances()
    
    for asset, qty in onchain_assets.items():
        total_assets[asset] = total_assets.get(asset, 0.0) + qty

    # Récupération des prix actuels via CoinGecko / Binance / CCXT
    # (Exemple simplifié de valorisation en EUR)
    ticker_prices = get_current_prices_eur(list(total_assets.keys()))

    dominants_val = 0.0
    core_val = 0.0
    speculative_val = 0.0
    total_val = 0.0

    for asset, qty in total_assets.items():
        price = ticker_prices.get(asset, 0.0)
        value_eur = qty * price
        total_val += value_eur

        # Classification stricte
        asset_upper = asset.upper()
        if asset_upper in DOMINANTS_TICKERS:
            dominants_val += value_eur
        elif asset_upper in CORE_TICKERS:
            core_val += value_eur
        else:
            speculative_val += value_eur

    # Calcul des pourcentages
    pct_dom = (dominants_val / total_val * 100) if total_val > 0 else 0
    pct_core = (core_val / total_val * 100) if total_val > 0 else 0
    pct_spec = (speculative_val / total_val * 100) if total_val > 0 else 0

    return {
        "total": total_val,
        "dominants": (dominants_val, pct_dom),
        "core": (core_val, pct_core),
        "speculative": (speculative_val, pct_spec)
    }

def get_current_prices_eur(symbols):
    """Fonction helper pour récupérer les prix Spot en Euros."""
    prices = {}
    try:
        # Requête groupée simple sur CoinGecko API
        res = requests.get("https://api.coingecko.com/api/v3/simple/price", params={
            "ids": "bitcoin,ethereum,solana,cardano,chainlink",
            "vs_currencies": "eur"
        }).json()
        # Mapping interne des tickers vers CoinGecko
        # (À compléter selon les besoins du script)
    except Exception:
        pass
    return prices
