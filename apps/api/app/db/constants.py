"""Database-wide constants. Kept in their own module so models and migrations share them."""

# Size of the embedding vectors stored in code_chunks.embedding.
# This is the ONE place the number lives. The Gemini embedding model supports reduced output
# dimensions, so we ask it for 768 (CLAUDE.md decision 7). Changing it needs a migration that
# alters the column and rebuilds the HNSW index, plus a full re-index of every repository.
# Settings.embedding_dim is checked against this at startup (see app.config).
EMBEDDING_DIM = 768
