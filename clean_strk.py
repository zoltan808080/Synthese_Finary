import sqlite3
import os

DB_PATH = os.path.expanduser("~/Synthese_finary/crypto_trading.db")

def remove_duplicate_strk():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # On cible l'ID 45 (le doublon STRK sur OKX) pour le basculer en statut CLOSED
    target_id = 45
    
    cursor.execute("""
        UPDATE speculative_trades 
        SET status = 'CLOSED', step = 'CLOSED', closed_at = CURRENT_TIMESTAMP 
        WHERE id = ?
    """, (target_id,))
    
    conn.commit()
    conn.close()
    print(f"✅ La ligne doublon STRK (ID {target_id}) a été clôturée avec succès en base de données.")

if __name__ == "__main__":
    remove_duplicate_strk()
