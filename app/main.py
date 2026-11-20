from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.bootstrap import create_dispatcher
from app.config import ConfigError, load_settings
from app.container import build_container
from app.database.migrations import run_migrations
from app.database.session import Database
from app.tasks import maintenance_loop
from app.web import build_app

logger = logging.getLogger("app")

COMMANDS = [
    BotCommand(command="start", description="Start the bot"),
    BotCommand(command="help", description="Show help"),
    BotCommand(command="find", description="Find a random partner"),
    BotCommand(command="next", description="Disconnect and search again"),
    BotCommand(command="stop", description="End conversation"),
    BotCommand(command="profile", description="View profile"),
    BotCommand(command="report", description="Report current partner"),
    BotCommand(command="block", description="Block current partner"),
    BotCommand(command="unblock", description="Manage blocked users"),
    BotCommand(command="premium", description="Premium plans"),
]


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Keep chatty libraries quiet so logs never contain tokens or message content.
    for noisy in ("aiohttp.access", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def run() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    setup_logging(settings.log_level)
    if settings.min_age < 18:
        logger.warning("MIN_AGE is %s (< 18). Make sure this is a deliberate, legally reviewed decision.", settings.min_age)

    db = Database(settings.mongodb_uri, settings.mongodb_db)
    if not await db.ping():
        raise SystemExit("Cannot connect to MongoDB. Check MONGODB_URI.")
    await run_migrations(db, settings)

    container = build_container(settings, db)
    interrupted = await container.broadcasts.mark_interrupted()
    if interrupted:
        logger.warning("%s broadcast(s) were interrupted by the previous shutdown.", interrupted)
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher(container)

    try:
        me = await bot.get_me()
    except Exception as exc:  # noqa: BLE001
        await bot.session.close()
        await db.close()
        raise SystemExit(f"Cannot reach Telegram or BOT_TOKEN is invalid: {type(exc).__name__}") from exc
    logger.info("Starting @%s in %s mode (%s)", me.username, settings.run_mode, settings.environment)
    await bot.set_my_commands(COMMANDS)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    maintenance = asyncio.create_task(maintenance_loop(bot, container, stop))
    app = build_app(container)
    runner: web.AppRunner | None = None

    try:
        if settings.run_mode == "webhook":
            SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret).register(
                app, path=settings.webhook_path
            )
            setup_application(app, dp, bot=bot)
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=False,
            )
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.port).start()
            logger.info("Webhook set; listening on port %s", settings.port)
            await stop.wait()
        else:
            runner = web.AppRunner(app)  # health endpoint only
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.port).start()
            await bot.delete_webhook(drop_pending_updates=False)
            polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
            await stop.wait()
            await dp.stop_polling()
            await polling
    finally:
        stop.set()
        maintenance.cancel()
        await container.broadcasts.shutdown()
        if runner:
            await runner.cleanup()
        await bot.session.close()
        await db.close()
        logger.info("Shut down cleanly.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
