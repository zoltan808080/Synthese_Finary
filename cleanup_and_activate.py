import sqlite3
import os

db_path = "crypto_trading.db"
if not os.path.exists(db_path):
    for f in os.listdir("."):
        if f.endswith(".db"):
            db_path = f
            break

print(f"📂 Utilisation de la base : {db_path}")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. Nettoyer les doublons en double (garder uniquement la dernière entrée par couple symbol + exchange pour les SUGGESTED)
cursor.execute("""
    DELETE FROM speculative_trades 
    WHERE status = 'SUGGESTED' 
    AND id NOT IN (
        SELECT MAX(id) FROM speculative_trades WHERE status = 'SUGGESTED' GROUP BY symbol, exchange
    );
""")
print(f"🧹 Doublons purgés.")

# 2. Activer les positions spéculatives en les passant au statut 'OPEN'
cursor.execute("""
    UPDATE speculative_trades 
    SET status = 'OPEN' 
    WHERE status = 'SUGGESTED' 
    AND symbol NOT IN ('SOL', 'ADA', 'LINK', 'UNI', 'ATOM', 'XRP');
""")
print(f"✅ {cursor.rowcount} positions activées au statut 'OPEN'.")

conn.commit()
conn.close()
