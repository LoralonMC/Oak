"""
Oak — A modular Discord bot framework.

Entry point: configure logging, create OakConfig, create OakBot, run.

GitHub: https://github.com/LoralonMC/oak
"""

import logging
from logging.handlers import TimedRotatingFileHandler
import sys
from pathlib import Path

# Create logs directory relative to this file (not CWD)
logs_dir = Path(__file__).parent / "logs"
logs_dir.mkdir(exist_ok=True)

# Configure logging with rotating file handler (keeps 30 days of logs)
file_handler = TimedRotatingFileHandler(
    logs_dir / "oak.log",
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        file_handler,
        logging.StreamHandler(sys.stdout),
    ],
)

logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main():
    from oak import OakBot, OakConfig

    try:
        logger.info("Starting Oak...")
        config = OakConfig()
        bot = OakBot(config)
        bot.run(config.token, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Oak shutting down...")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
