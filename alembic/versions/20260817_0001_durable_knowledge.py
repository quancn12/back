"""Create durable versioned knowledge tables."""
from alembic import op

from app.models.destination import Destination
from app.models.hotel import Hotel
from app.models.hotel_document import HotelDocument
from app.models.knowledge import DocumentChunk, DocumentVersion, IngestionJob, SourceDocument
from app.models.service_mention import ServiceMention
from app.models.travel_document import TravelDocument


revision = "20260817_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Keep this baseline independent from models added in later revisions. The
    # previous Base.metadata.create_all() made a fresh migration include future
    # tables before their own revisions ran.
    for table in (
        Destination.__table__,
        Hotel.__table__,
        HotelDocument.__table__,
        ServiceMention.__table__,
        TravelDocument.__table__,
        SourceDocument.__table__,
        DocumentVersion.__table__,
        IngestionJob.__table__,
        DocumentChunk.__table__,
    ):
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    DocumentChunk.__table__.drop(bind=bind, checkfirst=True)
    IngestionJob.__table__.drop(bind=bind, checkfirst=True)
    DocumentVersion.__table__.drop(bind=bind, checkfirst=True)
    SourceDocument.__table__.drop(bind=bind, checkfirst=True)
