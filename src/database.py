from __future__ import annotations

import json
import pickle
import shutil
import sys
from pathlib import Path

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter


if getattr(sys, "frozen", False):
    PROJECT_FOLDER = Path(sys.executable).resolve().parent
else:
    SRC_FOLDER = Path(__file__).resolve().parent
    PROJECT_FOLDER = SRC_FOLDER.parent

DATA_FOLDER = PROJECT_FOLDER / "data"
DOCUMENTS_FILE = DATA_FOLDER / "documents.pkl"
FAISS_FOLDER = DATA_FOLDER / "faiss"
VECTOR_CACHE_FOLDER = DATA_FOLDER / "vector_cache"
INGEST_MANIFEST = DATA_FOLDER / "document_cache" / "manifest.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 32
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
CACHE_VERSION = 2


def _safe_cache_name(book: str) -> str:
    safe = "".join(
        character if character.isalnum() else "_"
        for character in book
    )
    return f"{safe}.vectors.pkl"


def _load_manifest() -> dict:
    if not INGEST_MANIFEST.exists():
        return {}

    try:
        data = json.loads(INGEST_MANIFEST.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_vector_cache(
    cache_path: Path,
    expected_signature,
):
    if not cache_path.exists():
        return None

    try:
        with cache_path.open("rb") as file:
            cached = pickle.load(file)

        if (
            cached.get("cache_version") == CACHE_VERSION
            and cached.get("signature") == expected_signature
            and cached.get("chunk_size") == CHUNK_SIZE
            and cached.get("chunk_overlap") == CHUNK_OVERLAP
        ):
            return cached
    except Exception:
        return None

    return None


def build_database(progress_callback=None):
    DATA_FOLDER.mkdir(parents=True, exist_ok=True)
    VECTOR_CACHE_FOLDER.mkdir(parents=True, exist_ok=True)

    if not DOCUMENTS_FILE.exists():
        raise RuntimeError(
            "The library has not been scanned yet. "
            "Add a PDF, DOCX, or TXT file and press Rebuild."
        )

    with DOCUMENTS_FILE.open("rb") as file:
        documents = pickle.load(file)

    if not documents:
        raise RuntimeError("No readable documents were found to index.")

    manifest = _load_manifest()
    documents_by_book = {}

    for document in documents:
        book = str(document.metadata.get("book", "Unknown"))
        documents_by_book.setdefault(book, []).append(document)

    if progress_callback:
        progress_callback(
            5,
            f"Checking {len(documents_by_book)} library files...",
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    all_texts = []
    all_metadatas = []
    all_vectors = []
    valid_cache_names = set()
    total_books = max(len(documents_by_book), 1)

    for book_index, (book, book_documents) in enumerate(
        sorted(documents_by_book.items()),
        start=1,
    ):
        cache_name = _safe_cache_name(book)
        cache_path = VECTOR_CACHE_FOLDER / cache_name
        valid_cache_names.add(cache_name)

        signature = manifest.get(book, {}).get("signature")
        cached = _load_vector_cache(cache_path, signature)

        if cached is not None:
            texts = cached["texts"]
            metadatas = cached["metadatas"]
            vectors = cached["vectors"]

            if progress_callback:
                progress_callback(
                    10 + int(book_index / total_books * 75),
                    f"Reusing embeddings: {book}",
                )
        else:
            chunks = splitter.split_documents(book_documents)

            if not chunks:
                continue

            texts = [chunk.page_content for chunk in chunks]
            metadatas = [dict(chunk.metadata) for chunk in chunks]
            vectors = []

            total_chunks = len(texts)

            for start in range(0, total_chunks, BATCH_SIZE):
                end = min(start + BATCH_SIZE, total_chunks)
                vectors.extend(
                    embeddings.embed_documents(texts[start:end])
                )

                if progress_callback:
                    within_file = end / max(total_chunks, 1)
                    completed_files = book_index - 1
                    overall = (
                        completed_files + within_file
                    ) / total_books
                    progress_callback(
                        10 + int(overall * 75),
                        f"Embedding {book}: {end}/{total_chunks}",
                    )

            cache_payload = {
                "cache_version": CACHE_VERSION,
                "signature": signature,
                "chunk_size": CHUNK_SIZE,
                "chunk_overlap": CHUNK_OVERLAP,
                "texts": texts,
                "metadatas": metadatas,
                "vectors": vectors,
            }

            with cache_path.open("wb") as file:
                pickle.dump(cache_payload, file)

        all_texts.extend(texts)
        all_metadatas.extend(metadatas)
        all_vectors.extend(vectors)

    for cache_path in VECTOR_CACHE_FOLDER.glob("*.vectors.pkl"):
        if cache_path.name not in valid_cache_names:
            try:
                cache_path.unlink()
            except OSError:
                pass

    if not all_texts or not all_vectors:
        raise RuntimeError("No searchable text chunks were created.")

    if progress_callback:
        progress_callback(88, "Assembling the searchable index...")

    database = FAISS.from_embeddings(
        text_embeddings=list(zip(all_texts, all_vectors)),
        embedding=embeddings,
        metadatas=all_metadatas,
    )

    temp_folder = DATA_FOLDER / "faiss_new"
    old_folder = DATA_FOLDER / "faiss_old"

    shutil.rmtree(temp_folder, ignore_errors=True)
    shutil.rmtree(old_folder, ignore_errors=True)
    temp_folder.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(94, "Saving the new index...")

    database.save_local(str(temp_folder))

    if FAISS_FOLDER.exists():
        FAISS_FOLDER.rename(old_folder)

    temp_folder.rename(FAISS_FOLDER)
    shutil.rmtree(old_folder, ignore_errors=True)

    if progress_callback:
        progress_callback(
            100,
            f"Index ready: {len(all_texts)} chunks",
        )

    return database


if __name__ == "__main__":
    build_database(
        progress_callback=lambda percent, message:
        print(f"{percent}% - {message}")
    )