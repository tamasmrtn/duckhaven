"""Compose wiring for the tracing stack (OTel Collector + Tempo + Grafana).

Both compose files must ship the collector and Tempo; Grafana is a dev-only
convenience and must stay out of the HA file, where operators bring their own.
"""

from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)
with (DEPLOY / "docker-compose.ha.yml").open() as f:
    HA = yaml.safe_load(f)


def test_collector_and_tempo_in_both_files():
    for compose in (DEV, HA):
        assert "otel-collector" in compose["services"]
        assert "tempo" in compose["services"]
        assert "tempo_data" in compose["volumes"]


def test_grafana_only_in_dev_compose():
    assert "grafana" in DEV["services"]
    assert "grafana" not in HA["services"]


def test_api_services_export_otlp():
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in DEV["services"]["api"]["environment"]
    for replica in ("api-1", "api-2"):
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in HA["services"][replica]["environment"]


def test_referenced_config_files_exist():
    assert (DEPLOY / "otel" / "otel-collector.yaml").is_file()
    assert (DEPLOY / "tempo" / "tempo.yaml").is_file()
    assert (DEPLOY / "grafana" / "provisioning" / "datasources" / "tempo.yaml").is_file()


def test_polaris_otel_enabled_and_pointed_at_the_collector():
    for compose in (DEV, HA):
        env = compose["services"]["polaris"]["environment"]
        assert env["QUARKUS_OTEL_SDK_DISABLED"] == "false"
        assert "QUARKUS_OTEL_EXPORTER_OTLP_ENDPOINT" in env


def test_tempo_image_defaults_to_a_pinned_version_not_latest():
    # deploy/tempo/tempo.yaml is on Tempo's 2.x config schema (a monolithic
    # `compactor` block); 3.0 replaced that with a disaggregated
    # backend-scheduler/backend-worker split and crash-loops against it. Every
    # other bundled image floats on :latest — Tempo must not, until tempo.yaml
    # is migrated to the 3.x schema.
    for compose in (DEV, HA):
        image = compose["services"]["tempo"]["image"]
        assert "TEMPO_IMAGE_TAG:-latest" not in image
