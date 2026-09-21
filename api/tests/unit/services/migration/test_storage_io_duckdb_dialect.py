"""Translating DuckHaven's own storage block into the vocabulary this module reads.

Two credential vocabularies exist: Polaris vends the Iceberg REST spelling, and
DuckLake -- which has no vendor -- is minted by DuckHaven in DuckDB's. The
listing helpers speak the first, so the second is translated rather than given a
second implementation.
"""

from __future__ import annotations

from api.services.migration.storage_io import (
    StorageContext,
    _same_s3_endpoint,
    context_from_duckdb_block,
)


def test_every_s3_field_is_translated():
    ctx = context_from_duckdb_block(
        "s3",
        {
            "type": "s3",
            "key_id": "AK",
            "secret": "SK",
            "session_token": "TOK",
            "region": "eu-west-1",
            "endpoint": "store.example.com",
            "use_ssl": True,
        },
        {"region": "ignored"},
    )

    assert ctx.creds["s3.access-key-id"] == "AK"
    assert ctx.creds["s3.secret-access-key"] == "SK"
    assert ctx.creds["s3.session-token"] == "TOK"
    assert ctx.creds["client.region"] == "eu-west-1"
    # DuckDB is handed a bare host; boto3 needs the scheme back.
    assert ctx.creds["s3.endpoint"] == "https://store.example.com"


def test_an_empty_session_token_becomes_none():
    """boto3 treats an empty token as a token and signs with it, which fails."""
    ctx = context_from_duckdb_block(
        "object_store", {"type": "s3", "key_id": "a", "secret": "b", "session_token": ""}, None
    )
    assert ctx.creds["s3.session-token"] is None


def test_an_azure_block_becomes_a_bare_sas_keyed_by_account():
    ctx = context_from_duckdb_block(
        "adls_gen2",
        {
            "type": "azure",
            "account_name": "acme",
            "connection_string": (
                "BlobEndpoint=https://acme.blob.core.windows.net;"
                "SharedAccessSignature=sv=2024&sig=abc"
            ),
        },
        None,
    )
    assert ctx.creds == {"adls.sas-token.acme": "sv=2024&sig=abc"}


def test_a_server_side_copy_is_only_offered_when_one_client_sees_both_ends():
    """It is a single request naming both objects, so differing endpoints or
    credentials have to fall back to streaming rather than fail."""
    same = StorageContext("s3", {"s3.endpoint": "https://a", "s3.access-key-id": "K"}, {})
    other_endpoint = StorageContext("s3", {"s3.endpoint": "https://b", "s3.access-key-id": "K"}, {})
    other_key = StorageContext("s3", {"s3.endpoint": "https://a", "s3.access-key-id": "J"}, {})

    assert _same_s3_endpoint(same, same) is True
    assert _same_s3_endpoint(same, other_endpoint) is False
    assert _same_s3_endpoint(same, other_key) is False
