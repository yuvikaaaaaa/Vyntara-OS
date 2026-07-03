"""IOS — User & Auth Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator, model_validator

from app.core.enums import OAuthProvider, UserRole
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema
from app.schemas.common import PasswordStr, ShortStr, UsernameStr


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserCreate(AppModel):
    email: EmailStr
    username: UsernameStr
    password: PasswordStr
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class UserUpdate(AppModel):
    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=1024)
    metadata_: dict | None = Field(default=None, alias="metadata")


class UserRead(AuditedSchema):
    id: UUID
    email: str
    username: str
    full_name: str | None
    avatar_url: str | None
    role: UserRole
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    login_count: int
    metadata_: dict = Field(alias="metadata")

    model_config = {"populate_by_name": True, "from_attributes": True}


class UserSummary(OrmModel):
    """Lightweight user reference embedded in other schemas."""

    id: UUID
    username: str
    full_name: str | None
    avatar_url: str | None
    role: UserRole


class AdminUserUpdate(AppModel):
    """Admin-only user mutation (role, active status)."""

    role: UserRole | None = None
    is_active: bool | None = None
    is_verified: bool | None = None


# ---------------------------------------------------------------------------
# Auth / Login
# ---------------------------------------------------------------------------


class LoginRequest(AppModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RegisterRequest(UserCreate):
    pass


class PasswordChangeRequest(AppModel):
    current_password: str = Field(min_length=1)
    new_password: PasswordStr

    @model_validator(mode="after")
    def passwords_differ(self) -> "PasswordChangeRequest":
        if self.current_password == self.new_password:
            raise ValueError("New password must differ from current password.")
        return self


class RefreshRequest(AppModel):
    refresh_token: str = Field(min_length=1)


class OAuthCallbackRequest(AppModel):
    code: str
    state: str
    provider: OAuthProvider


# ---------------------------------------------------------------------------
# UserSession
# ---------------------------------------------------------------------------


class UserSessionRead(TimestampedSchema):
    id: UUID
    user_id: UUID
    is_active: bool
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None


# ---------------------------------------------------------------------------
# APIKey
# ---------------------------------------------------------------------------


class APIKeyCreate(AppModel):
    name: ShortStr
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class APIKeyRead(TimestampedSchema):
    id: UUID
    user_id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    is_active: bool


class APIKeyCreated(APIKeyRead):
    """Returned once at creation — contains the plaintext key."""

    raw_key: str


# ---------------------------------------------------------------------------
# UserPreference
# ---------------------------------------------------------------------------


class UserPreferenceSet(AppModel):
    preference_key: ShortStr
    value_json: dict | list | str | int | float | bool | None
    description: str | None = Field(default=None, max_length=500)


class UserPreferenceRead(TimestampedSchema):
    id: UUID
    user_id: UUID
    preference_key: str
    value_json: dict | list | str | int | float | bool | None
    description: str | None


# ---------------------------------------------------------------------------
# OAuthAccount
# ---------------------------------------------------------------------------


class OAuthAccountRead(TimestampedSchema):
    id: UUID
    user_id: UUID
    provider: OAuthProvider
    provider_user_id: str
    provider_email: str | None