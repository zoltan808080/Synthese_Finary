import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

# Recherche du fichier de base de données SQLite
db_path = "crypto_trading.db"
if not os.path.exists(db_path):
    for f in os.listdir("."):
        if f.endswith(".db"):
            db_path = f
            break

print(f"📂 Utilisation de la base de données : {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Afficher la structure de la table speculative_trades pour vérification
cursor.execute("PRAGMA table_info(speculative_trades);")
columns = [col[1] for col in cursor.fetchall()]
print(f"📋 Colonnes de la table speculative_trades : {columns}")

# Mettre à jour le statut des lignes en attente vers un statut actif (ex: 'ENTRY' ou 'OPEN')
try:
    # On regarde si la colonne 'status' existe
    if "status" in columns:
        cursor.execute("UPDATE speculative_trades SET status = 'ENTRY' WHERE status = 'PENDING' OR status IS NULL OR status = 'IGNORED';")
        conn.commit()
        print(f"✅ {cursor.rowcount} positions basculées en statut actif (ENTRY) dans speculative_trades !")
    else:
        print("⚠️ La colonne 'status' n'existe pas dans speculative_trades.")
except Exception as e:
    print(f"⚠️ Erreur lors de la mise à jour : {e}")

conn.close()
