"""Add durable users, credentials, OAuth identities, and opaque sessions.

Adoption-safe: every table, column, foreign key and index is created only if it
is missing. Before 20260923_0008 the app ran ``Base.metadata.create_all()`` at
startup, so a machine that pulled this model and started the app without
running migrations first already has these tables while ``alembic_version``
still reads 20260911_0004. A plain ``create_table`` then fails with "table
already exists", and since startup now refuses an un-migrated database the app
could not start at all. On an empty database the DDL is exactly as before.
"""
from alembic import op
import sqlalchemy as sa
revision = "20260922_0005"
down_revision = "20260911_0004"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _create_table(name, *elements):
    if not _inspector().has_table(name):
        op.create_table(name, *elements)


def _create_index(name, table, columns):
    if name not in {index["name"] for index in _inspector().get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _create_table("users", sa.Column("id", sa.String(36), primary_key=True), sa.Column("email", sa.String(320), nullable=False, unique=True), sa.Column("email_normalized", sa.String(320), nullable=False, unique=True), sa.Column("display_name", sa.String(160)), sa.Column("status", sa.String(24), nullable=False, server_default="active"), sa.Column("email_verified_at", sa.DateTime()), sa.Column("avatar_url", sa.String(2048)), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    _create_index("ix_users_email", "users", ["email"]); _create_index("ix_users_email_normalized", "users", ["email_normalized"])
    _create_table("password_credentials", sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True), sa.Column("password_hash", sa.String(512), nullable=False), sa.Column("password_updated_at", sa.DateTime(), nullable=False))
    _create_table("oauth_identities", sa.Column("id", sa.String(36), primary_key=True), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("provider_subject", sa.String(255), nullable=False), sa.Column("provider_email", sa.String(320)), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("provider", "provider_subject", name="uq_oauth_identity_provider_subject"))
    _create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"])
    _create_table("app_sessions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("csrf_token_hash", sa.String(64), nullable=False), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("expires_at", sa.DateTime(), nullable=False), sa.Column("last_seen_at", sa.DateTime(), nullable=False), sa.Column("revoked_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False))
    _create_index("ix_app_sessions_token_hash", "app_sessions", ["token_hash"]); _create_index("ix_app_sessions_user_id", "app_sessions", ["user_id"]); _create_index("ix_app_sessions_expires_at", "app_sessions", ["expires_at"])
    _create_table("auth_oauth_states", sa.Column("id", sa.String(36), primary_key=True), sa.Column("state_hash", sa.String(64), nullable=False, unique=True), sa.Column("session_id", sa.String(36), sa.ForeignKey("app_sessions.id", ondelete="CASCADE"), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("redirect_uri", sa.String(2048), nullable=False), sa.Column("nonce", sa.String(128), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=False), sa.Column("consumed_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False))
    _create_index("ix_auth_oauth_states_state_hash", "auth_oauth_states", ["state_hash"])
    _create_index("ix_auth_oauth_states_session_id", "auth_oauth_states", ["session_id"])
    _create_index("ix_auth_oauth_states_expires_at", "auth_oauth_states", ["expires_at"])
    # create_all() never alters an existing table, so on an adopted database the
    # ownership columns are normally still missing even though the auth tables
    # exist. A column that is already present was created together with its
    # foreign key, so the key is only added alongside a column added here.
    inspector = _inspector()
    columns = {column["name"] for column in inspector.get_columns("unified_conversations")}
    indexes = {index["name"] for index in inspector.get_indexes("unified_conversations")}
    add_user = "owner_user_id" not in columns
    add_session = "owner_session_id" not in columns
    add_user_index = "ix_unified_conversations_owner_user_id" not in indexes
    add_session_index = "ix_unified_conversations_owner_session_id" not in indexes
    if add_user or add_session or add_user_index or add_session_index:
        with op.batch_alter_table("unified_conversations") as batch:
            if add_user:
                batch.add_column(sa.Column("owner_user_id", sa.String(36), nullable=True))
                batch.create_foreign_key("fk_unified_conversations_owner_user", "users", ["owner_user_id"], ["id"], ondelete="CASCADE")
            if add_session:
                batch.add_column(sa.Column("owner_session_id", sa.String(36), nullable=True))
                batch.create_foreign_key("fk_unified_conversations_owner_session", "app_sessions", ["owner_session_id"], ["id"], ondelete="CASCADE")
            if add_user_index:
                batch.create_index("ix_unified_conversations_owner_user_id", ["owner_user_id"])
            if add_session_index:
                batch.create_index("ix_unified_conversations_owner_session_id", ["owner_session_id"])

def downgrade() -> None:
    with op.batch_alter_table("unified_conversations") as batch:
        batch.drop_index("ix_unified_conversations_owner_session_id")
        batch.drop_index("ix_unified_conversations_owner_user_id")
        batch.drop_constraint("fk_unified_conversations_owner_session", type_="foreignkey")
        batch.drop_constraint("fk_unified_conversations_owner_user", type_="foreignkey")
        batch.drop_column("owner_session_id")
        batch.drop_column("owner_user_id")
    op.drop_table("auth_oauth_states"); op.drop_table("app_sessions"); op.drop_table("oauth_identities"); op.drop_table("password_credentials"); op.drop_table("users")
