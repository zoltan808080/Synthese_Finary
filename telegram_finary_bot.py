import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    set_trade_break_even,
)
import exchange_connectors as ec
import pnl_engine as pe
import sl_audit_engine as sa

# Support des moteurs optionnels v2.9 / v3.0
try:
  from rsi_engine import build_rsi_heatmap_dataframe

  RSI_AVAILABLE = True
except ImportError:
  RSI_AVAILABLE = False

try:
  from crypto_speculative_advisor import (
      check_circuit_breaker,
      fetch_fear_and_greed_index,
  )

  ADVISOR_AVAILABLE = True
except ImportError:
  ADVISOR_AVAILABLE = False

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
      ("status", "⚡ Synthèse exécutive & état du marché (Fear&Greed, Cash)"),
      ("positions", "📊 Trades suivis avec boutons d'action (BE / Clôture)"),
      ("rsi", "🔥 Heatmap RSI multi-UT (Screener survente/surachat)"),
      ("pnl", "📈 Bilan PnL global, Win Rate & frais API"),
      ("audit", "🛡️ Audit de couverture Stop-Loss / Take-Profit"),
      ("help", "📖 Guide complet d'utilisation"),
      ("start", "👋 Relancer l'accueil"),
  ]
  await application.bot.set_my_commands(commands)


def ask_gemini_sync(prompt):
  """Génération de contenu avec Fallback multi-modèles (Résilience v3.0)."""
  client = genai.Client(api_key=GEMINI_API_KEY)
  modeles = [
      "gemini-3.8-flash",
      "gemini-3.7-flash",
      "gemini-3.6-flash",
      "gemini-3.5-flash-lite",
  ]

  for modele in modeles:
    logging.info(f"🤖 Tentative d'appel avec le modèle : {modele}")
    for tentative in range(1, 3):
      try:
        response = client.models.generate_content(
            model=modele, contents=prompt
        )
        if response and response.text:
          logging.info(f"✅ Réponse générée avec succès via {modele}")
          return response.text
      except Exception as e:
        logging.warning(
            f"⚠️ Erreur avec {modele} (tentative {tentative}/2) : {e}"
        )
        time.sleep(tentative * 1)

  raise RuntimeError("❌ Tous les modèles Gemini configurés ont échoué.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès refusé.")
    return

  await update.message.reply_text(
      "👋 Bonjour Grégoire !\n\n"
      "Je suis ton assistant patrimonial & cockpit mobile (v3.0).\n\n"
      "• `/status` — Synthèse du marché & liquidités cash\n"
      "• `/positions` — Vos trades suivis en BDD avec gestion 1-click\n"
      "• `/rsi` — Heatmap RSI multi-UT\n"
      "• `/pnl` — Bilan des gains réalisés\n"
      "• `/audit` — Vérification des Stop-Loss"
  )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  """Affiche une synthèse exécutive rapide du marché et des avoirs."""
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  await update.message.reply_chat_action(action="typing")

  try:
    fng_str = "N/A"
    cb_str = "N/A"
    if ADVISOR_AVAILABLE:
      fng = fetch_fear_and_greed_index()
      fng_str = (
          f"{fng.get('value', 'N/A')}/100 ({fng.get('classification', '')})"
      )
      is_crash, crash_pct = check_circuit_breaker()
      cb_str = f"{'🚨 DÉFENSIF (CRASH)' if is_crash else '🟢 NORMAL'} ({crash_pct:+.2f}%)"

    cash_info = await asyncio.to_thread(ec.get_total_verified_cash)
    total_cash = cash_info.get("total_cash", 0.0)

    open_trades = get_open_positions()

    msg = (
        "⚡ *SYNTHÈSE EXÉCUTIVE EN TEMPS RÉEL (v3.0)*\n\n"
        f"• *Sentiment de Marché :* {fng_str}\n"
        f"• *Circuit Breaker BTC (24h) :* {cb_str}\n"
        f"• *Liquidités Cash Réel :* `{total_cash:,.2f} €`\n"
        f"• *Positions Spéculatives Actives :* `{len(open_trades)}` ligne(s)\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
  except Exception as e:
    await update.message.reply_text(
        f"❌ Erreur lors de la génération du status : {e}"
    )


async def rsi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  """Affiche le screener Heatmap RSI en direct."""
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  if not RSI_AVAILABLE:
    await update.message.reply_text(
        "⚠️ Module RSI non disponible (`rsi_engine.py`)."
    )
    return

  await update.message.reply_text("🔄 Calcul de la Heatmap RSI en cours...")

  open_trades = get_open_positions()
  tracked_symbols = list(
      {t[1] for t in open_trades}
      | {"BTC", "ETH", "SOL", "NEAR", "ONDO", "PENDLE", "SUI", "AVAX"}
  )

  try:
    df_rsi = await asyncio.to_thread(
        build_rsi_heatmap_dataframe, tracked_symbols
    )
    if df_rsi.empty:
      await update.message.reply_text("⚠️ Aucune donnée RSI disponible.")
      return

    msg = "🔥 *HEATMAP RSI MULTI-TIMEFRAME (v2.9)*\n\n"
    for _, row in df_rsi.iterrows():
      sym = row["Actif"]
      r15, r1h, r4h, r1d = row["15m"], row["1h"], row["4h"], row["1d"]
      signal = row["Signal"]

      msg += (
          f"🔹 *{sym}* : {signal}\n"
          f"   15m: `{r15}` | 1h: `{r1h}` | 4h: `{r4h}` | 1D: `{r1d}`\n\n"
      )

    await update.message.reply_text(msg, parse_mode="Markdown")
  except Exception as e:
    await update.message.reply_text(f"❌ Erreur lors du calcul RSI : {e}")


async def positions_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  """Affiche les positions actives avec boutons d'action 1-click."""
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
  await update.message.reply_text(
      "📊 *POSITIONS SPÉCULATIVES ACTIVES*", parse_mode="Markdown"
  )

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
        f"🔹 *{symbol}* ({exchange}) [ID {rec_id}]\n"
        f"  • Investi : {amount_eur:.2f} € @ {price_entry:.4f} €\n"
        f"  • Cours actuel : {pnl_str}\n"
        f"  • 🎯 TP : {tp_price:.4f} € | 🛑 SL : {sl_price:.4f} €\n"
        f"  • Entrée : {date_str}"
    )

    # Clavier d'action 1-click sous chaque position
    keyboard = [
        [
            InlineKeyboardButton(
                "🛡️ Passer en BE", callback_data=f"trade_be_{rec_id}"
            ),
            InlineKeyboardButton(
                "🔒 Clôturer", callback_data=f"trade_close_{rec_id}"
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        block, parse_mode="Markdown", reply_markup=reply_markup
    )


async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  await update.message.reply_text("🔄 Audit de la couverture SL/TP...")

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
  user_id = str(update.effective_user.id)
  if user_id != str(ALLOWED_USER_ID):
    await update.message.reply_text("⛔ Accès non autorisé.")
    return

  msg = (
      "📖 *GUIDE DE L'ASSISTANT FINARY & COCKPIT (v3.0)*\n\n"
      "💬 *1. Questions Ouvertes (IA)*\n"
      "Posez directement vos questions sur le patrimoine, l'exposition crypto ou"
      " les risques.\n\n"
      "⚡ *2. Pilotage & Commandes*\n"
      "• `/status` — Bilan synthétique du marché, Fear&Greed et cash\n"
      "• `/positions` — Trades suivis avec boutons d'action 1-click (`Passer en"
      " BE` / `Clôturer`)\n"
      "• `/rsi` — Screener Heatmap RSI (15m, 1h, 4h, 1D)\n"
      "• `/pnl` — PnL réalisé global et frais API\n"
      "• `/audit` — Contrôle de la présence des Stop-Loss\n"
      "• `/start` — Relancer l'accueil"
  )
  await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  """Gestionnaire central des boutons interactifs Telegram."""
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
        f"🔒 *Position ID {rec_id} clôturée en BDD.*", parse_mode="Markdown"
    )

  elif data.startswith("trade_be_"):
    rec_id = int(data.replace("trade_be_", ""))
    set_trade_break_even(rec_id)
    await query.message.reply_text(
        f"🛡️ *Position ID {rec_id} passée en Break-Even !* (SL = Prix d'entrée)",
        parse_mode="Markdown",
    )

  elif data.startswith("ghost_clean_"):
    rec_id = int(data.replace("ghost_clean_", ""))
    close_trade(rec_id)
    await query.message.reply_text(
        f"🧹 *Ligne fantôme ID {rec_id} purgée de la base de données.*",
        parse_mode="Markdown",
    )


def fetch_finary_snapshot_sync():
  session = init_finary_session()
  from finary_uapi.user_portfolio import (
      get_portfolio_cryptos,
      get_portfolio_investments,
  )

  invest_data = get_portfolio_investments(session)
  crypto_data = get_portfolio_cryptos(session)
  return build_current_snapshot(invest_data, crypto_data)


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

  # Commandes Telegram
  app.add_handler(CommandHandler("start", start_command))
  app.add_handler(CommandHandler("help", help_command))
  app.add_handler(CommandHandler("status", status_command))
  app.add_handler(CommandHandler("rsi", rsi_command))
  app.add_handler(CommandHandler("positions", positions_command))
  app.add_handler(CommandHandler("pnl", pnl_command))
  app.add_handler(CommandHandler("audit", audit_command))

  # Boutons et messages
  app.add_handler(CallbackQueryHandler(handle_callback_query))
  app.add_handler(
      MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
  )

  print("🤖 Bot Telegram Cockpit & Assistant Finary v3.0 démarré...")
  app.run_polling()
