from db_manager import init_db, get_open_positions

init_db()
positions = get_open_positions()

print(f"\n📊 Total des positions actives suivies en BDD : {len(positions)}\n")
print(
    f"{'ID':<4} | {'SYMBOLE':<8} | {'EXCHANGE':<8} | {'PRIX ENTRÉE':<12} |"
    f" {'MONTANT (€)':<12} | {'TP1 (x2)':<10} | {'SL':<10}"
)
print("-" * 75)

for p in positions:
  # Sécurité pour éviter les plantages si une valeur est None en BDD
  p_price = float(p[3]) if p[3] is not None else 0.0
  p_amount = float(p[4]) if p[4] is not None else 0.0
  p_tp1 = float(p[5]) if len(p) > 5 and p[5] is not None else 0.0
  p_sl = float(p[7]) if len(p) > 7 and p[7] is not None else 0.0

  print(
      f"{str(p[0]):<4} | {str(p[1]):<8} | {str(p[2]):<8} |"
      f" {p_price:<12.4f} | {p_amount:<12.2f} |"
      f" {p_tp1:<10.4f} | {p_sl:<10.4f}"
  )
print("-" * 75)
