from __future__ import annotations

import json
import os
import pickle
import re
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TQDM_DISABLE", "1")

try:
    from tqdm import tqdm as _tqdm

    # Prevent tqdm from creating its persistent monitor thread.
    _tqdm.monitor_interval = 0
except Exception:
    pass

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


MODEL_NAME = "qwen2.5:3b"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
if getattr(sys, "frozen", False):
    PROJECT_FOLDER = Path(sys.executable).resolve().parent
else:
    SRC_FOLDER = Path(__file__).resolve().parent.parent
    PROJECT_FOLDER = SRC_FOLDER.parent

DATABASE_FOLDER = PROJECT_FOLDER / "data" / "faiss"
LIBRARY_FOLDER = PROJECT_FOLDER / "library"

_embeddings = None
_db_lock = threading.Lock()
db = None
conversation: list[dict[str, str]] = []


class GenerationStopped(Exception):
    pass


def _get_embeddings():
    global _embeddings

    with _db_lock:
        if _embeddings is None:
            _embeddings = HuggingFaceEmbeddings(
                model_name="all-MiniLM-L6-v2",
                show_progress=False,
            )

        return _embeddings


def reload_database() -> None:
    global db

    if not DATABASE_FOLDER.exists():
        with _db_lock:
            db = None
        return

    new_db = FAISS.load_local(
        str(DATABASE_FOLDER),
        _get_embeddings(),
        allow_dangerous_deserialization=True,
    )

    with _db_lock:
        db = new_db


def ensure_database_loaded() -> None:
    with _db_lock:
        loaded = db is not None

    if not loaded:
        reload_database()


def preload_database() -> None:
    # Startup must only report Ready after this finishes successfully.
    ensure_database_loaded()


def clear_conversation() -> None:
    conversation.clear()


def get_conversation() -> list[dict[str, str]]:
    return [dict(item) for item in conversation]


def set_conversation(items: list[dict[str, str]]) -> None:
    conversation.clear()

    for item in items[-10:]:
        user = str(item.get("user", "")).strip()
        ai = str(item.get("ai", "")).strip()

        if user and ai:
            conversation.append({"user": user, "ai": ai})



def get_library_stats() -> dict[str, int]:
    pdf_count = len(list(LIBRARY_FOLDER.glob("*.pdf")))
    docx_count = len(list(LIBRARY_FOLDER.glob("*.docx")))
    txt_count = len(list(LIBRARY_FOLDER.glob("*.txt")))
    page_count = 0
    chunk_count = 0

    documents_file = PROJECT_FOLDER / "data" / "documents.pkl"
    if documents_file.exists():
        try:
            with documents_file.open("rb") as file:
                page_count = len(pickle.load(file))
        except Exception:
            page_count = 0

    ensure_database_loaded()

    with _db_lock:
        active_db = db

    if active_db is not None:
        try:
            chunk_count = int(active_db.index.ntotal)
        except Exception:
            chunk_count = 0

    return {
        "pdfs": pdf_count,
        "docx": docx_count,
        "txt": txt_count,
        "pages": page_count,
        "chunks": chunk_count,
    }


def _progress(callback, percent: int, message: str) -> None:
    if callback:
        callback(percent, message)


def _ollama_chat(
    messages: list[dict[str, str]],
    progress_callback=None,
    stop_event: threading.Event | None = None,
) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": True,
        "keep_alive": "30m",
        "options": {
            "temperature": 0.1,
            "num_predict": 450,
        },
    }

    request = Request(
        OLLAMA_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    parts: list[str] = []
    token_updates = 0

    try:
        with urlopen(request, timeout=300) as response:
            for raw_line in response:
                if stop_event and stop_event.is_set():
                    response.close()
                    raise GenerationStopped()

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    parts.append(content)
                    token_updates += 1
                    _progress(
                        progress_callback,
                        min(92, 55 + token_updates // 3),
                        "Generating answer...",
                    )

                if data.get("done"):
                    break

    except GenerationStopped:
        raise
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama returned an error: {details}") from error
    except URLError as error:
        raise RuntimeError(
            "Could not connect to Ollama. Make sure Ollama is running."
        ) from error

    answer = "".join(parts).strip()

    if stop_event and stop_event.is_set():
        raise GenerationStopped()

    if not answer:
        raise RuntimeError("Ollama returned an empty answer.")

    return answer


def _small_talk_response(question: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9\s']", " ", question.lower())
    normalized = " ".join(normalized.split())

    if normalized in {
        "hi", "hello", "hey", "yo", "sup",
        "good morning", "good afternoon", "good evening",
    }:
        return "Hello! Ask me anything about the documents in your library."

    if normalized in {"thanks", "thank you", "thx", "appreciate it"}:
        return "You're welcome."

    if normalized in {"who are you", "what are you"}:
        return (
            "I'm Calypso, your private, source-grounded offline AI. "
            "I answer using the documents you choose."
        )

    return None


def _content_words(text: str) -> set[str]:
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "could",
        "do", "does", "for", "from", "had", "has", "have", "how", "i",
        "in", "is", "it", "may", "of", "on", "or", "should", "that",
        "the", "their", "there", "these", "this", "to", "was", "were",
        "what", "when", "where", "which", "who", "why", "will", "with",
        "would", "you", "your",
    }
    return {
        word
        for word in re.findall(r"[a-z0-9]{3,}", text.lower())
        if word not in stop_words
    }


def _retrieve_documents(
    question: str,
    selected_book: str | None = None,
    selected_books: list[str] | None = None,
    limit: int = 6,
    progress_callback=None,
    stop_event: threading.Event | None = None,
):
    with _db_lock:
        active_db = db

    if active_db is None:
        raise RuntimeError(
            "The library database is not loaded. Add files and press Rebuild."
        )

    queries = [question]
    if conversation:
        queries.append(
            f"Previous topic: {conversation[-1]['user']}. "
            f"Follow-up: {question}"
        )

    candidates = []
    seen_chunks = set()

    for index, query in enumerate(queries, start=1):
        if stop_event and stop_event.is_set():
            raise GenerationStopped()

        _progress(
            progress_callback,
            10 + int((index - 1) / max(len(queries), 1) * 20),
            f"Searching library ({index}/{len(queries)})...",
        )

        allowed_books = set(selected_books or [])
        if selected_book:
            allowed_books.add(selected_book)

        search_count = 40 if allowed_books else 16
        results = active_db.similarity_search_with_score(
            query,
            k=search_count,
        )

        for document, score in results:
            book = str(document.metadata.get("book", ""))

            if allowed_books and book not in allowed_books:
                continue

            key = (
                document.page_content[:300],
                book,
                document.metadata.get("page"),
            )
            if key in seen_chunks:
                continue

            seen_chunks.add(key)
            candidates.append((float(score), document))

    candidates.sort(key=lambda item: item[0])
    _progress(progress_callback, 35, "Checking source reliability...")

    if not candidates:
        return []

    best_score = candidates[0][0]
    relative_cutoff = best_score + max(0.35, abs(best_score) * 0.25)

    filtered = [
        (score, document)
        for score, document in candidates
        if score <= relative_cutoff
    ][:limit]

    if len(filtered) < min(2, len(candidates)):
        filtered = candidates[:min(limit, max(2, len(candidates)))]

    return filtered


def _document_passages(book: str, limit: int = 14) -> list[str]:
    ensure_database_loaded()

    with _db_lock:
        active_db = db

    if active_db is None:
        return []

    passages = []
    try:
        values = active_db.docstore._dict.values()
    except Exception:
        values = []

    for document in values:
        if str(document.metadata.get("book", "")) != book:
            continue

        text = str(document.page_content).strip()
        if text:
            passages.append(text)

        if len(passages) >= limit:
            break

    return passages


def summarize_document(
    book: str,
    progress_callback=None,
) -> str:
    passages = _document_passages(book, limit=18)

    if not passages:
        raise RuntimeError(
            "No indexed text was found for this document. Rebuild first."
        )

    _progress(progress_callback, 30, f"Preparing summary for {book}...")

    context = "\n\n".join(passages)
    messages = [
        {
            "role": "system",
            "content": (
                "Summarize the supplied document passages. Cover the main "
                "ideas, important details, and practical takeaways. Do not "
                "invent information that is not in the passages."
            ),
        },
        {
            "role": "user",
            "content": f"Document: {book}\n\nPassages:\n{context}",
        },
    ]

    return _ollama_chat(
        messages,
        progress_callback=progress_callback,
    )


def suggest_questions(
    book: str,
    progress_callback=None,
) -> list[str]:
    passages = _document_passages(book, limit=10)

    if not passages:
        raise RuntimeError(
            "No indexed text was found for this document. Rebuild first."
        )

    _progress(progress_callback, 35, f"Reading {book}...")

    messages = [
        {
            "role": "system",
            "content": (
                "Create exactly five useful questions that can be answered "
                "from the supplied document passages. Return one question "
                "per line with no numbering and no extra text."
            ),
        },
        {
            "role": "user",
            "content": "\n\n".join(passages),
        },
    ]

    result = _ollama_chat(
        messages,
        progress_callback=progress_callback,
    )

    questions = []
    for line in result.splitlines():
        cleaned = re.sub(r"^\s*[-*0-9.)]+\s*", "", line).strip()
        if cleaned:
            questions.append(cleaned)

    return questions[:5]



def ask_ai(
    question: str,
    progress_callback=None,
    stop_event: threading.Event | None = None,
    selected_book: str | None = None,
    selected_books: list[str] | None = None,
    answer_mode: str = "Balanced",
    interaction_mode: str = "Research",
):
    global conversation

    question = question.strip()

    if not question:
        return "Please enter a question.", []

    small_talk = _small_talk_response(question)
    if small_talk is not None:
        conversation.append({"user": question, "ai": small_talk})
        conversation = conversation[-10:]
        return small_talk, []

    if interaction_mode == "Chat":
        mode_instructions = {
            "Quick": "Answer briefly and directly.",
            "Detailed": "Give a thorough, well-structured answer.",
            "Bullet points": "Prefer concise bullet points.",
            "Explain simply": "Use very simple, plain language.",
            "Balanced": "Give a clear answer with moderate detail.",
        }
        mode_instruction = mode_instructions.get(
            answer_mode,
            mode_instructions["Balanced"],
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Calypso, a helpful private offline AI. "
                    "In Chat mode, answer normally using your built-in "
                    "knowledge and reasoning. Do not search the user's "
                    "library and do not provide library references. "
                    f"{mode_instruction}"
                ),
            }
        ]

        for item in conversation[-4:]:
            messages.append({"role": "user", "content": item["user"]})
            messages.append({"role": "assistant", "content": item["ai"]})

        messages.append({"role": "user", "content": question})

        _progress(progress_callback, 15, "Thinking...")
        answer = _ollama_chat(
            messages,
            progress_callback=progress_callback,
            stop_event=stop_event,
        )

        conversation.append({"user": question, "ai": answer})
        conversation = conversation[-10:]
        return answer, []

    if stop_event and stop_event.is_set():
        raise GenerationStopped()

    _progress(progress_callback, 5, "Preparing search...")

    results = _retrieve_documents(
        question,
        selected_book=selected_book,
        selected_books=selected_books,
        limit=6,
        progress_callback=progress_callback,
        stop_event=stop_event,
    )

    if not results:
        answer = (
            "I couldn't find a useful matching passage in the selected "
            "document or library. Try rephrasing the question."
        )
        conversation.append({"user": question, "ai": answer})
        conversation = conversation[-10:]
        return answer, []

    context_parts: list[str] = []
    sources: list[dict[str, object]] = []
    seen_sources: set[tuple[str, object, str]] = set()

    for score, result in results:
        if stop_event and stop_event.is_set():
            raise GenerationStopped()

        book = str(result.metadata.get("book", "Unknown"))
        page = result.metadata.get("page", "?")
        file_type = str(
            result.metadata.get(
                "file_type",
                Path(book).suffix.lower().lstrip("."),
            )
        )
        source_path = LIBRARY_FOLDER / book
        preview = " ".join(result.page_content.split()).strip()

        context_parts.append(
            f"[Source: {book}, location {page}]\n{result.page_content}"
        )

        source_key = (book, page, preview)
        if source_key not in seen_sources:
            seen_sources.add(source_key)
            sources.append(
                {
                    "book": book,
                    "page": page,
                    "pdf": str(source_path),
                    "file_type": file_type,
                    "preview": preview,
                    "score": round(float(score), 4),
                }
            )

    _progress(progress_callback, 45, "Preparing source-grounded context...")
    context = "\n\n".join(context_parts)

    if selected_book:
        scope_text = (
            f"Focus on the selected document: {selected_book}."
        )
    elif selected_books:
        scope_text = (
            "Focus on these selected library documents: "
            + ", ".join(selected_books)
        )
    else:
        scope_text = "Use the supplied passages from the user's library."

    mode_instructions = {
        "Quick": "Give a brief direct answer in one or two short paragraphs.",
        "Detailed": (
            "Give a thorough answer with useful explanation and structure."
        ),
        "Bullet points": "Prefer concise bullet points.",
        "Explain simply": (
            "Use plain language and explain it as simply as possible."
        ),
        "Balanced": "Give a clear answer with moderate detail.",
    }
    mode_instruction = mode_instructions.get(
        answer_mode,
        mode_instructions["Balanced"],
    )

    system_message = f"""
You are Calypso, a private offline assistant that answers only from the
user's own documents.

{scope_text}

Instructions:
- Use the supplied passages as the main source for the answer.
- Prefer information that directly addresses the question.
- Do not invent quotations, page numbers, or document claims.
- When the passages are incomplete, explain the answer carefully without
  pretending the documents said something they did not.
- Keep the answer clear, focused, and readable.
- Response style: {mode_instruction}
""".strip()

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_message}
    ]

    for item in conversation[-3:]:
        messages.append({"role": "user", "content": item["user"]})
        messages.append({"role": "assistant", "content": item["ai"]})

    messages.append(
        {
            "role": "user",
            "content": (
                f"Library passages:\n\n{context}\n\n"
                f"Question:\n{question}"
            ),
        }
    )

    _progress(progress_callback, 52, "Starting Ollama...")

    answer = _ollama_chat(
        messages,
        progress_callback=progress_callback,
        stop_event=stop_event,
    ).strip()


    _progress(progress_callback, 96, "Formatting answer...")

    conversation.append({"user": question, "ai": answer})
    conversation = conversation[-10:]

    return answer, sources