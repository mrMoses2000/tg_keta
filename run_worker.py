"""
Entry point: run the worker + outbox dispatcher.
"""
import asyncio
import signal
import structlog

from src.db import postgres as pg
from src.db import redis_client as rc
from src.queue.worker import run_worker
from src.engine.outbox_dispatcher import run_outbox_loop

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Start worker and outbox dispatcher as concurrent tasks."""
    # Initialize connections
    await pg.get_pool()
    await rc.get_redis()

    logger.info("worker_main_starting")

    # Run worker and outbox loop concurrently
    worker_task = asyncio.create_task(run_worker())
    outbox_task = asyncio.create_task(run_outbox_loop(interval=15))

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("worker_main_shutdown_signal")
        stop_event.set()
        worker_task.cancel()
        outbox_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        await asyncio.gather(worker_task, outbox_task)
    except asyncio.CancelledError:
        pass
    finally:
        await pg.close_pool()
        await rc.close_redis()
        logger.info("worker_main_shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
