import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agent.auth import TokenHolder
from agent.results.server import make_results_app


@pytest.fixture(autouse=True)
def _no_warm_connections():
    """Leave no warm spares behind: `run_control_channel` turns the pool on, and a
    later test's `open_and_attach` would take a spare instead of opening its own."""
    yield
    from agent.executor import runner

    runner.enable_warm_connections(0)


@pytest_asyncio.fixture
async def results_client(tmp_path):
    """Yields (AsyncClient, results_dir, session_token) for results server tests."""
    token = "test-session-token"
    app = make_results_app(tmp_path, TokenHolder(token))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, tmp_path, token


@pytest.fixture(scope="session")
def tracer_provider():
    """Session-wide in-memory tracer provider for span-asserting tests.

    trace.set_tracer_provider is once-per-process (later calls are ignored with
    a warning), so every test that wants to inspect spans must share this one
    provider/exporter rather than installing its own.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def span_exporter(tracer_provider):
    tracer_provider.clear()
    return tracer_provider
