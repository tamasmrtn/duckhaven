import api.services.ldap as ldap_mod
from api.config import settings
from api.models.user import User
from api.services.auth import authenticate_password, hash_password


async def _local_user(db_session) -> User:
    user = User(
        email="break@glass.local",
        name="Glass",
        password_hash=hash_password("pw"),
        role="admin",
        auth_provider="local",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_local_first_beats_ldap(db_session, monkeypatch):
    """A local password is always verified locally; LDAP is never consulted."""
    await _local_user(db_session)
    monkeypatch.setattr(settings, "ldap_enabled", True)

    def _boom(*_args, **_kwargs):
        raise AssertionError("LDAP must not be called for a local account")

    monkeypatch.setattr(ldap_mod, "_claims_from_directory", _boom)
    user = await authenticate_password(db_session, "break@glass.local", "pw")
    assert user is not None
    assert user.auth_provider == "local"


async def test_ldap_fallback_provisions_user(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ldap_enabled", True)
    monkeypatch.setattr(settings, "ldap_group_role_map", {"cn=admins": "admin"})
    monkeypatch.setattr(
        ldap_mod,
        "_claims_from_directory",
        lambda email, password: {
            "dn": "cn=dana,dc=corp",
            "email": email,
            "name": "Dana",
            "groups": ["cn=admins"],
        },
    )
    user = await authenticate_password(db_session, "dana@corp.com", "pw")
    assert user is not None
    assert user.auth_provider == "ldap"
    assert user.role == "admin"


async def test_ldap_bind_failure_denies(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ldap_enabled", True)
    monkeypatch.setattr(ldap_mod, "_claims_from_directory", lambda email, password: None)
    assert await authenticate_password(db_session, "nobody@corp.com", "pw") is None


async def test_ldap_disabled_unknown_email_denies(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ldap_enabled", False)
    assert await authenticate_password(db_session, "ghost@corp.com", "pw") is None
