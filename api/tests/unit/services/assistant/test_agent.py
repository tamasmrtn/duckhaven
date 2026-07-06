from api.config import settings
from api.services.assistant.agent import _build_model


def test_openai_compatible_model_keeps_tag(monkeypatch):
    # An Ollama-style tag contains a colon and must not be truncated.
    monkeypatch.setattr(settings, "assistant_openai_base_url", "https://ollama.com/v1")
    monkeypatch.setattr(settings, "assistant_model", "kimi-k2.7-code:cloud")
    monkeypatch.setattr(settings, "assistant_api_key", "x")
    model = _build_model()
    assert model.model_name == "kimi-k2.7-code:cloud"


def test_openai_prefix_is_stripped(monkeypatch):
    monkeypatch.setattr(settings, "assistant_openai_base_url", "http://local/v1")
    monkeypatch.setattr(settings, "assistant_model", "openai:gpt-4o")
    monkeypatch.setattr(settings, "assistant_api_key", "x")
    model = _build_model()
    assert model.model_name == "gpt-4o"


def test_no_base_url_returns_model_string(monkeypatch):
    monkeypatch.setattr(settings, "assistant_openai_base_url", None)
    monkeypatch.setattr(settings, "assistant_model", "anthropic:claude-sonnet-4-latest")
    assert _build_model() == "anthropic:claude-sonnet-4-latest"
