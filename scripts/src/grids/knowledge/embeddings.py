"""Local embedding function for ChromaDB using sentence-transformers."""

from chromadb import EmbeddingFunction, Documents, Embeddings

MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


class LocalEmbeddingFunction(EmbeddingFunction):
    """Wraps sentence-transformers for ChromaDB."""

    def __init__(self):
        pass

    def __call__(self, input: Documents) -> Embeddings:
        model = _get_model()
        embeddings = model.encode(input, show_progress_bar=False)
        return embeddings.tolist()
