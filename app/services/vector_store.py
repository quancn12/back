"""
ChromaDB vector store for hotel RAG.

A single collection stores all hotel chunks. Every query is filtered by hotel_id so
one hotel cannot retrieve another hotel's data. Retrieval combines vector search
with a small accent-insensitive keyword pass, which helps Vietnamese mobile users
who type short or unaccented questions.
"""
import asyncio
import re
import unicodedata
import uuid

import chromadb
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.services.llm_client import ollama_client


_STOPWORDS = {
    "anh", "ban", "biet", "cac", "cai", "can", "cho", "co", "cua", "duoc",
    "em", "gan", "gi", "hay", "hoi", "khach", "khong", "la", "san", "lam", "minh",
    "mot", "muon", "nao", "nay", "nhung", "o", "toi", "trong", "tu", "ve",
    "vui", "xin", "di", "den", "tai", "thong", "tin",
}


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _sanitize_fts_token(token: str) -> str:
    """Loại bỏ ký tự đặc biệt có thể làm hỏng câu truy vấn MATCH của SQLite FTS5."""
    clean = re.sub(r'[^\w\s]', '', token, flags=re.UNICODE).strip()
    return f'"{clean}"' if clean else ""


def _query_tokens(query_text: str) -> list[str]:
    normalized = _normalize_text(query_text)
    tokens = []
    for token in normalized.split():
        if len(token) < 3:
            continue
        if token in _STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _keyword_score(query_text: str, document_text: str) -> float:
    normalized_query = _normalize_text(query_text)
    normalized_doc = _normalize_text(document_text)
    tokens = _query_tokens(query_text)
    if not tokens:
        return 0.0

    score = 0.0
    for token in tokens:
        if re.search(rf"\b{re.escape(token)}\b", normalized_doc):
            score += 1.0

    meaningful_query = " ".join(tokens)
    if len(tokens) >= 2 and meaningful_query in normalized_doc:
        score += 2.0
    elif normalized_query and normalized_query in normalized_doc:
        score += 1.5

    return score


def _sync_sqlite_fts_search(hotel_id: str, tokens: list[str]) -> list[tuple[str, str, str]]:
    """Hàm đồng bộ truy vấn SQLite FTS5 để đẩy sang ThreadPool qua asyncio.to_thread."""
    clean_tokens = [_sanitize_fts_token(t) for t in tokens if _sanitize_fts_token(t)]
    if not clean_tokens:
        return []

    match_expr = " OR ".join(clean_tokens)
    query_sql = text(
        "SELECT id, text, document_id FROM hotel_document_chunks_fts "
        "WHERE hotel_id = :hotel_id AND hotel_document_chunks_fts MATCH :query"
    )
    with engine.connect() as conn:
        rows = conn.execute(query_sql, {"hotel_id": hotel_id, "query": match_expr}).fetchall()
        return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _sync_sqlite_fts_insert(ids: list[str], texts: list[str], hotel_id: str, document_id: str) -> None:
    """Hàm đồng bộ chèn FTS5 để đẩy sang ThreadPool."""
    with engine.begin() as conn:
        for cid, text_val in zip(ids, texts):
            conn.execute(
                text(
                    "INSERT INTO hotel_document_chunks_fts(id, hotel_id, document_id, text) "
                    "VALUES (:id, :hotel_id, :document_id, :text)"
                ),
                {"id": cid, "hotel_id": hotel_id, "document_id": document_id, "text": text_val}
            )


def _sync_sqlite_fts_delete(document_id: str) -> None:
    """Hàm đồng bộ xóa FTS5."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM hotel_document_chunks_fts WHERE document_id = :document_id"),
            {"document_id": document_id}
        )


class VectorStore:
    def __init__(self) -> None:
        self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self._collection = self._client.get_or_create_collection(
            name="hotel_documents",
            metadata={
                "hnsw:space": "cosine",
                "hnsw:search_ef": 32,
            },
        )

    async def add_chunks(
        self,
        hotel_id: str,
        document_id: str,
        chunks: list[str],
        filename: str | None = None,
    ) -> int:
        """Embed PDF chunks in small batches and store them in Chroma."""
        prepared: list[tuple[int, str]] = [
            (idx, chunk.strip()) for idx, chunk in enumerate(chunks) if chunk and chunk.strip()
        ]
        if not prepared:
            return 0

        batch_size = max(1, settings.ollama_embed_batch_size)
        added_count = 0

        for start in range(0, len(prepared), batch_size):
            batch = prepared[start : start + batch_size]
            texts = [chunk_text for _, chunk_text in batch]
            embeddings = await ollama_client.embed_batch(texts)

            ids: list[str] = []
            metadatas: list[dict] = []
            for idx, _chunk_text in batch:
                ids.append(f"{document_id}-{idx}-{uuid.uuid4().hex[:8]}")
                metadata = {
                    "hotel_id": hotel_id,
                    "document_id": document_id,
                    "chunk_index": idx,
                }
                if filename:
                    metadata["filename"] = filename
                    metadata["source"] = filename
                metadatas.append(metadata)

            # Chạy thêm ChromaDB vào Thread pool để không block main thread
            await asyncio.to_thread(
                self._collection.add,
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            # Đồng bộ sang bảng ảo SQLite FTS5 (nếu dùng SQLite)
            if settings.database_url.startswith("sqlite"):
                try:
                    await asyncio.to_thread(_sync_sqlite_fts_insert, ids, texts, hotel_id, document_id)
                except Exception as e:
                    print(f"Lỗi ghi SQLite FTS5: {e}")

            added_count += len(ids)

        return added_count

    async def query(
        self,
        hotel_id: str,
        query_text: str,
        top_k: int = 5,
        exclude_ids: set[str] | None = None,
    ) -> list[dict]:
        """Find relevant chunks with vector search plus accent-insensitive keyword search."""
        exclude_ids = exclude_ids or set()
        candidates: dict[str, dict] = {}
        fetch_n = max(top_k * 5, top_k + 15)

        # Nếu Ollama embedding tạm thời không sẵn sàng, keyword search vẫn có
        # thể phục vụ khách thay vì làm hỏng toàn bộ request.
        try:
            query_vector = await ollama_client.embed(query_text)
            vector_results = await asyncio.to_thread(
                self._collection.query,
                query_embeddings=[query_vector],
                n_results=fetch_n,
                where={"hotel_id": hotel_id},
            )

            ids = vector_results.get("ids", [[]])[0]
            docs = vector_results.get("documents", [[]])[0]
            metas = vector_results.get("metadatas", [[]])[0]
            distances = vector_results.get("distances", [[]])[0]

            for rank, (cid, doc, meta, dist) in enumerate(zip(ids, docs, metas, distances)):
                if cid in exclude_ids:
                    continue
                keyword_score = _keyword_score(query_text, doc)
                if (
                    dist <= settings.rag_max_vector_distance
                    or keyword_score >= settings.rag_keyword_min_score
                ):
                    candidates[cid] = self._make_chunk(
                        cid=cid,
                        doc=doc,
                        meta=meta,
                        distance=dist,
                        keyword_score=keyword_score,
                        vector_rank=rank,
                    )
        except Exception as exc:  # noqa: BLE001
            print(
                "[VectorStore] Ollama embedding không sẵn sàng; "
                f"chuyển sang SQLite FTS5: {exc}"
            )

        # Keyword Search sử dụng SQLite FTS5 qua ThreadPool
        keyword_ids = []
        keyword_docs = []
        keyword_metas = []

        if settings.database_url.startswith("sqlite"):
            try:
                tokens = _query_tokens(query_text)
                if not tokens:
                    norm = _normalize_text(query_text)
                    if norm:
                        tokens = [norm]

                if tokens:
                    fts_rows = await asyncio.to_thread(_sync_sqlite_fts_search, hotel_id, tokens)
                    for cid, text_val, doc_id in fts_rows:
                        keyword_ids.append(cid)
                        keyword_docs.append(text_val)
                        keyword_metas.append({
                            "hotel_id": hotel_id,
                            "document_id": doc_id,
                        })
            except Exception as e:
                print(f"Lỗi truy vấn SQLite FTS5: {e}")

        # Tính điểm keyword cho các chunk tìm được từ FTS5 và đưa vào candidates
        for cid, doc, meta in zip(keyword_ids, keyword_docs, keyword_metas):
            if cid in exclude_ids:
                continue
            keyword_score = _keyword_score(query_text, doc)
            if keyword_score < settings.rag_keyword_min_score:
                continue
            existing = candidates.get(cid)
            if existing:
                existing["keyword_score"] = max(existing["keyword_score"], keyword_score)
                continue
            candidates[cid] = self._make_chunk(
                cid=cid,
                doc=doc,
                meta=meta,
                distance=1.0,
                keyword_score=keyword_score,
                vector_rank=fetch_n + 100,
            )

        seen_texts: set[str] = set()
        chunks: list[dict] = []
        sorted_candidates = sorted(
            candidates.values(),
            key=lambda item: (
                -item["keyword_score"],
                item["distance"],
                item["vector_rank"],
            ),
        )

        for chunk in sorted_candidates:
            normalized = chunk["text"].strip()
            if normalized in seen_texts:
                continue
            seen_texts.add(normalized)
            chunks.append(chunk)
            if len(chunks) >= top_k:
                break

        return chunks

    def _make_chunk(
        self,
        cid: str,
        doc: str,
        meta: dict | None,
        distance: float,
        keyword_score: float,
        vector_rank: int,
    ) -> dict:
        meta = meta or {}
        source = meta.get("source") or meta.get("filename") or meta.get("document_id")
        return {
            "id": cid,
            "text": doc,
            "metadata": meta,
            "distance": distance,
            "keyword_score": keyword_score,
            "vector_rank": vector_rank,
            "source": source,
        }

    async def delete_document_chunks(self, document_id: str) -> None:
        """Delete all chunks belonging to one document asynchronously."""
        await asyncio.to_thread(self._collection.delete, where={"document_id": document_id})
        if settings.database_url.startswith("sqlite"):
            try:
                await asyncio.to_thread(_sync_sqlite_fts_delete, document_id)
            except Exception as e:
                print(f"Lỗi xóa SQLite FTS5: {e}")


vector_store = VectorStore()


