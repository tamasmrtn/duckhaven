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


def test_configuration_lock_is_on_by_default():
    """Secure by default: flipping this off silently removes the control that
    stops a session statement re-widening its own DuckDB sandbox with `SET`."""
    assert Settings.model_fields["sandbox_lock_configuration"].default is True


def test_disabled_filesystems_defaults_empty():
    """Off by default because presigned staging reads need HTTPFileSystem; the
    HTTP containment is carried by the agent's network egress restriction
    instead. See the rationale on the setting itself."""
    assert Settings.model_fields["sandbox_disabled_filesystems"].default == ""
