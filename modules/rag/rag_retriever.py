"""
High-level retrieval for the job bot.

- index_profile(config): chunk the applicant's background and embed it once.
- retrieve(query, k):     return the most relevant background chunks.
- build_context(...):     compose a compact "Relevant background" block to inject
                          into a form-answer prompt, scoped to the question + job.

The point: the AI form filler stops pasting the whole bio into every prompt and
instead grounds each answer in only the background that matters for that field.
"""

from __future__ import annotations

import re

from modules.rag.rag_embed import get_embedder
from modules.rag.rag_store import VectorStore

PROFILE_SOURCE = "profile"

# Split on sentence boundaries and blank lines; a chunk is one or two sentences.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def chunk_text(text: str, max_sentences: int = 2) -> list[str]:
    """Break background prose into short, embeddable chunks."""
    if not text:
        return []
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    chunks, buffer = [], []
    for sent in sentences:
        buffer.append(sent)
        if len(buffer) >= max_sentences:
            chunks.append(" ".join(buffer))
            buffer = []
    if buffer:
        chunks.append(" ".join(buffer))
    return chunks


def _profile_texts(config: dict) -> list[str]:
    """Collect the raw background strings from config: bio plus any explicit
    experience/highlight lists the applicant provides."""
    applicant = config.get("applicant", {}) or {}
    texts: list[str] = []

    bio = applicant.get("bio", "")
    if bio:
        texts.extend(chunk_text(bio))

    # Optional richer background: a list of experience/highlight strings.
    for key in ("experience", "highlights", "projects"):
        items = applicant.get(key)
        if isinstance(items, list):
            texts.extend(str(i).strip() for i in items if str(i).strip())
        elif isinstance(items, str) and items.strip():
            texts.extend(chunk_text(items))

    # De-dupe while preserving order.
    seen, unique = set(), []
    for t in texts:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def index_profile(config: dict, store: VectorStore | None = None) -> int:
    """(Re)build the profile index from config. Returns the chunk count."""
    store = store or VectorStore()
    embedder = get_embedder()
    chunks = _profile_texts(config)

    store.clear(PROFILE_SOURCE)
    if not chunks:
        return 0
    store.add_many(PROFILE_SOURCE, [(c, embedder.embed(c)) for c in chunks])
    return len(chunks)


def retrieve(query: str, k: int = 3, store: VectorStore | None = None) -> list[str]:
    """Return up to k background chunks most relevant to `query`."""
    store = store or VectorStore()
    if store.count(PROFILE_SOURCE) == 0:
        return []
    embedder = get_embedder()
    hits = store.search(embedder.embed(query), k=k, source=PROFILE_SOURCE)
    return [h["chunk"] for h in hits if h["score"] > 0]


def build_context(question_label: str, job: dict, k: int = 3,
                  store: VectorStore | None = None) -> str | None:
    """
    Build the grounding block for one form question. The query blends the
    question with the job's title/company/description so retrieval favors the
    background that fits both the field and the role.

    Returns a formatted string, or None if nothing is indexed (caller then
    falls back to the full bio).
    """
    job = job or {}
    query = " ".join(str(x) for x in (
        question_label,
        job.get("title", ""),
        job.get("company", ""),
        (job.get("description", "") or "")[:500],
    ) if x)

    chunks = retrieve(query, k=k, store=store)
    if not chunks:
        return None
    bullets = "\n".join(f"  - {c}" for c in chunks)
    return f"Relevant background (retrieved for this question):\n{bullets}"
