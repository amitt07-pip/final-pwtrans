"""
Run Crypto Escrow Bot only
"""
import sys
import types
import os
import asyncio

if sys.version_info >= (3, 13):
    sys.modules["imghdr"] = types.ModuleType("imghdr")

def main():
    crypto_token = os.getenv("BOT_TOKEN")
    if not crypto_token:
        print("❌ ERROR: BOT_TOKEN not set!")
        sys.exit(1)
    
    print("✅ Crypto Escrow Bot - Starting...")
    
    import pwcrypto
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
    
    pwcrypto.load_history()
    
    # Initialize database connection pool
    pwcrypto.initialize_db_pool()
    pwcrypto.load_active_deals_from_db()
    
    # Initialize Telethon for bio checking
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(pwcrypto.initialize_telethon())
    
    app = ApplicationBuilder().token(crypto_token).build()
    
    # Conversation handler for /addstat
    addstat_conv = ConversationHandler(
        entry_points=[CommandHandler("addstat", pwcrypto.addstat_start)],
        states={
            pwcrypto.ADDSTAT_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, pwcrypto.addstat_volume)],
            pwcrypto.ADDSTAT_DEALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, pwcrypto.addstat_deals)],
            pwcrypto.ADDSTAT_HIGHEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, pwcrypto.addstat_highest)],
        },
        fallbacks=[CommandHandler("cancel", pwcrypto.addstat_cancel)],
    )
    app.add_handler(addstat_conv)

    app.add_handler(CommandHandler("add", pwcrypto.add_deal))
    app.add_handler(CallbackQueryHandler(pwcrypto.fee_selected, pattern=r"^fee_"))
    app.add_handler(CommandHandler("close", pwcrypto.close_deal))
    app.add_handler(CommandHandler("refund", pwcrypto.refund_deal))
    app.add_handler(CommandHandler("active_deals", pwcrypto.show_active_deals))
    app.add_handler(CommandHandler("stats", pwcrypto.show_stats))
    app.add_handler(CommandHandler("adminwise", pwcrypto.adminwise_command))
    
    print(f"✅ Crypto Escrow Bot is running (Admins: {len(pwcrypto.ADMINS)})")
    print("✅ Bot is now polling for updates...")
    
    app.run_polling()

if __name__ == "__main__":
    main()
