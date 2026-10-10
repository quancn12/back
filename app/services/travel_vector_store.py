"""Hybrid retrieval backed by SQLite for the pilot and pgvector in PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import unicodedata
from collections import OrderedDict

from sqlalchemy import text as sql_text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.knowledge import DocumentChunk, DocumentVersion, SourceDocument
from app.services.llm_client import llm_client


logger = logging.getLogger(__name__)


_STOPWORDS = {
    "anh", "ban", "biet", "cac", "cai", "can", "cho", "co", "cua", "duoc",
    "em", "gi", "hay", "hoi", "khong", "la", "lam", "minh", "mot", "muon",
    "nao", "nay", "nhung", "toi", "trong", "tu", "ve", "vui", "xin", "di",
    "den", "tai", "thong", "tin", "du", "lich",
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_search_text(value: str) -> str:
    """Canonical accent-folded text stored by both SQLite and PostgreSQL."""
    return _normalize(value)


def _tokens(query: str) -> list[str]:
    return [
        token for token in _normalize(query).split()
        if len(token) >= 3 and token not in _STOPWORDS
    ]


def _keyword_score(query: str, document: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    normalized_doc = _normalize(document)
    score = sum(
        1.0 for token in query_tokens
        if re.search(rf"\b{re.escape(token)}\b", normalized_doc)
    )
    phrase = " ".join(query_tokens)
    if len(query_tokens) >= 2 and phrase in normalized_doc:
        score += 2.0
    return score


def _sqlite_fts_expression(query: str) -> str:
    """Build a syntax-safe OR query from the same normalized ranking tokens."""
    return " OR ".join(f'"{token}"' for token in dict.fromkeys(_tokens(query)))


def _metadata_dict(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# Keyword overlap refines the order; it does not decide it. Vietnamese written
# without diacritics collides constantly at syllable level ("quan" matches
# "tham quan", "cao" matches "vung cao"), so an unrelated tour can share more
# syllables with a question than the document that actually answers it. The
# bonus is capped, so a long chunk cannot accumulate an unbounded advantage:
# at most it moves a candidate 0.25 closer, which is far less than the spread
# between a real match and an incidental one.
KEYWORD_NUDGE = 0.05
KEYWORD_NUDGE_CAP = 5.0

# Programme filenames start with the tour code, for example
# "HVTB 0004-23 1N Yen Bai - Ho Thac Ba...". After _normalize the separators
# are gone, so the code reads "hvtb 0004 23".
_FILENAME_TOUR_CODE = re.compile(r"^([a-z]{2,6}\s+\d{3,4}\s+\d{2})\b")

_POLICY_QUERY_PHRASES = (
    "huy tour",
    "huy chuong trinh",
    "chinh sach huy",
    "dieu kien huy",
    "phi huy",
    "hoan tien",
    "hoan phi",
    "hoan coc",
    "doi lich",
    "doi tour",
    "doi ngay",
    "phi doi",
    "chinh sach tour",
    "quy dinh tour",
    "dieu kien tour",
    "bao hiem tour",
    "bao hiem du lich",
    "muc phat tour",
    "dat coc tour",
    "tien coc tour",
    "thanh toan tour",
    "cancel tour",
    "tour cancellation",
    "cancellation policy",
    "refund tour",
    "tour refund",
    "reschedule tour",
    "rebook tour",
    "tour insurance",
    "cancel the tour",
    "cancel my tour",
    "cancel this tour",
    "cancellation fee",
    "refund my deposit",
    "refund for tour",
    "change tour date",
    "change travel date",
    "reschedule my trip",
)

_CJK_POLICY_QUERY_MARKERS = (
    "取消行程",
    "取消旅游",
    "取消旅行团",
    "取消费用",
    "退团",
    "退款",
    "退订旅游",
    "改期",
    "更改行程",
    "更改出行日期",
    "保险条款",
    "旅游保险",
)


def is_policy_query(query_text: str) -> bool:
    normalized = _normalize(query_text)
    raw = re.sub(r"\s+", "", query_text.casefold())
    return (
        any(phrase in normalized for phrase in _POLICY_QUERY_PHRASES)
        or any(marker in raw for marker in _CJK_POLICY_QUERY_MARKERS)
    )


def _document_code(metadata: dict) -> str:
    """Tour code of the programme a chunk belongs to, normalized like a query.

    The code is read out of the corpus rather than guessed out of the question,
    so a date or a room number in a guest's message can never be mistaken for
    one: a match requires a code that genuinely exists in the library.
    """
    name = metadata.get("filename") or metadata.get("source") or ""
    match = _FILENAME_TOUR_CODE.match(_normalize(name))
    return match.group(1) if match else ""


def _cosine_distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 1.0
    return 1.0 - (dot / (left_norm * right_norm))


def _result(
    *,
    chunk_id: str,
    document: str,
    metadata: dict,
    distance: float,
    keyword_score: float,
    vector_rank: int,
) -> dict:
    return {
        "id": chunk_id,
        "text": document,
        "metadata": metadata,
        "distance": float(distance),
        "keyword_score": float(keyword_score),
        "vector_rank": vector_rank,
        "source": metadata.get("source") or metadata.get("filename"),
    }


class TravelVectorStore:
    def __init__(self) -> None:
        self._query_embeddings: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._inflight_embeddings: dict[tuple[str, str], asyncio.Task[list[float]]] = {}
        self._cache_lock = asyncio.Lock()

    async def _embed_query(self, query_text: str) -> list[float]:
        # The vector represents this exact text under this embedding model.
        # Accent-folding or whitespace normalization here can return a vector
        # computed from a different string than the one requested.
        key = (settings.ollama_embed_model, query_text)
        async with self._cache_lock:
            if settings.embedding_query_cache_size > 0:
                cached = self._query_embeddings.get(key)
                if cached is not None:
                    self._query_embeddings.move_to_end(key)
                    return cached
            pending = self._inflight_embeddings.get(key)
            if pending is None:
                pending = asyncio.create_task(
                    self._load_query_embedding(key, query_text)
                )
                self._inflight_embeddings[key] = pending

                def forget_finished(task: asyncio.Task[list[float]]) -> None:
                    # Retrieve failures even when every caller disconnected,
                    # so a later query can retry without an unhandled task.
                    if not task.cancelled():
                        try:
                            task.exception()
                        except asyncio.CancelledError:
                            pass

                pending.add_done_callback(forget_finished)

        # A cancelled request must not cancel the shared embedding task used by
        # other simultaneous requests. `_load_query_embedding` owns cache and
        # cleanup so it also completes safely when every caller cancels.
        return await asyncio.shield(pending)

    async def _load_query_embedding(
        self, key: tuple[str, str], query_text: str
    ) -> list[float]:
        task = asyncio.current_task()
        try:
            vector = await llm_client.embed(query_text)
            if settings.embedding_query_cache_size > 0:
                async with self._cache_lock:
                    self._query_embeddings[key] = vector
                    self._query_embeddings.move_to_end(key)
                    while len(self._query_embeddings) > settings.embedding_query_cache_size:
                        self._query_embeddings.popitem(last=False)
            return vector
        finally:
            async with self._cache_lock:
                if self._inflight_embeddings.get(key) is task:
                    self._inflight_embeddings.pop(key, None)

    async def query(
        self,
        *,
        query_text: str,
        country_code: str = "VN",
        destination_slug: str | None = None,
        top_k: int = 6,
        policy_query: bool | None = None,
    ) -> list[dict]:
        try:
            query_vector = await self._embed_query(query_text)
            if len(query_vector) != settings.embedding_dimensions:
                raise RuntimeError(
                    f"Embedding dimension {len(query_vector)} khác cấu hình "
                    f"{settings.embedding_dimensions}."
                )
        except Exception as exc:
            # Exact keyword retrieval can still ground a useful answer while the
            # embedding model is loading or temporarily unavailable. Do not log
            # the guest query because chat logging is opt-in.
            logger.warning(
                "Travel query embedding unavailable; using keyword fallback: %s",
                type(exc).__name__,
            )
            candidates = await asyncio.to_thread(
                self._query_keyword_only,
                query_text,
                country_code,
                destination_slug,
            )
            return self._rank(
                candidates, top_k, query_text, policy_query=policy_query
            )
        if settings.database_url.startswith("postgresql"):
            candidates = await asyncio.to_thread(
                self._query_postgres,
                query_text,
                query_vector,
                country_code,
                destination_slug,
                top_k,
            )
        else:
            candidates = await asyncio.to_thread(
                self._query_sqlite,
                query_text,
                query_vector,
                country_code,
                destination_slug,
            )
        return self._rank(candidates, top_k, query_text, policy_query=policy_query)

    def _query_keyword_only(
        self,
        query_text: str,
        country_code: str,
        destination_slug: str | None,
    ) -> list[dict]:
        """Retrieve indexed textual matches without requiring an embedding model."""
        db = SessionLocal()
        try:
            indexed = self._query_indexed_keywords(
                db, query_text, country_code, destination_slug
            )
            if indexed is not None:
                candidates = []
                for rank, row in enumerate(indexed):
                    keyword = _keyword_score(query_text, row["text"])
                    if keyword < settings.rag_keyword_min_score:
                        continue
                    metadata = _metadata_dict(row["chunk_metadata"])
                    metadata["chunk_index"] = row["chunk_index"]
                    candidates.append(
                        _result(
                            chunk_id=row["id"],
                            document=row["text"],
                            metadata=metadata,
                            distance=1.0,
                            keyword_score=keyword,
                            vector_rank=rank,
                        )
                    )
                return candidates

            # An unversioned development database may not have the migration-
            # owned index yet. Runtime startup rejects that state, but retain a
            # bounded compatibility fallback for direct service/unit use.
            query = (
                db.query(DocumentChunk)
                .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                .join(SourceDocument, SourceDocument.id == DocumentChunk.source_document_id)
                .filter(
                    DocumentVersion.is_active.is_(True),
                    DocumentVersion.status == "done",
                    SourceDocument.status == "active",
                    DocumentChunk.country_code == country_code,
                )
            )
            if destination_slug:
                query = query.filter(DocumentChunk.destination_slug == destination_slug)

            candidates = []
            for rank, chunk in enumerate(query.all()):
                keyword = _keyword_score(query_text, chunk.text)
                if keyword < settings.rag_keyword_min_score:
                    continue
                metadata = dict(chunk.chunk_metadata or {})
                metadata["chunk_index"] = chunk.chunk_index
                candidates.append(
                    _result(
                        chunk_id=chunk.id,
                        document=chunk.text,
                        metadata=metadata,
                        distance=1.0,
                        keyword_score=keyword,
                        vector_rank=rank,
                    )
                )
            return candidates
        finally:
            db.close()

    @staticmethod
    def _query_indexed_keywords(
        db,
        query_text: str,
        country_code: str,
        destination_slug: str | None,
        *,
        limit: int = 250,
    ):
        destination_clause = (
            "AND c.destination_slug = :destination_slug" if destination_slug else ""
        )
        params = {"country_code": country_code, "limit": limit}
        if destination_slug:
            params["destination_slug"] = destination_slug
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            expression = _sqlite_fts_expression(query_text)
            if not expression:
                return []
            params["fts_query"] = expression
            statement = sql_text(
                "SELECT c.id, c.text, c.chunk_metadata, c.chunk_index "
                "FROM travel_document_chunks_fts "
                "JOIN document_chunks c ON c.id = travel_document_chunks_fts.id "
                "JOIN document_versions v ON v.id = c.document_version_id "
                "JOIN source_documents s ON s.id = c.source_document_id "
                "WHERE travel_document_chunks_fts MATCH :fts_query "
                "AND v.is_active = 1 AND v.status = 'done' AND s.status = 'active' "
                "AND c.country_code = :country_code "
                f"{destination_clause} "
                "ORDER BY bm25(travel_document_chunks_fts) LIMIT :limit"
            )
        elif dialect == "postgresql":
            params["query_text"] = normalize_search_text(query_text)
            statement = sql_text(
                "SELECT c.id, c.text, c.chunk_metadata, c.chunk_index "
                "FROM document_chunks c "
                "JOIN document_versions v ON v.id = c.document_version_id "
                "JOIN source_documents s ON s.id = c.source_document_id "
                "WHERE v.is_active = TRUE AND v.status = 'done' AND s.status = 'active' "
                "AND c.country_code = :country_code "
                f"{destination_clause} "
                "AND to_tsvector('simple', c.search_text) "
                "@@ websearch_to_tsquery('simple', :query_text) "
                "LIMIT :limit"
            )
        else:
            return None
        try:
            return db.execute(statement, params).mappings().all()
        except SQLAlchemyError as exc:
            logger.warning(
                "Travel keyword index unavailable; using compatibility scan: %s",
                type(exc).__name__,
            )
            db.rollback()
            return None

    def _query_sqlite(
        self,
        query_text: str,
        query_vector: list[float],
        country_code: str,
        destination_slug: str | None,
    ) -> list[dict]:
        db = SessionLocal()
        try:
            query = (
                db.query(DocumentChunk)
                .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                .join(SourceDocument, SourceDocument.id == DocumentChunk.source_document_id)
                .filter(
                    DocumentVersion.is_active.is_(True),
                    DocumentVersion.status == "done",
                    SourceDocument.status == "active",
                    DocumentChunk.country_code == country_code,
                )
            )
            if destination_slug:
                query = query.filter(DocumentChunk.destination_slug == destination_slug)
            rows = query.all()
            indexed = self._query_indexed_keywords(
                db, query_text, country_code, destination_slug
            )
            indexed_ids = (
                {row["id"] for row in indexed} if indexed is not None else None
            )
            candidates = []
            for rank, chunk in enumerate(rows):
                distance = _cosine_distance(query_vector, list(chunk.embedding))
                keyword = (
                    _keyword_score(query_text, chunk.text)
                    if indexed_ids is None or chunk.id in indexed_ids
                    else 0.0
                )
                if (
                    distance <= settings.rag_max_vector_distance
                    or keyword >= settings.rag_keyword_min_score
                ):
                    metadata = dict(chunk.chunk_metadata or {})
                    metadata["chunk_index"] = chunk.chunk_index
                    candidates.append(
                        _result(
                            chunk_id=chunk.id,
                            document=chunk.text,
                            metadata=metadata,
                            distance=distance,
                            keyword_score=keyword,
                            vector_rank=rank,
                        )
                    )
            return candidates
        finally:
            db.close()

    def _query_postgres(
        self,
        query_text: str,
        query_vector: list[float],
        country_code: str,
        destination_slug: str | None,
        top_k: int,
    ) -> list[dict]:
        destination_clause = "AND c.destination_slug = :destination_slug" if destination_slug else ""
        vector_literal = "[" + ",".join(f"{value:.9g}" for value in query_vector) + "]"
        limit = max(top_k * 5, top_k + 15)
        statement = sql_text(
            "SELECT c.id, c.text, c.chunk_metadata, c.chunk_index, "
            "c.embedding <=> CAST(:embedding AS vector) AS distance "
            "FROM document_chunks c "
            "JOIN document_versions v ON v.id = c.document_version_id "
            "JOIN source_documents s ON s.id = c.source_document_id "
            "WHERE v.is_active = TRUE AND v.status = 'done' AND s.status = 'active' "
            "AND c.country_code = :country_code "
            f"{destination_clause} "
            "ORDER BY c.embedding <=> CAST(:embedding AS vector) LIMIT :limit"
        )
        params = {
            "embedding": vector_literal,
            "country_code": country_code,
            "limit": limit,
        }
        if destination_slug:
            params["destination_slug"] = destination_slug
        with engine.connect() as conn:
            rows = conn.execute(statement, params).mappings().all()
            keyword_statement = sql_text(
                "SELECT c.id, c.text, c.chunk_metadata, c.chunk_index, 1.0 AS distance "
                "FROM document_chunks c "
                "JOIN document_versions v ON v.id = c.document_version_id "
                "JOIN source_documents s ON s.id = c.source_document_id "
                "WHERE v.is_active = TRUE AND v.status = 'done' AND s.status = 'active' "
                "AND c.country_code = :country_code "
                f"{destination_clause} "
                "AND to_tsvector('simple', c.search_text) "
                "@@ websearch_to_tsquery('simple', :query_text) LIMIT :limit"
            )
            keyword_params = dict(params)
            keyword_params["query_text"] = normalize_search_text(query_text)
            keyword_rows = conn.execute(keyword_statement, keyword_params).mappings().all()
        candidates_by_id: dict[str, dict] = {}
        for rank, row in enumerate(rows):
            keyword = _keyword_score(query_text, row["text"])
            distance = float(row["distance"])
            if (
                distance <= settings.rag_max_vector_distance
                or keyword >= settings.rag_keyword_min_score
            ):
                metadata = _metadata_dict(row["chunk_metadata"])
                metadata["chunk_index"] = row["chunk_index"]
                candidates_by_id[row["id"]] = _result(
                        chunk_id=row["id"],
                        document=row["text"],
                        metadata=metadata,
                        distance=distance,
                        keyword_score=keyword,
                        vector_rank=rank,
                    )
        for rank, row in enumerate(keyword_rows, start=len(rows)):
            keyword = _keyword_score(query_text, row["text"])
            if keyword < settings.rag_keyword_min_score:
                continue
            existing = candidates_by_id.get(row["id"])
            if existing is not None:
                existing["keyword_score"] = max(existing["keyword_score"], keyword)
                continue
            metadata = _metadata_dict(row["chunk_metadata"])
            metadata["chunk_index"] = row["chunk_index"]
            candidates_by_id[row["id"]] = _result(
                chunk_id=row["id"],
                document=row["text"],
                metadata=metadata,
                distance=float(row["distance"]),
                keyword_score=keyword,
                vector_rank=rank,
            )
        return list(candidates_by_id.values())

    @staticmethod
    def _rank(
        candidates: list[dict],
        top_k: int,
        query_text: str = "",
        *,
        policy_query: bool | None = None,
    ) -> list[dict]:
        # Naming a tour code is an explicit request for that programme. Without
        # this, chunks of sibling tours whose wording happens to sit closer push
        # the named programme's own itinerary out of the window, and the answer
        # is drawn from the wrong tour or missed entirely.
        normalized_query = _normalize(query_text)
        wants_policy = (
            is_policy_query(query_text) if policy_query is None else policy_query
        )

        # Contract terms are high impact and programme-specific. They are
        # stored as a separate chunk type and must not leak into ordinary
        # itinerary answers (or be mixed with itinerary text for policy asks).
        candidates = [
            item for item in candidates
            if ((item.get("metadata") or {}).get("section") == "Policy") == wants_policy
        ]

        def names_this_programme(item: dict) -> bool:
            code = _document_code(item.get("metadata") or {})
            return bool(code) and code in normalized_query

        ordered = sorted(
            candidates,
            key=lambda item: (
                0 if names_this_programme(item) else 1,
                item["distance"]
                - KEYWORD_NUDGE * min(item["keyword_score"], KEYWORD_NUDGE_CAP),
                item["vector_rank"],
            ),
        )
        if wants_policy and ordered:
            first_metadata = ordered[0].get("metadata") or {}
            programme_id = (
                first_metadata.get("document_id")
                or first_metadata.get("filename")
                or first_metadata.get("source")
            )
            if programme_id:
                ordered = [
                    item for item in ordered
                    if (
                        (item.get("metadata") or {}).get("document_id")
                        or (item.get("metadata") or {}).get("filename")
                        or (item.get("metadata") or {}).get("source")
                    ) == programme_id
                ]
        unique: list[dict] = []
        seen: set[str] = set()
        for chunk in ordered:
            # Collapse only identical text. Similar or differently formatted
            # excerpts may carry distinct source evidence and must remain.
            fingerprint = chunk["text"]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(chunk)
            if len(unique) >= top_k:
                break
        return unique


travel_vector_store = TravelVectorStore()
