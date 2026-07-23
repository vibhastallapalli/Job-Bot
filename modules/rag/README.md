# RAG layer — retrieval-grounded form answers

The AI form filler used to paste the applicant's **entire bio** into every
Claude prompt. This layer replaces that with retrieval: the background is
embedded once and stored in SQLite, and each form question pulls back only the
chunks relevant to that question and job. Tighter, less noisy prompts mean fewer
hallucinations and lower token cost — the same reason "a lot of AI work is data
wrangling / RAG / fast vector retrieval."

## How it fits in

```
config applicant.bio / applicant.experience
        │  index_profile()  (once, at app startup)
        ▼
   rag_chunks table  ── embeddings in SQLite (database/database_models.py)
        │  build_context(question, job)  (per form field)
        ▼
   ai_form_filler.answer_question()  →  Claude prompt grounded in retrieved chunks
```

If nothing is indexed or retrieval errors, `answer_question` falls back to the
full bio, so behavior degrades safely.

## Files

| File | Role |
|---|---|
| `rag_embed.py` | Embedders: `HashingEmbedder` (pure-Python, offline, default) and optional `SentenceTransformerEmbedder`; `cosine()`. |
| `rag_store.py` | `VectorStore` over the `rag_chunks` SQLite table — add / clear / count / cosine search. |
| `rag_retriever.py` | `chunk_text`, `index_profile`, `retrieve`, `build_context`. |
| `rag_test.py` | Standard-library tests (temp DB, hashing embedder). |

## Embedding backends

- **Default — `HashingEmbedder`:** signed feature hashing, no dependencies, no
  downloads, deterministic. Similarity is lexical (shared terms). Great offline
  and for tests.
- **Optional — semantic:** install `sentence-transformers` and set
  `JOB_BOT_EMBEDDER=sentence-transformers` for paraphrase-aware retrieval. Falls
  back to hashing automatically if the package or model isn't available.

## Run the tests

```bash
python modules/rag/rag_test.py
```

## Use it

Populate `applicant.bio` and (optionally) `applicant.experience` in `config.json`
— each `experience` item becomes one retrievable chunk. The index rebuilds on
app startup; no manual step. To reindex from code:

```python
from modules.rag import index_profile, retrieve
index_profile(config)
retrieve("options pricing experience", k=3)
```
