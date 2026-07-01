"""Worker process entrypoint (placeholder).

Real background jobs (fixture/odds collection, simulations) are wired here
later. For now it idles so the compose ``worker`` service starts cleanly.
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def main() -> None:
    configure_logging()
    logger.info("Worker started (placeholder — no jobs registered yet)")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
