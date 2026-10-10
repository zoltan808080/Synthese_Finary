import sqlite3
import os

db_path = "crypto_trading.db"
if not os.path.exists(db_path):
    for f in os.listdir("."):
        if f.endswith(".db"):
            db_path = f
            break

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT id, symbol, exchange, status, amount_eur FROM speculative_trades;")
rows = cursor.fetchall()

print(f"\n📋 Liste de toutes les lignes dans speculative_trades (Total : {len(rows)}) :\n")
print(f"{'ID':<4} | {'SYMBOLE':<8} | {'EXCHANGE':<8} | {'STATUT ACTUEL':<12} | {'MONTANT (€)'}")
print("-" * 55)
for r in rows:
    print(f"{str(r[0]):<4} | {str(r[1]):<8} | {str(r[2]):<8} | {str(r[3]):<12} | {str(r[4])}")
print("-" * 55)

conn.close()
