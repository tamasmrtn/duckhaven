"""LDAP / Active Directory authentication via service-account search + user bind.

Flow: bind as the configured service account, search for the user by email,
re-bind as the user's DN with the submitted password to verify it, then map the
user's ``memberOf`` groups to a DuckHaven role and JIT-provision the account.

``ldap3`` is synchronous, so the blocking work runs in a worker thread. Any LDAP
error (server down, bad service creds, user not found, wrong password) results in
a denied login — the local-first path in ``services.auth`` has already handled
the break-glass admin, so failures here never lock anyone out.
"""

from __future__ import annotations

import logging
import ssl

from ldap3 import AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from api.config import settings
from api.models.user import User
from api.services.provisioning import provision_federated_user, resolve_role

logger = logging.getLogger(__name__)


def _claims_from_directory(email: str, password: str) -> dict | None:
    """Verify credentials against LDAP and return identity claims, or None."""
    tls = Tls(
        validate=ssl.CERT_REQUIRED,
        ca_certs_file=settings.ldap_tls_ca_cert,
    )
    use_ssl = bool(settings.ldap_server_uri and settings.ldap_server_uri.startswith("ldaps"))
    server = Server(
        settings.ldap_server_uri,
        use_ssl=use_ssl,
        tls=tls if (use_ssl or settings.ldap_use_start_tls) else None,
        connect_timeout=settings.ldap_timeout_s,
    )
    auto_bind = AUTO_BIND_TLS_BEFORE_BIND if settings.ldap_use_start_tls else AUTO_BIND_NO_TLS

    search_filter = settings.ldap_user_filter.format(email=escape_filter_chars(email))
    conn = Connection(
        server,
        user=settings.ldap_bind_dn,
        password=settings.ldap_bind_password,
        auto_bind=auto_bind,
        receive_timeout=settings.ldap_timeout_s,
    )
    try:
        conn.search(
            settings.ldap_user_search_base,
            search_filter,
            attributes=[
                settings.ldap_email_attr,
                settings.ldap_name_attr,
                settings.ldap_group_attr,
            ],
        )
        if not conn.entries:
            return None
        entry = conn.entries[0]
        user_dn = entry.entry_dn

        # Re-bind as the user to actually verify the password.
        user_conn = Connection(server, user=user_dn, password=password)
        if settings.ldap_use_start_tls:
            user_conn.open()
            user_conn.start_tls()
        if not user_conn.bind():
            return None
        user_conn.unbind()

        groups = [str(v) for v in (entry[settings.ldap_group_attr].values or [])]
        name = str(entry[settings.ldap_name_attr].value or email)
        return {"dn": user_dn, "email": email, "name": name, "groups": groups}
    finally:
        conn.unbind()


async def authenticate_ldap(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate ``email``/``password`` against LDAP, provisioning on success."""
    try:
        claims = await run_in_threadpool(_claims_from_directory, email, password)
    except LDAPException:
        logger.warning("LDAP authentication failed for a login attempt", exc_info=False)
        return None
    if claims is None:
        return None
    role = resolve_role(claims["groups"], settings.ldap_group_role_map)
    return await provision_federated_user(
        db,
        email=claims["email"],
        name=claims["name"],
        subject=claims["dn"],
        provider="ldap",
        role=role,
    )
