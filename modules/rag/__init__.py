"""
Retrieval-augmented generation (RAG) layer for the job bot.

Instead of dumping the applicant's entire bio into every Claude prompt, this
package embeds the background once, stores the vectors in SQLite, and retrieves
only the chunks relevant to the specific form question being answered. That
keeps prompts focused (less noise -> fewer hallucinations, lower token cost).

Public surface:
    from modules.rag import build_context, index_profile
"""

from modules.rag.rag_retriever import build_context, index_profile, retrieve

__all__ = ["build_context", "index_profile", "retrieve"]
