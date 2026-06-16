import math
import re
from typing import List, Dict, Optional, Tuple, Any

# Try to import sentence_transformers and numpy as optional dependencies
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    np = None


class BM25:
    """
    A lightweight, self-contained Python implementation of BM25 ranking.
    """
    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.doc_lengths = [len(doc) for doc in corpus]
        self.avg_doc_len = sum(self.doc_lengths) / (self.corpus_size + 1e-9)
        self.doc_freqs = {}
        self.idf = {}
        self.doc_term_freqs = []
        
        # Initialize frequencies
        for doc in corpus:
            tf = {}
            for term in doc:
                tf[term] = tf.get(term, 0) + 1
            self.doc_term_freqs.append(tf)
            for term in tf:
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1
                
        # Calculate IDF
        for term, freq in self.doc_freqs.items():
            # Standard BM25 IDF formula with smoothing to avoid negative values
            self.idf[term] = math.log((self.corpus_size - freq + 0.5) / (freq + 0.5) + 1.0 + 1e-9)

    def get_score(self, doc_index: int, query_terms: List[str]) -> float:
        score = 0.0
        tf = self.doc_term_freqs[doc_index]
        doc_len = self.doc_lengths[doc_index]
        
        for term in query_terms:
            if term not in tf:
                continue
            f = tf[term]
            idf_val = self.idf.get(term, 0.0)
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))
            score += idf_val * (numerator / denominator)
        return score


def tokenize(text: str) -> List[str]:
    """
    Tokenizes text into a list of lowercase alphanumeric words.
    """
    return re.findall(r'\w+', text.lower())


def get_embeddings(texts: List[str], model_name: str = "all-MiniLM-L6-v2") -> Any:
    """
    Generates sentence embeddings using the sentence-transformers library.
    """
    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        raise ImportError(
            "The 'sentence-transformers' package is required for semantic search. "
            "Please install it using: pip install sentence-transformers"
        )
    model = SentenceTransformer(model_name)
    return model.encode(texts, convert_to_numpy=True, show_progress_bar=False)


def cosine_similarity(v1: Any, v2: Any) -> float:
    """
    Computes cosine similarity between two 1D numpy arrays.
    """
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return float(dot_product / (norm_v1 * norm_v2))


def get_semantic_scores(query: str, documents: List[str], model_name: str = "all-MiniLM-L6-v2") -> List[float]:
    """
    Computes semantic similarity (cosine similarity) of a query against a list of documents.
    """
    if not documents:
        return []
    embeddings = get_embeddings([query] + documents, model_name=model_name)
    query_emb = embeddings[0]
    doc_embs = embeddings[1:]
    
    scores = []
    for doc_emb in doc_embs:
        sim = cosine_similarity(query_emb, doc_emb)
        scores.append(sim)
    return scores


def hybrid_search(
    db,
    query: str,
    project_filter: Optional[str] = None,
    since_ts: Optional[int] = None,
    until_ts: Optional[int] = None,
    tag_filters: Optional[List[str]] = None,
    alpha: float = 0.5,
    model_name: str = "all-MiniLM-L6-v2"
) -> List[Dict]:
    """
    Performs a hybrid search (BM25 + Cosine Similarity) over terminal sessions.
    If sentence-transformers is not installed, raises an ImportError.
    """
    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        raise ImportError(
            "The 'sentence-transformers' package is required for semantic search. "
            "Please install it using: pip install sentence-transformers"
        )
        
    # Fetch all candidate sessions matching metadata filters (but without the query text filter)
    from termstory.search import advanced_search
    candidate_sessions = advanced_search(
        db,
        query=None,
        project_filter=project_filter,
        since_ts=since_ts,
        until_ts=until_ts,
        tag_filters=tag_filters
    )
    
    if not candidate_sessions or not query:
        return candidate_sessions

    # Construct document representation for each session
    documents = []
    for s in candidate_sessions:
        cmd_str = " ".join(s.get("all_commands", []))
        commit_str = " ".join([c.get("message", "") for c in s.get("all_commits", [])])
        doc_text = (
            f"Project: {s.get('project_name', '')}\n"
            f"Summary: {s.get('ai_summary') or ''}\n"
            f"Commands: {cmd_str}\n"
            f"Commits: {commit_str}"
        )
        documents.append(doc_text)
        
    # 1. Compute BM25 scores
    tokenized_corpus = [tokenize(doc) for doc in documents]
    query_terms = tokenize(query)
    bm25 = BM25(tokenized_corpus)
    bm25_scores = [bm25.get_score(i, query_terms) for i in range(len(candidate_sessions))]
    
    # 2. Compute Cosine Similarity scores
    semantic_scores = get_semantic_scores(query, documents, model_name=model_name)
    
    # 3. Min-Max normalization for BM25 scores
    min_bm25 = min(bm25_scores) if bm25_scores else 0.0
    max_bm25 = max(bm25_scores) if bm25_scores else 0.0
    bm25_range = max_bm25 - min_bm25
    if bm25_range == 0.0:
        bm25_range = 1e-9
    normalized_bm25 = [(score - min_bm25) / bm25_range for score in bm25_scores]
    
    # 4. Min-Max normalization for semantic scores
    min_sem = min(semantic_scores) if semantic_scores else 0.0
    max_sem = max(semantic_scores) if semantic_scores else 0.0
    sem_range = max_sem - min_sem
    if sem_range == 0.0:
        sem_range = 1e-9
    normalized_sem = [(score - min_sem) / sem_range for score in semantic_scores]
    
    # Combine scores
    scored_sessions = []
    for i, s in enumerate(candidate_sessions):
        h_score = alpha * normalized_sem[i] + (1.0 - alpha) * normalized_bm25[i]
        
        # Populate matching_commands and matching_commits based on overlapping terms
        matching_cmds = []
        for cmd in s.get("all_commands", []):
            if any(term in cmd.lower() for term in query_terms):
                matching_cmds.append(cmd)
                
        matching_commits = []
        for commit in s.get("all_commits", []):
            msg = commit.get("message", "").lower()
            if any(term in msg for term in query_terms):
                matching_commits.append(commit)
                
        s["matching_commands"] = matching_cmds
        s["matching_commits"] = matching_commits
        s["hybrid_score"] = h_score
        scored_sessions.append(s)
        
    # Sort sessions by hybrid score descending
    scored_sessions.sort(key=lambda x: x["hybrid_score"], reverse=True)
    
    return scored_sessions
