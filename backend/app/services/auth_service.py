"""IOS — Auth Service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_REFRESH_TOKEN_EXPIRE_DAYS
from app.core.exceptions import (
    AuthenticationError,
    DuplicateEmailError,
    DuplicateUsernameError,
    RefreshTokenRevokedError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    generate_api_key,
    get_api_key_prefix,
    verify_api_key,
    generate_oauth_state,
)
from app.models.user import APIKey, User, UserSession
from app.schemas.common import TokenPair
from app.schemas.user import APIKeyCreate, APIKeyCreated, LoginRequest, RegisterRequest
from app.services.base import BaseService


class AuthService(BaseService):
    """Handles authentication, token lifecycle, and API key management."""

    async def register(self, data: RegisterRequest) -> User:
        async with self._span("register"):
            async with self._transaction() as uow:
                if await uow.users.email_exists(data.email):
                    raise DuplicateEmailError("Email already registered.")
                if await uow.users.username_exists(data.username):
                    raise DuplicateUsernameError("Username already taken.")

                user = User(
                    email=data.email.lower(),
                    username=data.username,
                    hashed_password=hash_password(data.password),
                    full_name=data.full_name,
                )
                await uow.users.create(user)
                self._log.info("user_registered", user_id=str(user.id))
                return user

    async def login(self, data: LoginRequest) -> tuple[User, TokenPair]:
        async with self._span("login"):
            async with self._transaction() as uow:
                user = await uow.users.get_by_email(data.email)
                if not user or not user.hashed_password:
                    raise AuthenticationError("Invalid email or password.")
                if not verify_password(data.password, user.hashed_password):
                    raise AuthenticationError("Invalid email or password.")
                if not user.is_active:
                    raise AuthenticationError("Account is deactivated.")

                await uow.users.increment_login_count(user)
                tokens = self._issue_tokens(str(user.id), {"roles": [user.role]})
                await self._store_refresh_token(uow, user.id, tokens.refresh_token)
                self._log.info("user_login", user_id=str(user.id))
                return user, tokens

    async def refresh(self, refresh_token: str) -> TokenPair:
        async with self._span("refresh"):
            from app.core.security import decode_refresh_token
            try:
                payload = decode_refresh_token(refresh_token)
            except (TokenExpiredError, TokenInvalidError):
                raise

            user_id = UUID(payload["sub"])
            token_hash = hash_refresh_token(refresh_token)

            async with self._transaction() as uow:
                user = await uow.users.get_by_id(user_id, raise_not_found=True)
                if not user.is_active:
                    raise AuthenticationError("Account is deactivated.")

                # Validate stored hash
                stored = await self._find_refresh_token(uow, user_id, token_hash)
                if stored is None:
                    raise RefreshTokenRevokedError("Refresh token not recognised or revoked.")

                # Rotate: revoke old, issue new
                await uow.users.revoke_api_key(stored)  # reuse revoke pattern
                tokens = self._issue_tokens(str(user.id), {"roles": [user.role]})
                await self._store_refresh_token(uow, user_id, tokens.refresh_token)
                return tokens

    async def logout(self, refresh_token: str) -> None:
        async with self._span("logout"):
            token_hash = hash_refresh_token(refresh_token)
            async with self._transaction() as uow:
                session = await uow.users.get_session_by_token_hash(token_hash)
                if session:
                    await uow.users.deactivate_session(session)

    async def validate_api_key(self, raw_key: str) -> User:
        """Validate an API key and return the owning user."""
        async with self._span("validate_api_key"):
            from app.core.security import _hash_api_key  # noqa: PLC2701
            key_hash = _hash_api_key(raw_key)
            async with self._transaction() as uow:
                api_key = await uow.users.get_api_key_by_hash(key_hash)
                if api_key is None or not api_key.is_active:
                    raise AuthenticationError("Invalid or expired API key.")
                if not verify_api_key(raw_key, api_key.key_hash):
                    raise AuthenticationError("Invalid API key.")
                await uow.users.touch_api_key(api_key)
                user = await uow.users.get_by_id(api_key.user_id, raise_not_found=True)
                if not user.is_active:
                    raise AuthenticationError("Account is deactivated.")
                return user

    async def create_api_key(
        self, user_id: UUID, data: APIKeyCreate
    ) -> APIKeyCreated:
        async with self._span("create_api_key"):
            raw_key, key_hash = generate_api_key()
            expires_at = data.expires_at
            api_key = APIKey(
                user_id=user_id,
                name=data.name,
                key_hash=key_hash,
                key_prefix=get_api_key_prefix(raw_key),
                scopes=data.scopes,
                expires_at=expires_at,
            )
            async with self._transaction() as uow:
                await uow.users.create_api_key(api_key)
            return APIKeyCreated.model_validate(
                {**api_key.__dict__, "raw_key": raw_key}
            )

    async def revoke_api_key(self, user_id: UUID, key_id: UUID) -> None:
        async with self._span("revoke_api_key"):
            async with self._transaction() as uow:
                api_key = await uow.users.get_by_id.__func__(
                    uow.users, key_id
                )  # type: ignore[attr-defined]
                # Direct get from session
                api_key = await uow.users._session.get(APIKey, key_id)
                if api_key is None or api_key.user_id != user_id:
                    from app.core.exceptions import NotFoundError
                    raise NotFoundError("API key not found.")
                await uow.users.revoke_api_key(api_key)

    async def get_oauth_state(self) -> str:
        return generate_oauth_state()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _issue_tokens(self, subject: str, extra_claims: dict) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(subject, extra_claims),
            refresh_token=create_refresh_token(subject),
            expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def _store_refresh_token(
        self, uow, user_id: UUID, raw_refresh_token: str
    ) -> None:
        """Persist the refresh token hash as a UserSession record."""
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            days=JWT_REFRESH_TOKEN_EXPIRE_DAYS
        )
        session = UserSession(
            user_id=user_id,
            session_token_hash=hash_refresh_token(raw_refresh_token),
            expires_at=expires_at,
        )
        await uow.users.create_session(session)

    async def _find_refresh_token(
        self, uow, user_id: UUID, token_hash: str
    ) -> UserSession | None:
        return await uow.users.get_session_by_token_hash(token_hash)