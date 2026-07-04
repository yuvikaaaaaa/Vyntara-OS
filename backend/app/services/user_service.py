"""IOS — User Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import UserRole
from app.core.exceptions import (
    AuthorizationError,
    DuplicateEmailError,
    DuplicateUsernameError,
    NotFoundError,
    UserNotFoundError,
)
from app.models.user import User, UserPreference
from app.schemas.user import (
    AdminUserUpdate,
    APIKeyRead,
    UserPreferenceRead,
    UserPreferenceSet,
    UserRead,
    UserSessionRead,
    UserUpdate,
)
from app.services.base import BaseService


class UserService(BaseService):
    """User profile management, preferences, and session listing."""

    async def get_user(self, user_id: UUID) -> User:
        async with self._transaction() as uow:
            user = await uow.users.get_by_id(user_id)
            if not user or user.is_deleted:
                raise UserNotFoundError(f"User {user_id} not found.")
            return user

    async def update_profile(self, user_id: UUID, data: UserUpdate) -> User:
        async with self._span("update_profile"):
            async with self._transaction() as uow:
                user = await uow.users.get_by_id(user_id, raise_not_found=True)
                updates = data.model_dump(exclude_none=True)
                if "metadata" in updates:
                    updates["metadata_"] = updates.pop("metadata")
                await uow.users.update(user, updates)
                return user

    async def admin_update_user(
        self,
        target_user_id: UUID,
        data: AdminUserUpdate,
        acting_user_id: UUID,
    ) -> User:
        """Admin-only: change role, active status, verified status."""
        async with self._span("admin_update_user"):
            if acting_user_id == target_user_id and data.is_active is False:
                raise AuthorizationError("Cannot deactivate your own account.")
            async with self._transaction() as uow:
                user = await uow.users.get_by_id(target_user_id, raise_not_found=True)
                updates = data.model_dump(exclude_none=True)
                await uow.users.update(user, updates)
                self._log.info(
                    "admin_user_updated",
                    target=str(target_user_id),
                    actor=str(acting_user_id),
                    changes=list(updates.keys()),
                )
                return user

    async def delete_user(self, user_id: UUID, acting_user_id: UUID) -> None:
        """Soft-delete a user account."""
        if user_id == acting_user_id:
            raise AuthorizationError("Cannot delete your own account.")
        async with self._transaction() as uow:
            user = await uow.users.get_by_id(user_id, raise_not_found=True)
            await uow.users.soft_delete(user)
            self._log.info("user_deleted", user_id=str(user_id), actor=str(acting_user_id))

    async def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        role: UserRole | None = None,
    ) -> tuple[list[User], int]:
        filters = []
        if role:
            filters.append(User.role == role)
        async with self._transaction() as uow:
            return await uow.users.paginate(
                page=page,
                page_size=page_size,
                filters=filters or None,
                order_by=User.created_at,
                descending=True,
            )

    async def list_sessions(self, user_id: UUID) -> list[UserSessionRead]:
        async with self._transaction() as uow:
            sessions = await uow.users.get_active_sessions(user_id)
            return [UserSessionRead.model_validate(s) for s in sessions]

    async def list_api_keys(self, user_id: UUID) -> list[APIKeyRead]:
        async with self._transaction() as uow:
            keys = await uow.users.list_api_keys(user_id)
            return [APIKeyRead.model_validate(k) for k in keys]

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    async def set_preference(
        self, user_id: UUID, data: UserPreferenceSet
    ) -> UserPreferenceRead:
        async with self._transaction() as uow:
            pref = UserPreference(
                user_id=user_id,
                preference_key=data.preference_key,
                value_json=data.value_json,
                description=data.description,
            )
            saved = await uow.users.upsert_preference(pref)
            return UserPreferenceRead.model_validate(saved)

    async def list_preferences(self, user_id: UUID) -> list[UserPreferenceRead]:
        async with self._transaction() as uow:
            prefs = await uow.users.list_preferences(user_id)
            return [UserPreferenceRead.model_validate(p) for p in prefs]

    async def get_preference(
        self, user_id: UUID, key: str
    ) -> UserPreferenceRead:
        async with self._transaction() as uow:
            pref = await uow.users.get_preference(user_id, key)
            if pref is None:
                raise NotFoundError(f"Preference '{key}' not found.")
            return UserPreferenceRead.model_validate(pref)