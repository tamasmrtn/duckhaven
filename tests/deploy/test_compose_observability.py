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


def test_tempo_image_defaults_to_a_3x_release():
    # tempo.yaml is on the 3.x config schema, which 2.x cannot parse, so the
    # default must be a 3.x release — never :latest, never the old 2.x pin.
    for compose in (DEV, HA):
        image = compose["services"]["tempo"]["image"]
        assert "TEMPO_IMAGE_TAG:-latest" not in image
        assert "TEMPO_IMAGE_TAG:-3." in image


def test_tempo_config_uses_the_3x_schema_and_keeps_72h_retention():
    with (DEPLOY / "tempo" / "tempo.yaml").open() as f:
        config = yaml.safe_load(f)
    # 3.x removed the monolithic `compactor` block and refuses to start on it.
    assert "compactor" not in config
    # Retention is a backend-worker setting; without it blocks live for 14 days.
    assert config["backend_worker"]["compaction"]["block_retention"] == "72h"
