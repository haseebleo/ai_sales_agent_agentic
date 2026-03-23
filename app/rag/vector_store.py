"""
Vector Store Abstraction Layer
Supports Chroma (default, zero-infra) and Qdrant (production-grade).
New backends implement VectorStoreBase and register in _BACKENDS.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import VectorStoreProvider, settings

logger = logging.getLogger("trango_agent.rag.vector_store")


class RetrievedChunk:
    __slots__ = ("text", "metadata", "score", "source_label")

    def __init__(self, text: str, metadata: dict[str, Any], score: float):
        self.text = text
        self.metadata = metadata
        self.score = score
        self.source_label = (
            f"{metadata.get('sheet_name', '?')} "
            f"[{metadata.get('category', '?')}]"
        )

    def __repr__(self) -> str:
        return f"<Chunk score={self.score:.3f} src={self.source_label}>"


class VectorStoreBase(ABC):
    @abstractmethod
    def upsert(self, chunks: list[dict[str, Any]]) -> int:
        """Embed and store/replace all chunks. Returns count inserted."""

    @abstractmethod
    def query(self, query_text: str, top_k: int, score_threshold: float) -> list[RetrievedChunk]:
        """Return top_k chunks above score_threshold, ordered by relevance."""

    @abstractmethod
    def collection_count(self) -> int:
        """Return number of stored documents."""

    @abstractmethod
    def reset(self) -> None:
        """Drop and recreate the collection (for full re-index)."""


# ── Ollama Embedding Function ──────────────────────────────────────────────────

class OllamaEmbeddingFunction:
    """ChromaDB-compatible embedding function using Ollama's local API."""

    def __init__(self, url: str, model: str) -> None:
        self._url = url
        self._model = model

    def name(self) -> str:
        return f"ollama-{self._model}"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import urllib.request, json
        embeddings = []
        for text in texts:
            payload = json.dumps({"model": self._model, "prompt": text}).encode()
            req = urllib.request.Request(
                self._url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            embeddings.append(data["embedding"])
        return embeddings

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self._embed(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self._embed(input)


# ── Chroma Backend ────────────────────────────────────────────────────────────

class ChromaVectorStore(VectorStoreBase):
    def __init__(self) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        self._ef = self._build_embedding_function()
        self._col = self._client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"ChromaDB ready — collection '{settings.CHROMA_COLLECTION}' "
                    f"({self._col.count()} docs)")

    def _build_embedding_function(self):
        """Use OpenAI embeddings when a real API key is set, otherwise use Ollama local embeddings."""
        api_key = settings.OPENAI_API_KEY
        if api_key and api_key != "sk-..." and not api_key.startswith("sk-..."):
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
            logger.info("Using OpenAI embedding function for ChromaDB")
            return OpenAIEmbeddingFunction(
                api_key=api_key,
                model_name=settings.OPENAI_EMBEDDING_MODEL,
            )
        else:
            logger.info("Using Ollama embedding function for ChromaDB (llama3.1:8b)")
            return OllamaEmbeddingFunction(
                url=str(settings.OLLAMA_BASE_URL).rstrip("/") + "/api/embeddings",
                model=settings.OLLAMA_MODEL,
            )

    def upsert(self, chunks: list[dict[str, Any]]) -> int:
        ids, docs, metas = [], [], []
        for i, chunk in enumerate(chunks):
            ids.append(f"chunk_{i:05d}")
            docs.append(chunk["text"])
            metas.append({k: str(v) for k, v in chunk["metadata"].items()})

        # Chroma batch limit is 5000; split if needed
        batch_size = 500
        for start in range(0, len(ids), batch_size):
            self._col.upsert(
                ids=ids[start : start + batch_size],
                documents=docs[start : start + batch_size],
                metadatas=metas[start : start + batch_size],
            )
        logger.info(f"Upserted {len(ids)} chunks into ChromaDB")
        return len(ids)

    def query(self, query_text: str, top_k: int, score_threshold: float) -> list[RetrievedChunk]:
        results = self._col.query(
            query_texts=[query_text],
            n_results=min(top_k, self._col.count() or 1),
            include=["documents", "metadatas", "distances"],
        )
        chunks: list[RetrievedChunk] = []
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        for doc, meta, dist in zip(docs, metas, dists):
            # Chroma cosine distance → similarity score
            score = 1.0 - dist
            if score >= score_threshold:
                chunks.append(RetrievedChunk(text=doc, metadata=meta, score=score))

        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks

    def collection_count(self) -> int:
        return self._col.count()

    def reset(self) -> None:
        self._client.delete_collection(settings.CHROMA_COLLECTION)
        self._col = self._client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB collection reset")


# ── Qdrant Backend ────────────────────────────────────────────────────────────

class QdrantVectorStore(VectorStoreBase):
    """
    Production-recommended backend for high-throughput deployments.
    Requires a running Qdrant instance (docker-compose.yml included).
    """
    _VECTOR_SIZE = 1536  # text-embedding-3-small

    def __init__(self) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
        )
        self._collection = settings.QDRANT_COLLECTION
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._VECTOR_SIZE, distance=Distance.COSINE
                ),
            )
        logger.info(f"Qdrant ready — collection '{self._collection}'")
        self._embedder = self._build_embedder()

    def _build_embedder(self):
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        def embed(texts: list[str]) -> list[list[float]]:
            resp = client.embeddings.create(input=texts, model=settings.OPENAI_EMBEDDING_MODEL)
            return [e.embedding for e in resp.data]

        return embed

    def upsert(self, chunks: list[dict[str, Any]]) -> int:
        from qdrant_client.models import PointStruct

        texts = [c["text"] for c in chunks]
        batch_size = 100
        total = 0
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start : start + batch_size]
            batch_chunks = chunks[start : start + batch_size]
            vectors = self._embedder(batch_texts)
            points = [
                PointStruct(
                    id=start + i,
                    vector=vec,
                    payload={**batch_chunks[i]["metadata"], "text": batch_chunks[i]["text"]},
                )
                for i, vec in enumerate(vectors)
            ]
            self._client.upsert(collection_name=self._collection, points=points)
            total += len(points)
        logger.info(f"Upserted {total} chunks into Qdrant")
        return total

    def query(self, query_text: str, top_k: int, score_threshold: float) -> list[RetrievedChunk]:
        vector = self._embedder([query_text])[0]
        results = self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )
        return [
            RetrievedChunk(
                text=r.payload.get("text", ""),
                metadata={k: v for k, v in r.payload.items() if k != "text"},
                score=r.score,
            )
            for r in results
        ]

    def collection_count(self) -> int:
        info = self._client.get_collection(self._collection)
        return info.points_count or 0

    def reset(self) -> None:
        from qdrant_client.models import Distance, VectorParams
        self._client.delete_collection(self._collection)
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=self._VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info("Qdrant collection reset")


# ── Factory ───────────────────────────────────────────────────────────────────

_store_instance: VectorStoreBase | None = None


def get_vector_store() -> VectorStoreBase:
    global _store_instance
    if _store_instance is None:
        if settings.VECTOR_STORE == VectorStoreProvider.QDRANT:
            _store_instance = QdrantVectorStore()
        else:
            _store_instance = ChromaVectorStore()
    return _store_instance


def reset_store() -> None:
    global _store_instance
    _store_instance = None
