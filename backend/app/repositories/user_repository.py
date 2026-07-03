"""IOS — User Repository."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, select, update

from app.models.user import APIKey, OAuthAccount, User, UserPreference, UserSession
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        return await self.get_by_field(User.email, email.lower())

    async def get_by_username(self, username: str) -> User | None:
        return await self.get_by_field(User.username, username)

    async def email_exists(self, email: str) -> bool:
        from sqlalchemy import func
        stmt = select(func.count()).select_from(User).where(
            User.email == email.lower(),
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def username_exists(self, username: str) -> bool:
        from sqlalchemy import func
        stmt = select(func.count()).select_from(User).where(
            User.username == username,
            User.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def increment_login_count(self, user: User) -> None:
        stmt = (
            update(User)
            .where(User.id == user.id)
            .values(
                login_count=User.login_count + 1,
                last_login_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # UserSession
    # ------------------------------------------------------------------

    async def create_session(self, session_obj: UserSession) -> UserSession:
        self._session.add(session_obj)
        await self._session.flush()
        await self._session.refresh(session_obj)
        return session_obj

    async def get_session_by_token_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(
            UserSession.session_token_hash == token_hash,
            UserSession.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def deactivate_session(self, session_obj: UserSession) -> None:
        session_obj.is_active = False
        await self._session.flush()

    async def get_active_sessions(self, user_id: UUID) -> list[UserSession]:
        stmt = select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.is_active.is_(True),
            UserSession.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # APIKey
    # ------------------------------------------------------------------

    async def create_api_key(self, api_key: APIKey) -> APIKey:
        self._session.add(api_key)
        await self._session.flush()
        await self._session.refresh(api_key)
        return api_key

    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        stmt = select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_api_keys(self, user_id: UUID) -> list[APIKey]:
        stmt = select(APIKey).where(
            APIKey.user_id == user_id,
            APIKey.deleted_at.is_(None),
        ).order_by(APIKey.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def revoke_api_key(self, api_key: APIKey) -> None:
        api_key.revoked_at = datetime.now(tz=timezone.utc)
        await self._session.flush()

    async def touch_api_key(self, api_key: APIKey) -> None:
        api_key.last_used_at = datetime.now(tz=timezone.utc)
        await self._session.flush()

    # ------------------------------------------------------------------
    # UserPreference
    # ------------------------------------------------------------------

    async def get_preference(
        self, user_id: UUID, key: str
    ) -> UserPreference | None:
        stmt = select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.preference_key == key,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def upsert_preference(self, pref: UserPreference) -> UserPreference:
        existing = await self.get_preference(pref.user_id, pref.preference_key)
        if existing:
            existing.value_json = pref.value_json
            existing.description = pref.description
            await self._session.flush()
            return existing
        self._session.add(pref)
        await self._session.flush()
        await self._session.refresh(pref)
        return pref

    async def list_preferences(self, user_id: UUID) -> list[UserPreference]:
        stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # OAuthAccount
    # ------------------------------------------------------------------

    async def get_oauth_account(
        self, provider: str, provider_user_id: str
    ) -> OAuthAccount | None:
        stmt = select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == provider_user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create_oauth_account(self, account: OAuthAccount) -> OAuthAccount:
        self._session.add(account)
        await self._session.flush()
        await self._session.refresh(account)
        return account

    async def list_oauth_accounts(self, user_id: UUID) -> list[OAuthAccount]:
        stmt = select(OAuthAccount).where(OAuthAccount.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())