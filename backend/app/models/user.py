"""
Intelligence Operating System — User Models
============================================
ORM models for the identity and authentication domain:

``User``            — Principal identity record with role and OAuth support.
``UserSession``     — Active conversation / browser session tracking.
``APIKey``          — Hashed API keys for programmatic access.
``UserPreference``  — Per-user configuration key-value store.

All models inherit from ``Base``, ``UUIDPrimaryKeyMixin``, and ``AuditMixin``
(which adds ``created_at``, ``updated_at``, ``deleted_at`` and soft-delete helpers).

Foreign-key relationships:
    UserSession   → User  (cascade delete)
    APIKey        → User  (cascade delete)
    UserPreference → User (cascade delete)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import OAuthProvider, UserRole
from app.models.base import AuditMixin, Base, UUIDPrimaryKeyMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent import AgentTask
    from app.models.audit import AuditLog
    from app.models.conversation import Conversation


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class User(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Core identity record for every principal that interacts with IOS.

    Authentication modes:
    - Password-based (``hashed_password`` is set)
    - OAuth-only (``hashed_password`` is NULL; ``oauth_accounts`` is populated)

    Roles follow the hierarchy defined in ``UserRole``:
        ADMIN > OPERATOR > ANALYST > VIEWER > API_CLIENT

    Soft-delete via ``deleted_at`` (inherited from ``AuditMixin``).
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_username", "username"),
        Index("ix_users_is_active", "is_active"),
        Index("ix_users_role", "role"),
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("username", name="uq_users_username"),
        {"comment": "Principal identity records for all IOS users."},
    )

    # ------------------------------------------------------------------
    # Identity fields
    # ------------------------------------------------------------------
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Primary email address. Must be unique across all users.",
    )
    username: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Display username. Must be unique.",
    )
    full_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Optional display name.",
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="URL of the user's profile avatar.",
    )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    hashed_password: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="bcrypt hash of the user's password. NULL for OAuth-only users.",
    )

    # ------------------------------------------------------------------
    # Status & role
    # ------------------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="False = account deactivated; login is denied.",
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True = email address has been verified.",
    )
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role_enum", create_type=True),
        nullable=False,
        default=UserRole.VIEWER,
        server_default=text("'viewer'"),
        doc="Primary role determining permission level.",
    )

    # ------------------------------------------------------------------
    # Activity tracking
    # ------------------------------------------------------------------
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp of the most recent successful authentication.",
    )
    login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Total lifetime login count.",
    )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Flexible metadata bag (timezone, locale, onboarding flags, etc.).",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="All browser/API sessions belonging to this user.",
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        "APIKey",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="API keys issued to this user.",
    )
    preferences: Mapped[list["UserPreference"]] = relationship(
        "UserPreference",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="Per-user preference key-value records.",
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="All conversations initiated by this user.",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
        doc="Audit log entries attributed to this user (no cascade delete).",
    )
    tasks: Mapped[list["AgentTask"]] = relationship(
        "AgentTask",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="Agent tasks submitted by this user.",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


# ---------------------------------------------------------------------------
# UserSession
# ---------------------------------------------------------------------------


class UserSession(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Tracks an active user browser or API session.

    Used for:
    - Grouping conversations within a browsing session
    - Session-level working memory scoping
    - Audit context for API calls

    Expired sessions are soft-deleted (``deleted_at`` set) rather than
    physically removed, to preserve the audit trail.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
        Index("ix_user_sessions_is_active", "is_active"),
        {"comment": "Active user sessions (browser and API)."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="Owning user.",
    )
    session_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        doc="SHA-256 hash of the opaque session token. Never store plaintext.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="False once the session is explicitly revoked.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="UTC timestamp when this session expires.",
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
        doc="Client IP at session creation (INET type for IPv4/IPv6).",
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="HTTP User-Agent string at session creation.",
    )
    device_fingerprint: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Optional client-side device fingerprint hash.",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship(
        "User",
        back_populates="sessions",
    )

    def __repr__(self) -> str:
        return (
            f"<UserSession id={self.id} user_id={self.user_id} "
            f"active={self.is_active}>"
        )


# ---------------------------------------------------------------------------
# APIKey
# ---------------------------------------------------------------------------


class APIKey(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Hashed API key for programmatic (non-browser) access to IOS.

    Only the SHA-256 hash of the raw key is persisted.  The raw key is
    returned to the user exactly once at creation time.

    The ``key_prefix`` (first 8 chars of the raw key) is stored in plaintext
    to allow users to identify their keys in the management UI without
    revealing the full secret.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_user_id", "user_id"),
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_revoked_at", "revoked_at"),
        {"comment": "Hashed API keys for programmatic IOS access."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Human-readable label for this key (e.g. 'CI/CD pipeline key').",
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        doc="SHA-256 hex digest of the raw API key.",
    )
    key_prefix: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        doc="First 8 characters of the raw key, stored for UI display.",
    )
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="Permission scopes granted to this key.",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp of the most recent successful use.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Optional expiry. NULL = never expires.",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="If set, the key has been permanently revoked.",
    )
    ip_allowlist: Mapped[list[str] | None] = mapped_column(
        ARRAY(INET),
        nullable=True,
        doc="Optional list of allowed source IPs. NULL = any IP.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship(
        "User",
        back_populates="api_keys",
    )

    @property
    def is_active(self) -> bool:
        """``True`` if the key has not been revoked and has not expired."""
        from datetime import timezone
        now = datetime.now(tz=timezone.utc)
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at < now:
            return False
        return True

    def __repr__(self) -> str:
        return (
            f"<APIKey id={self.id} name={self.name!r} "
            f"prefix={self.key_prefix!r} active={self.is_active}>"
        )


# ---------------------------------------------------------------------------
# UserPreference
# ---------------------------------------------------------------------------


class UserPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Per-user configuration stored as typed key-value pairs.

    Examples: default model tier, UI theme, memory retention policy,
    notification preferences, language setting.

    Uses a simple ``key`` + ``value_json`` design rather than separate columns
    so that new preference keys can be added without schema migrations.
    """

    __tablename__ = "user_preferences"
    __table_args__ = (
        Index("ix_user_preferences_user_id", "user_id"),
        UniqueConstraint("user_id", "preference_key", name="uq_user_preferences_user_key"),
        {"comment": "Per-user configuration key-value pairs."},
    )

    from app.models.base import TimestampMixin  # local import to avoid shadow

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    preference_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Preference key (e.g. 'ui.theme', 'model.default_tier').",
    )
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="JSON-serialised preference value.",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional human-readable description of this preference.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship(
        "User",
        back_populates="preferences",
    )

    def __repr__(self) -> str:
        return (
            f"<UserPreference user_id={self.user_id} "
            f"key={self.preference_key!r} value={self.value_json!r}>"
        )


# ---------------------------------------------------------------------------
# OAuthAccount  (supporting table — one user can have multiple providers)
# ---------------------------------------------------------------------------


class OAuthAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Links a ``User`` to an external OAuth2 provider account.

    A single user may have both a Google account and a GitHub account linked.
    The ``provider_user_id`` is the opaque identifier returned by the provider
    (e.g. Google's ``sub`` claim).
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        Index("ix_oauth_accounts_user_id", "user_id"),
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_oauth_accounts_provider_uid"
        ),
        {"comment": "OAuth2 provider account links per user."},
    )

    from app.models.base import TimestampMixin  # local import

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        SAEnum(OAuthProvider, name="oauth_provider_enum", create_type=True),
        nullable=False,
        doc="OAuth2 provider identifier.",
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="User identifier returned by the OAuth2 provider.",
    )
    provider_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Email address associated with the provider account.",
    )
    access_token_hint: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc="First 16 chars of the provider access token (for debugging; never full token).",
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Provider-specific extra profile data.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<OAuthAccount provider={self.provider} "
            f"provider_user_id={self.provider_user_id!r}>"
        )


# Bring TimestampMixin into scope for models that need it directly
from app.models.base import TimestampMixin  # noqa: E402, F401