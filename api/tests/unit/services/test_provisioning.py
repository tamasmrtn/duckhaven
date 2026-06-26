import pytest
from fastapi import HTTPException

from api.models.user import User
from api.services.provisioning import provision_federated_user


async def test_provision_creates_federated_user(db_session):
    user = await provision_federated_user(
        db_session,
        email="alice@corp.com",
        name="Alice",
        subject="sub-1",
        provider="oidc",
        role="admin",
    )
    assert user.id is not None
    assert user.password_hash is None
    assert user.auth_provider == "oidc"
    assert user.external_subject == "sub-1"
    assert user.role == "admin"


async def test_provision_resyncs_name_and_role(db_session):
    await provision_federated_user(
        db_session, email="bob@corp.com", name="Bob", subject="s", provider="oidc", role="user"
    )
    updated = await provision_federated_user(
        db_session,
        email="bob@corp.com",
        name="Robert",
        subject="s",
        provider="oidc",
        role="admin",
    )
    assert updated.name == "Robert"
    assert updated.role == "admin"


async def test_provision_rejects_provider_collision(db_session):
    local = User(
        email="carol@corp.com", name="Carol", password_hash="x", role="admin", auth_provider="local"
    )
    db_session.add(local)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await provision_federated_user(
            db_session,
            email="carol@corp.com",
            name="Carol",
            subject="s",
            provider="oidc",
            role="user",
        )
    assert exc.value.status_code == 403


async def test_provision_rejects_disabled_user(db_session):
    await provision_federated_user(
        db_session, email="dan@corp.com", name="Dan", subject="s", provider="ldap", role="user"
    )
    # Re-fetch and disable.
    from api.services.auth import get_user_by_email

    dan = await get_user_by_email(db_session, "dan@corp.com")
    dan.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await provision_federated_user(
            db_session, email="dan@corp.com", name="Dan", subject="s", provider="ldap", role="user"
        )
    assert exc.value.status_code == 403
