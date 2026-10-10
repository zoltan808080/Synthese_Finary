import logging


def audit_positions_protection(open_positions, open_orders):
  """Croise les positions Spot ouvertes avec les ordres en attente pour identifier

  les actifs non protégés par un Stop-Loss ou Take-Profit.
  """
  protected_symbols = set()
  for order in open_orders:
    pair = str(order.get("pair", "")).upper()
    order_type = str(order.get("ordertype", "")).lower()
    side = str(order.get("type", "")).lower()

    if side == "sell" or any(
        k in order_type
        for k in ["sell", "stop", "take_profit", "conditional", "trigger", "oco"]
    ):
      protected_symbols.add(pair)

  unprotected = []
  protected = []

  for pair, info in open_positions.items():
    qty = info.get("qty", 0)
    if qty <= 0:
      continue

    pair_clean = pair.replace("-", "").replace("/", "").upper()

    # Détection de correspondance de paire (ex: NEAR-USDC / NEARUSDC)
    is_covered = any(
        prot.replace("-", "").replace("/", "").upper() in pair_clean
        or pair_clean in prot.replace("-", "").replace("/", "").upper()
        for prot in protected_symbols
    )

    item = {"pair": pair, "qty": qty, "pru": info.get("pru", 0.0)}

    if is_covered:
      protected.append(item)
    else:
      unprotected.append(item)

  return {
      "total_open_positions": len(protected) + len(unprotected),
      "protected_count": len(protected),
      "unprotected_count": len(unprotected),
      "unprotected_positions": unprotected,
      "protected_positions": protected,
  }


def format_audit_telegram_message(audit_res):
  if audit_res["total_open_positions"] == 0:
    return "🛡️ *AUDIT SL/TP* : Aucune position ouverte détectée."

  msg = (
      f"🛡️ *AUDIT SL/TP EN TEMPS RÉEL (v1.6)*\n"
      f"----------------------------------------\n"
      f"📊 Positions ouvertes : `{audit_res['total_open_positions']}`\n"
      f"✅ Protégées (SL/TP actifs) : `{audit_res['protected_count']}`\n"
      f"⚠️ *SANS PROTECTION* : `{audit_res['unprotected_count']}`\n"
  )

  if audit_res["protected_positions"]:
    msg += "\n✅ *POSITIONS PROTÉGÉES (SL/TP)* :\n"
    for pos in audit_res["protected_positions"]:
      msg += f"• 🟢 `{pos['pair']}` (Qté: `{pos['qty']:.4f}`)\n"

  if audit_res["unprotected_positions"]:
    msg += "\n🚨 *ALERTE : POSITIONS NON PROTÉGÉES* 🚨\n"
    for pos in audit_res["unprotected_positions"]:
      msg += f"• 🔴 `{pos['pair']}` (Qté: `{pos['qty']:.4f}` @ PRU `{pos['pru']:.4f}`)\n"

  return msg
