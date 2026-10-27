#!/usr/bin/env python3
import os
import re
import subprocess
import time
import requests
from dotenv import load_dotenv

# Chargement du .env
env_path = os.path.expanduser("~/Synthese_finary/.env")
load_dotenv(env_path)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

if not BOT_TOKEN or not USER_ID:
    print("❌ Erreur : Variables TELEGRAM_BOT_TOKEN ou TELEGRAM_ALLOWED_USER_ID introuvables dans le .env")
    exit(1)

try:
    # Récupération des logs systemd
    cmd = "journalctl -u cloudflare.service -n 50 --no-pager"
    log_output = subprocess.check_output(cmd, shell=True, text=True)

    # Extraction Regex corrigée
    urls = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log_output)

    if urls:
        latest_url = urls[-1]
        print(f"✅ URL détectée : {latest_url}")
        
        msg = f"🚀 *Nouveau lien Cockpit Crypto :*\n{latest_url}"
        res = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": USER_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
        if res.status_code == 200:
            print("📨 Notification Telegram envoyée avec succès !")
        else:
            print(f"⚠️ Échec envoi Telegram ({res.status_code}) : {res.text}")
    else:
        print("⚠️ Aucune URL trycloudflare.com trouvée dans les 50 dernières lignes de log.")

except subprocess.CalledProcessError:
    print("❌ Erreur : Droits insuffisants pour lire journalctl. Recommence avec `sudo`.")
except Exception as e:
    print(f"❌ Erreur inattendue : {e}")
