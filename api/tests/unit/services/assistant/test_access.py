import pytest_asyncio
from conftest import seed_workspace

from api.models.catalog import WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.user import User
from api.services.assistant.access import service_account_can_write


@pytest_asyncio.fixture
async def sa(db_session) -> User:
    account = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def test_writer_role_on_open_catalog_can_write(db_session, sa):
    ws, _ = await seed_workspace(db_session, user_id=sa.id, role="writer")
    assert await service_account_can_write(db_session, ws.id, sa.id) is True


async def test_reader_role_cannot_write(db_session, sa):
    ws, _ = await seed_workspace(db_session, user_id=sa.id, role="reader")
    assert await service_account_can_write(db_session, ws.id, sa.id) is False


async def test_non_member_cannot_write(db_session, sa):
    ws, _ = await seed_workspace(db_session, user_id=sa.id, role=None)
    assert await service_account_can_write(db_session, ws.id, sa.id) is False


async def test_writer_grant_on_scoped_catalog_can_write(db_session, sa):
    ws, catalog = await seed_workspace(db_session, user_id=sa.id, role="reader")
    # Flip the attachment to scoped so the role no longer implies write, then grant.
    attachment = await db_session.get(WorkspaceCatalog, (ws.id, catalog.id))
    attachment.access_mode = "scoped"
    db_session.add(CatalogGrant(user_id=sa.id, catalog_id=catalog.id, tier="writer"))
    await db_session.commit()
    assert await service_account_can_write(db_session, ws.id, sa.id) is True
