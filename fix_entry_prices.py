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

# Met à jour price_entry en se basant sur tp1_price / 2 là où price_entry est à 0 ou NULL
cursor.execute("""
    UPDATE speculative_trades 
    SET price_entry = tp1_price / 2.0 
    WHERE (price_entry = 0 OR price_entry IS NULL) 
    AND tp1_price > 0;
""")

updated_count = cursor.rowcount
conn.commit()
conn.close()

print(f"✅ {updated_count} prix d'entrée corrigés automatiquement via le ratio du TP1 !")
