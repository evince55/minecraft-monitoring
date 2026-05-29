import asyncio

import config
from bot import Bot
from prometheus_client import PrometheusClient
from rcon_client import RCONClient


async def main():
    prometheus = PrometheusClient(config.PROMETHEUS_URL)
    rcon = RCONClient(config.RCON_HOST, config.RCON_PORT, config.RCON_PASSWORD)
    bot = Bot(prometheus, rcon)
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
