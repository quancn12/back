"""Add the unified stay, privacy, conversation, and hotel service foundation."""
from alembic import op
import sqlalchemy as sa


revision = "20260911_0003"
down_revision = "20260817_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hotel_rooms",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("hotel_id", sa.String(), sa.ForeignKey("hotels.id"), nullable=False),
        sa.Column("room_number", sa.String(), nullable=False),
        sa.Column("connect_code_hash", sa.String(64), nullable=False),
        sa.Column("verification_pin_hash", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("hotel_id", "room_number", name="uq_hotel_room"),
        sa.UniqueConstraint("connect_code_hash"),
    )
    op.create_index("ix_hotel_rooms_hotel_id", "hotel_rooms", ["hotel_id"])
    op.create_index("ix_hotel_rooms_room_number", "hotel_rooms", ["room_number"])
    op.create_index("ix_hotel_rooms_connect_code_hash", "hotel_rooms", ["connect_code_hash"])

    op.create_table(
        "stays",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("hotel_id", sa.String(), sa.ForeignKey("hotels.id"), nullable=False),
        sa.Column("room_id", sa.String(), sa.ForeignKey("hotel_rooms.id"), nullable=False),
        sa.Column("guest_alias", sa.String(), nullable=False),
        sa.Column("check_in_at", sa.DateTime(), nullable=False),
        sa.Column("check_out_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("hotel_id", "room_id", "check_in_at", "check_out_at", "status"):
        op.create_index(f"ix_stays_{column}", "stays", [column])

    op.create_table(
        "stay_connection_challenges",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("room_id", sa.String(), sa.ForeignKey("hotel_rooms.id"), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_stay_connection_challenges_room_id", "stay_connection_challenges", ["room_id"])
    op.create_index("ix_stay_connection_challenges_expires_at", "stay_connection_challenges", ["expires_at"])

    op.create_table(
        "stay_access_grants",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("stay_id", sa.String(), sa.ForeignKey("stays.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_stay_access_grants_stay_id", "stay_access_grants", ["stay_id"])
    op.create_index("ix_stay_access_grants_token_hash", "stay_access_grants", ["token_hash"])
    op.create_index("ix_stay_access_grants_expires_at", "stay_access_grants", ["expires_at"])

    op.create_table(
        "hotel_knowledge_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("hotel_id", sa.String(), sa.ForeignKey("hotels.id"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_hotel_knowledge_items_hotel_id", "hotel_knowledge_items", ["hotel_id"])
    op.create_index("ix_hotel_knowledge_items_category", "hotel_knowledge_items", ["category"])
    op.create_index("ix_hotel_knowledge_items_visibility", "hotel_knowledge_items", ["visibility"])

    op.create_table(
        "hotel_service_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("hotel_id", sa.String(), sa.ForeignKey("hotels.id"), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("hours", sa.String(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("hotel_id", "code", name="uq_hotel_service_code"),
    )
    op.create_index("ix_hotel_service_items_hotel_id", "hotel_service_items", ["hotel_id"])
    op.create_index("ix_hotel_service_items_code", "hotel_service_items", ["code"])
    op.create_index("ix_hotel_service_items_category", "hotel_service_items", ["category"])

    op.create_table(
        "unified_conversations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("destination_slug", sa.String(), nullable=True),
        sa.Column("active_stay_id", sa.String(), sa.ForeignKey("stays.id"), nullable=True),
        sa.Column("preferred_mode", sa.String(16), nullable=False),
        sa.Column("locale", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_active", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_unified_conversations_destination_slug", "unified_conversations", ["destination_slug"])
    op.create_index("ix_unified_conversations_active_stay_id", "unified_conversations", ["active_stay_id"])
    op.create_index("ix_unified_conversations_last_active", "unified_conversations", ["last_active"])

    op.create_table(
        "unified_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("unified_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("context_type", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_unified_messages_conversation_id", "unified_messages", ["conversation_id"])

    op.create_table(
        "consent_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("unified_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("conversation_id", "purpose", name="uq_conversation_consent"),
    )
    op.create_index("ix_consent_records_conversation_id", "consent_records", ["conversation_id"])
    op.create_index("ix_consent_records_purpose", "consent_records", ["purpose"])

    op.create_table(
        "service_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("hotel_id", sa.String(), sa.ForeignKey("hotels.id"), nullable=False),
        sa.Column("stay_id", sa.String(), sa.ForeignKey("stays.id"), nullable=False),
        sa.Column("service_item_id", sa.String(), sa.ForeignKey("hotel_service_items.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("stay_id", "idempotency_key", name="uq_stay_idempotency"),
    )
    for column in ("hotel_id", "stay_id", "service_item_id", "status", "idempotency_key"):
        op.create_index(f"ix_service_requests_{column}", "service_requests", [column])


def downgrade() -> None:
    for table in (
        "service_requests",
        "consent_records",
        "unified_messages",
        "unified_conversations",
        "hotel_service_items",
        "hotel_knowledge_items",
        "stay_access_grants",
        "stay_connection_challenges",
        "stays",
        "hotel_rooms",
    ):
        op.drop_table(table)
