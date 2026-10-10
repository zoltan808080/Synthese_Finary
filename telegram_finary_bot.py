import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from crypto_monitor import get_live_price
from daily_digest_engine import build_current_snapshot, init_finary_session
from db_manager import (
    close_trade,
    get_open_positions,
    mark_trade_ignored,
    mark_trade_taken,
)
import exchange_connectors as ec
import pnl_engine as pe
import sl_audit_engine as sa

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


async def post_init(application):
  """Enregistre le menu déroulant natif de commandes dans Telegram."""
  commands = [
      ("positions", "📊 Trades spéculatifs suivis en BDD & PnL direct"),
      ("pnl", "📈 Bilan PnL global, Win Rate & frais API"),
      ("audit", "🛡️ Audit de couverture Stop-Loss / Take-Profit"),
      ("help", "📖 Guide complet d'utilisation"),
      ("start", "👋 Relancer l'accueil"),
  ]
  await application.bot.set_my_commands(commands)


async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  """Handler pour la commande /pnl dans Telegram."""
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  await update.message.reply_text("🔄 Calcul du PnL global en cours...")

  try:
    trades = await asyncio.to_thread(ec.get_all_trades_history)
    pnl_summary = pe.calculate_pnl(trades)
    msg = pe.format_pnl_telegram_message(pnl_summary)

    await update.message.reply_text(msg, parse_mode="Markdown")
  except Exception as e:
    await update.message.reply_text(f"❌ Erreur lors du calcul PnL : {str(e)}")


async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  """Handler pour la commande /audit dans Telegram (Audit SL/TP v1.6)."""
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  await update.message.reply_text(
      "🔄 Audit de la couverture SL/TP en cours..."
  )

  try:
    trades = await asyncio.to_thread(ec.get_all_trades_history)
    pnl_summary = pe.calculate_pnl(trades)
    open_orders = await asyncio.to_thread(ec.get_all_open_orders)

    audit_res = sa.audit_positions_protection(
        pnl_summary["open_positions"], open_orders
    )
    msg = sa.format_audit_telegram_message(audit_res)

    await update.message.reply_text(msg, parse_mode="Markdown")
  except Exception as e:
    await update.message.reply_text(f"❌ Erreur lors de l'audit : {str(e)}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  """Affiche le guide complet d'utilisation du bot."""
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  msg = (
      "📖 *GUIDE DE L'ASSISTANT FINARY (v1.6)*\n\n"
      "Voici les fonctionnalités accessibles directement par commande ou"
      " message :\n\n"
      "💬 *1. Assistant Patrimonial (Questions ouvertes)*\n"
      "Pose-moi directement tes questions par message texte, par exemple :\n"
      "• _« Quelle est la répartition globale de mon patrimoine ? »_\n"
      "• _« Analyse mon exposition aux cryptomonnaies. »_\n"
      "• _« Quels sont mes plus gros risques actuellement ? »_\n\n"
      "🎯 *2. Rapport & Alertes Spéculatives*\n"
      "• *Rapport de 19h* : Bilan quotidien avec opportunités et boutons d'action"
      " `[✅ Pris]` / `[❌ Ignorer]`.\n"
      "• *Alertes 24/7* : Notification automatique dès qu'un token atteint"
      " +100% (TP) ou -30% (SL).\n\n"
      "⚙️ *3. Commandes Rapides*\n"
      "• `/positions` — Affiche les trades suivis en BDD, leur cours live et PnL"
      " (%)\n"
      "• `/pnl` — Consulter le PnL réalisé global, Win Rate et frais API\n"
      "• `/audit` — Vérifier en temps réel les Stop-Loss & Take-Profit actifs\n"
      "• `/help` — Afficher ce guide d'utilisation\n"
      "• `/start` — Relancer l'accueil"
  )
  await update.message.reply_text(msg, parse_mode="Markdown")


def fetch_finary_snapshot_sync():
  session = init_finary_session()
  from finary_uapi.user_portfolio import (
      get_portfolio_cryptos,
      get_portfolio_investments,
  )

  invest_data = get_portfolio_investments(session)
  crypto_data = get_portfolio_cryptos(session)
  return build_current_snapshot(invest_data, crypto_data)


def ask_gemini_sync(prompt):
  client = genai.Client(api_key=GEMINI_API_KEY)
  modeles = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

  for modele in modeles:
    logging.info(f"🤖 Tentative d'appel avec le modèle : {modele}")
    for tentative in range(1, 3):
      try:
        response = client.models.generate_content(
            model=modele, contents=prompt
        )
        if response.text:
          logging.info(f"✅ Réponse générée avec succès via {modele}")
          return response.text
      except Exception as e:
        logging.warning(
            f"⚠️ Erreur avec {modele} (tentative {tentative}/2) : {e}"
        )
        time.sleep(tentative * 2)

  raise RuntimeError("❌ Tous les modèles Gemini configurés ont échoué.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès refusé.")
    return

  await update.message.reply_text(
      "👋 Bonjour Grégoire !\n\n"
      "Je suis ton assistant patrimonial Finary propulsé par Gemini (v1.6).\n"
      "• Pose-moi n'importe quelle question sur ton portefeuille.\n"
      "• Utilise `/positions` pour tes trades en BDD, `/pnl` pour tes gains"
      " réalisés et `/audit` pour vérifier tes Stop-Loss."
  )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  open_trades = get_open_positions()
  if not open_trades:
    await update.message.reply_text(
        "📋 Aucune position spéculative suivie en BDD actuellement."
    )
    return

  await update.message.reply_chat_action(action="typing")

  header = "📊 *POSITIONS SPÉCULATIVES SUIVIES (Temps Réel)*\n\n"
  messages = [header]
  current_msg_index = 0

  for t in open_trades:
    rec_id = t[0] if len(t) > 0 else None
    symbol = t[1] if len(t) > 1 else "N/A"
    exchange = t[2] if len(t) > 2 else "N/A"
    price_entry = float(t[3]) if len(t) > 3 and t[3] is not None else 0.0
    amount_eur = float(t[4]) if len(t) > 4 and t[4] is not None else 0.0
    tp_price = float(t[5]) if len(t) > 5 and t[5] is not None else 0.0
    sl_price = float(t[7]) if len(t) > 7 and t[7] is not None else 0.0
    taken_at = t[7] if len(t) > 7 else None

    current_price = None
    try:
      current_price = await asyncio.wait_for(
          asyncio.to_thread(get_live_price, symbol, exchange), timeout=2.0
      )
    except Exception:
      pass

    pnl_str = "N/A"
    if current_price and price_entry > 0:
      pnl_pct = ((current_price - price_entry) / price_entry) * 100
      pnl_str = f"{pnl_pct:+.2f}% ({current_price:.4f} €)"

    date_str = str(taken_at)[:10] if taken_at else "N/A"

    block = (
        f"🔹 *{symbol}* ({exchange})\n"
        f"  • Investi : {amount_eur:.2f} € @ {price_entry:.4f} €\n"
        f"  • Cours actuel : {pnl_str}\n"
        f"  • 🎯 TP : {tp_price:.4f} € | 🛑 SL : {sl_price:.4f} €\n"
        f"  • Entrée : {date_str}\n\n"
    )

    # Si le message approche la limite Telegram (~3800 caractères pour être sûr), on crée un nouveau bloc
    if len(messages[current_msg_index]) + len(block) > 3800:
      messages.append(block)
      current_msg_index += 1
    else:
      messages[current_msg_index] += block

  # Envoi de tous les morceaux générés
  for msg_chunk in messages:
    await update.message.reply_text(msg_chunk, parse_mode="Markdown")


async def handle_callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  query = update.callback_query
  await query.answer()

  if str(query.from_user.id) != str(ALLOWED_USER_ID):
    return

  data = query.data
  if data.startswith("trade_take_"):
    rec_id = int(data.replace("trade_take_", ""))
    mark_trade_taken(rec_id)
    await query.message.reply_text(
        "✅ *Trade enregistré comme PRIS !* (Suivi 24/7 actif)",
        parse_mode="Markdown",
    )

  elif data.startswith("trade_ignore_"):
    rec_id = int(data.replace("trade_ignore_", ""))
    mark_trade_ignored(rec_id)
    await query.message.reply_text("❌ Opportunité ignorée.")

  elif data.startswith("trade_close_"):
    rec_id = int(data.replace("trade_close_", ""))
    close_trade(rec_id)
    await query.message.reply_text(
        "🔒 *Position clôturée dans la base de données.*", parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  user_query = update.message.text
  await update.message.reply_chat_action(action="typing")

  try:
    current_snap = await asyncio.to_thread(fetch_finary_snapshot_sync)
    total_patrimoine = (
        current_snap["total_invest"] + current_snap["total_crypto"]
    )
    context_prompt = f"""Tu es un assistant patrimonial privé et conseiller financier.
Voici l'état actuel et exact du portefeuille Finary de l'utilisateur :

- Patrimoine Total : {total_patrimoine:,.2f} €
- Poche Investissements (Bourse/Fonds/PEA) : {current_snap['total_invest']:,.2f} €
- Poche Cryptomonnaies : {current_snap['total_crypto']:,.2f} €

Détail complet des positions actuelles :
{json.dumps(current_snap['holdings'], indent=2, ensure_ascii=False)}

Question de l'utilisateur : "{user_query}"

Consignes :
1. Réponds de façon précise, synthétique et directe en t'appuyant sur les chiffres exacts ci-dessus.
2. Utilise un ton professionnel, clair et concis adapté à un tchat Telegram.
3. Si la question nécessite des calculs de pourcentages ou de répartition, effectue-les précisément."""

    reply_text = await asyncio.to_thread(ask_gemini_sync, context_prompt)
    await update.message.reply_text(reply_text)
  except Exception as e:
    logging.error(f"Erreur traitement message : {e}")
    await update.message.reply_text(f"⚠️ Une erreur est survenue : {e}")


if __name__ == "__main__":
  if not BOT_TOKEN:
    exit(1)

  app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

  # Enregistrement des commandes
  app.add_handler(CommandHandler("start", start_command))
  app.add_handler(CommandHandler("help", help_command))
  app.add_handler(CommandHandler("pnl", pnl_command))
  app.add_handler(CommandHandler("audit", audit_command))
  app.add_handler(CommandHandler("positions", positions_command))

  # Enregistrement des boutons et des messages textes
  app.add_handler(CallbackQueryHandler(handle_callback_query))
  app.add_handler(
      MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
  )

  print("🤖 Bot Telegram Assistant Finary v1.6 démarré...")
  app.run_polling()
