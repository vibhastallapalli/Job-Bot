"""
Tests for the RAG layer. Runs with the standard library only (hashing embedder,
a temp SQLite DB) — no model downloads, no network.

Run:  python modules/rag/rag_test.py
"""

import os
import sys
import tempfile

# Make the repo root importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.rag.rag_embed import HashingEmbedder, cosine, get_embedder
from modules.rag.rag_retriever import build_context, chunk_text, index_profile, retrieve
from modules.rag.rag_store import VectorStore

CONFIG = {
    "applicant": {
        "name": "Test Applicant",
        "bio": "Nanotechnology engineering student who likes building things.",
        "experience": [
            "Built a Black-Scholes options pricing engine in C++ computing Greeks like Delta and Gamma.",
            "Automated job applications end to end with Playwright browser automation and a Flask dashboard.",
            "Predicted football yellow cards with a Negative Binomial model and Bayesian shrinkage in Python.",
        ],
    }
}


def test_embedder_is_deterministic_and_normalized():
    emb = HashingEmbedder(dim=128)
    a = emb.embed("options pricing in c++")
    b = emb.embed("options pricing in c++")
    assert a == b, "embedding must be deterministic"
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-9, f"expected unit norm, got {norm}"


def test_cosine_orders_similarity():
    emb = HashingEmbedder(dim=256)
    q = emb.embed("black scholes options pricing greeks")
    close = emb.embed("options pricing engine with greeks delta gamma")
    far = emb.embed("playwright browser automation flask dashboard")
    assert cosine(q, close) > cosine(q, far), "related text must score higher"


def test_index_and_retrieve_ranks_relevant_chunk_first():
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore(db_path=os.path.join(tmp, "t.db"))
        n = index_profile(CONFIG, store=store)
        assert n == 4, f"expected 4 chunks (1 bio + 3 experience), got {n}"

        hits = retrieve("options pricing greeks delta", k=1, store=store)
        assert hits, "expected at least one hit"
        assert "Black-Scholes" in hits[0], f"wrong top chunk: {hits[0]}"

        hits2 = retrieve("browser automation with playwright", k=1, store=store)
        assert "Playwright" in hits2[0], f"wrong top chunk: {hits2[0]}"


def test_reindex_does_not_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore(db_path=os.path.join(tmp, "t.db"))
        index_profile(CONFIG, store=store)
        index_profile(CONFIG, store=store)  # run twice
        assert store.count("profile") == 4, "re-index must replace, not append"


def test_build_context_none_before_index_then_block_after():
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore(db_path=os.path.join(tmp, "t.db"))
        job = {"title": "Quant Developer", "company": "Acme",
               "description": "Work on options pricing and risk models."}
        assert build_context("Why this role?", job, store=store) is None

        index_profile(CONFIG, store=store)
        ctx = build_context("Describe your options pricing experience", job, k=2, store=store)
        assert ctx is not None and "Relevant background" in ctx
        assert "Black-Scholes" in ctx


def test_chunk_text_splits_prose():
    chunks = chunk_text("First sentence here. Second one now. Third arrives too.", max_sentences=2)
    assert len(chunks) == 2, f"expected 2 chunks, got {chunks}"


def test_get_embedder_defaults_to_hashing_offline():
    os.environ.pop("JOB_BOT_EMBEDDER", None)
    assert get_embedder().name == "hashing"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   - {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL - {t.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR- {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
