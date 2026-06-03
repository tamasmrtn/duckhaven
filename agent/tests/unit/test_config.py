from agent.config import Settings


def test_results_http_host_defaults_to_all_interfaces():
    """The result server must bind all interfaces by default so the control plane
    can reach it across the container/host boundary (BUG-8). The endpoint is
    Bearer-gated by the session token, so this is safe."""
    assert Settings.model_fields["results_http_host"].default == "0.0.0.0"


def test_session_token_path_defaults_empty_for_runtime_resolution():
    """An empty session-token path is resolved at runtime to a file under the
    persistent results dir (BUG-2)."""
    assert Settings.model_fields["session_token_path"].default == ""
