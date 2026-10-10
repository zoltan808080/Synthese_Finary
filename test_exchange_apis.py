import os
import re
import time
import datetime
import hmac
import hashlib
import base64
import requests
import urllib.parse

from dotenv import load_dotenv

load_dotenv()

def test_kraken():
    print("\n--- 🐙 TEST API KRAKEN ---")
    key = os.getenv("KRAKEN_API_KEY")
    secret = os.getenv("KRAKEN_API_SECRET")
    if not key or not secret:
        print("❌ Clés KRAKEN_API_KEY ou KRAKEN_API_SECRET absentes dans le .env")
        return

    url_path = "/0/private/Balance"
    nonce = str(int(time.time() * 1000))
    postdata = f"nonce={nonce}"
    encoded = (nonce + postdata).encode()
    message = url_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    sigdigest = base64.b64encode(mac.digest()).decode()

    headers = {"API-Key": key, "API-Sign": sigdigest}
    try:
        res = requests.post(f"https://api.kraken.com{url_path}", data={"nonce": nonce}, headers=headers, timeout=8)
        print(f"Status HTTP : {res.status_code}")
        print(f"Réponse brute : {res.text}")
    except Exception as e:
        print(f"❌ Erreur réseau Kraken : {e}")

def test_okx():
    print("\n--- 🟦 TEST API OKX (Sanitisation stricte) ---")
    
    # Extraction et nettoyage strict (Garde uniquement a-z, A-Z, 0-9 et guillemets/tirets)
    raw_key = os.getenv("OKX_API_KEY", "")
    raw_secret = os.getenv("OKX_API_SECRET", "")
    raw_passphrase = os.getenv("OKX_PASSPHRASE", "")

    key = re.sub(r'[^a-zA-Z0-9-]', '', raw_key)
    secret = re.sub(r'[^a-zA-Z0-9-]', '', raw_secret)
    passphrase = re.sub(r'[^a-zA-Z0-9-_@!]', '', raw_passphrase)

    if not key or not secret or not passphrase:
        print("❌ Clés manquantes ou invalides après nettoyage.")
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    method = "GET"
    request_path = "/api/v5/account/balance"
    message = timestamp + method + request_path

    mac = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), digestmod=hashlib.sha256)
    sign = base64.b64encode(mac.digest()).decode('utf-8')

    headers = {
        "OK-ACCESS-KEY": key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json"
    }

    print(f"Clé nettoyée : {key}")
    print(f"Longueur : {len(key)} caractères")

    try:
        worker_url = "https://okx-proxy-eu.vercel.app/api/v5/account/balance"
        res = requests.get(worker_url, headers=headers, timeout=8)
        #res = requests.get(f"https://www.okx.com{request_path}", headers=headers, timeout=8)
        print(f"Status HTTP : {res.status_code}")
        print(f"Réponse brute : {res.text}")
    except Exception as e:
        print(f"❌ Erreur réseau OKX : {e}")


def test_mexc():
    print("\n--- 🟩 TEST API MEXC ---")
    raw_key = os.getenv("MEXC_API_KEY", "")
    raw_secret = os.getenv("MEXC_API_SECRET", "")

    # Supprime espaces, retours à la ligne (\r/\n) et guillemets parasites
    key = raw_key.strip().strip('"').strip("'")
    secret = raw_secret.strip().strip('"').strip("'")

    if not key or not secret:
        print("❌ Clés MEXC_API_KEY ou MEXC_API_SECRET absentes dans le .env")
        return

    timestamp = int(time.time() * 1000)

    # Paramètres avec recvWindow (recommandé par MEXC)
    params = {"timestamp": timestamp, "recvWindow": 60000}

    query_string = urllib.parse.urlencode(params)
    signature = hmac.new(
        secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers = {"X-MEXC-APIKEY": key, "Content-Type": "application/json"}

    try:
        url = f"https://api.mexc.com/api/v3/account?{query_string}&signature={signature}"
        res = requests.get(url, headers=headers, timeout=8)
        print(f"Status HTTP : {res.status_code}")
        print(f"Réponse brute : {res.text}")
    except Exception as e:
        print(f"❌ Erreur réseau MEXC : {e}")

if __name__ == "__main__":

    test_kraken()
    test_okx()
    test_mexc()
