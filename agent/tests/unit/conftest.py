import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.auth import TokenHolder
from agent.results.server import make_results_app


@pytest_asyncio.fixture
async def results_client(tmp_path):
    """Yields (AsyncClient, results_dir, session_token) for results server tests."""
    token = "test-session-token"
    app = make_results_app(tmp_path, TokenHolder(token))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, tmp_path, token
