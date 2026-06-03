import asyncio
import logging
from pathlib import Path

import uvicorn

from agent.auth import TokenHolder
from agent.config import settings
from agent.control.channel import run_control_channel
from agent.results.retention import sweep_loop
from agent.results.server import make_results_app

logging.basicConfig(level=logging.INFO)


async def _run_result_server(results_dir: Path, token_holder: TokenHolder) -> None:
    app = make_results_app(results_dir, token_holder)
    config = uvicorn.Config(
        app,
        host=settings.results_http_host,
        port=settings.results_http_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    results_dir = Path(settings.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    token_holder = TokenHolder()
    await asyncio.gather(
        run_control_channel(results_dir=results_dir, token_holder=token_holder),
        _run_result_server(results_dir, token_holder),
        sweep_loop(
            results_dir,
            settings.result_retention_hours,
            settings.retention_sweep_interval_s,
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
