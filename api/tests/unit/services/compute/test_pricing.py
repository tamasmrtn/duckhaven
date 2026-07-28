"""Size bounds offered for a compute shape.

The ceiling used to be a module constant carrying Azure Container Instances'
per-group quota, applied to every provider. That made the sliders meaningless off
Azure in both directions: a 64-core box was capped at 4 vCPU, and a 4 GiB NUC was
offered 16 GiB that its agent would be OOM-killed for accepting.

It now comes from the backend, which is the only thing that knows what its platform
will run.
"""

import pytest

from api.config import settings
from api.services.compute import pricing


class _Backend:
    def __init__(self, capacity):
        self._capacity = capacity

    async def capacity(self):
        if isinstance(self._capacity, Exception):
            raise self._capacity
        return self._capacity


@pytest.fixture
def with_backend(monkeypatch):
    def _install(capacity, provider="docker"):
        monkeypatch.setattr(settings, "elastic_provider", provider)
        monkeypatch.setattr(pricing, "get_backend", lambda _p: _Backend(capacity))

    return _install


async def test_ceiling_comes_from_the_platform(with_backend):
    with_backend((7.0, 28.0))

    lim = await pricing.limits()

    assert (lim.cpu_max, lim.memory_max_gb) == (7.0, 28.0)
    # The floor and granularity are the same everywhere.
    assert (lim.cpu_min, lim.memory_min_gb) == (1.0, 1.0)
    assert (lim.cpu_step, lim.memory_step_gb) == (1.0, 1.0)


async def test_backend_with_no_opinion_gets_the_conservative_default(with_backend):
    """Guessing high would offer a size the platform then refuses, which surfaces
    as a provisioning failure minutes later rather than a narrower slider now."""
    with_backend(None)

    lim = await pricing.limits()

    assert (lim.cpu_max, lim.memory_max_gb) == (
        pricing.DEFAULT_CPU_MAX,
        pricing.DEFAULT_MEMORY_MAX_GB,
    )


async def test_unreachable_platform_does_not_break_the_dialog(with_backend):
    with_backend(RuntimeError("daemon down"))

    lim = await pricing.limits()

    assert lim.cpu_max == pricing.DEFAULT_CPU_MAX


async def test_unknown_provider_does_not_break_the_dialog(monkeypatch):
    monkeypatch.setattr(settings, "elastic_provider", "not-a-provider")

    lim = await pricing.limits()

    assert lim.cpu_max == pricing.DEFAULT_CPU_MAX


async def test_allows_accepts_inside_and_rejects_outside(with_backend):
    with_backend((7.0, 28.0))
    lim = await pricing.limits()

    assert lim.allows(7.0, 28.0)
    assert lim.allows(1.0, 1.0)
    assert not lim.allows(8.0, 28.0)
    assert not lim.allows(7.0, 29.0)
    assert not lim.allows(0.5, 4.0)


async def test_hourly_cost_is_zero_when_rates_are(monkeypatch):
    """A deployment on hardware you already own has no marginal hourly cost."""
    monkeypatch.setattr(settings, "elastic_azure_price_vcpu_hour", 0.0)
    monkeypatch.setattr(settings, "elastic_azure_price_memory_gb_hour", 0.0)

    assert pricing.hourly_cost(4.0, 16.0) == 0.0


async def test_hourly_cost_sums_both_rates(monkeypatch):
    monkeypatch.setattr(settings, "elastic_azure_price_vcpu_hour", 0.05)
    monkeypatch.setattr(settings, "elastic_azure_price_memory_gb_hour", 0.005)

    assert pricing.hourly_cost(2.0, 8.0) == round(2 * 0.05 + 8 * 0.005, 4)


class _PricedBackend(_Backend):
    """A backend that also declares the currency its rates are quoted in."""

    def __init__(self, capacity, currency):
        super().__init__(capacity)
        self._currency = currency

    async def pricing_currency(self):
        return self._currency


async def test_currency_comes_from_the_provider(monkeypatch):
    """Not a global default. The rates an operator enters are copied from a
    provider's own pricing page, which quotes them in a currency; that currency
    belongs to the provider, not to DuckHaven."""
    monkeypatch.setattr(settings, "elastic_provider", "azure_aci")
    monkeypatch.setattr(pricing, "get_backend", lambda _p: _PricedBackend(None, "EUR"))

    assert await pricing.currency() == "EUR"


async def test_a_provider_that_bills_nothing_has_no_currency(monkeypatch):
    """A container on a machine you already own is not priced by anyone, so there
    is no currency to render a cost in — and inventing one would put a cloud
    price symbol on a homelab."""
    monkeypatch.setattr(settings, "elastic_provider", "docker")
    monkeypatch.setattr(pricing, "get_backend", lambda _p: _Backend(None))

    assert await pricing.currency() is None


async def test_unknown_provider_has_no_currency(monkeypatch):
    monkeypatch.setattr(settings, "elastic_provider", "not-a-provider")

    assert await pricing.currency() is None
