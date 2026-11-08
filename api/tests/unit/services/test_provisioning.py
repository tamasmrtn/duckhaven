import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from api.models.user import User
from api.services.auth import get_user_by_email
from api.services.provisioning import ProvisioningRefused, provision_federated_user


async def _provision(db, *, email, subject, provider="oidc:entra", **kw):
    kw.setdefault("name", "N")
    kw.setdefault("role", "user")
    kw.setdefault("email_verified", True)
    return await provision_federated_user(db, email=email, subject=subject, provider=provider, **kw)


async def _add(db, **fields) -> User:
    fields.setdefault("name", "N")
    fields.setdefault("role", "user")
    user = User(password_hash=None, **fields)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_provision_creates_federated_user(db_session):
    user = await provision_federated_user(
        db_session,
        email="alice@corp.com",
        name="Alice",
        subject="sub-1",
        provider="oidc:entra",
        role="admin",
        email_verified=False,
    )
    assert user.id is not None
    assert user.password_hash is None
    assert user.auth_provider == "oidc:entra"
    assert user.external_subject == "sub-1"
    assert user.role == "admin"


async def test_provision_resyncs_name_and_role(db_session):
    await _provision(db_session, email="bob@corp.com", subject="s", name="Bob")
    updated = await _provision(
        db_session, email="bob@corp.com", subject="s", name="Robert", role="admin"
    )
    assert updated.name == "Robert"
    assert updated.role == "admin"


async def test_the_same_subject_binds_without_a_verified_email(db_session):
    first = await _provision(db_session, email="bob@corp.com", subject="s", email_verified=False)
    again = await _provision(db_session, email="bob@corp.com", subject="s", email_verified=False)
    assert again.id == first.id


async def test_provision_rejects_provider_collision(db_session):
    await _add(db_session, email="carol@corp.com", auth_provider="local")

    with pytest.raises(HTTPException) as exc:
        await _provision(db_session, email="carol@corp.com", subject="s")
    assert exc.value.status_code == 403


async def test_a_second_provider_cannot_take_over_an_account(db_session):
    original = await _provision(db_session, email="eve@corp.com", subject="sub-9")

    with pytest.raises(ProvisioningRefused):
        await _provision(
            db_session, email="eve@corp.com", subject="other-sub", provider="oidc:google"
        )

    await db_session.refresh(original)
    assert (original.auth_provider, original.external_subject) == ("oidc:entra", "sub-9")


async def test_another_subject_of_the_same_provider_is_refused(db_session):
    original = await _provision(db_session, email="eve@corp.com", subject="sub-9")

    with pytest.raises(ProvisioningRefused):
        await _provision(db_session, email="eve@corp.com", subject="sub-10")

    await db_session.refresh(original)
    assert original.external_subject == "sub-9"


@pytest.mark.parametrize("verified", [True, False])
async def test_an_unbound_account_binds_only_on_a_verified_email(db_session, verified):
    unbound = await _add(db_session, email="fay@corp.com", auth_provider="oidc:entra")

    if verified:
        user = await _provision(db_session, email="fay@corp.com", subject="sub-3")
        assert user.id == unbound.id
        assert user.external_subject == "sub-3"
    else:
        with pytest.raises(ProvisioningRefused):
            await _provision(
                db_session, email="fay@corp.com", subject="sub-3", email_verified=False
            )
    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_a_legacy_account_is_relabelled_on_its_subject(db_session):
    legacy = await _add(
        db_session, email="gus@corp.com", auth_provider="oidc", external_subject="sub-4"
    )

    user = await _provision(
        db_session,
        email="gus@corp.com",
        subject="sub-4",
        email_verified=False,
        legacy_provider="oidc",
    )
    assert user.id == legacy.id
    assert user.auth_provider == "oidc:entra"


async def test_a_legacy_account_with_another_email_is_not_relabelled(db_session):
    legacy = await _add(
        db_session, email="gus@corp.com", auth_provider="oidc", external_subject="sub-4"
    )

    user = await _provision(
        db_session, email="hal@corp.com", subject="sub-4", legacy_provider="oidc"
    )
    assert user.id != legacy.id
    await db_session.refresh(legacy)
    assert legacy.auth_provider == "oidc"


async def test_a_moved_directory_entry_keeps_its_account(db_session):
    """An LDAP DN changes when an entry moves; its verified address keeps the account."""
    original = await _provision(
        db_session, email="ivy@corp.com", subject="cn=ivy,ou=a", provider="ldap"
    )

    moved = await _provision(
        db_session,
        email="ivy@corp.com",
        subject="cn=ivy,ou=b",
        provider="ldap",
        rebind_on_verified_email=True,
    )
    assert moved.id == original.id
    assert moved.external_subject == "cn=ivy,ou=b"


async def test_provision_rejects_disabled_user(db_session):
    await _provision(db_session, email="dan@corp.com", subject="s", provider="ldap")
    dan = await get_user_by_email(db_session, "dan@corp.com")
    dan.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _provision(db_session, email="dan@corp.com", subject="s", provider="ldap")
    assert exc.value.status_code == 403
