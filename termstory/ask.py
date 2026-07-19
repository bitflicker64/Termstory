import math
import re
import sqlite3
from collections import Counter
from typing import List, Optional, Dict, Tuple

from termstory.models import Session
from termstory.config import get_db_path
from termstory.ai import _send_llm_request, _DEFAULT_MAX_TOKENS
from termstory.sanitizer import sanitize_session_commands, redact_command

# BM25 tuning constants
_BM25_K1 = 1.5   # term-frequency saturation
_BM25_B  = 0.75  # length normalisation


def _get_project_names_map() -> Dict[int, str]:
    """Helper to extract a mapping of project IDs to names from database."""
    db_path = get_db_path()
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM projects;")
        res = {row[0]: row[1] for row in cursor.fetchall()}
        return res
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()


def _tokenize(text: str) -> List[str]:
    """Tokenise text into lowercase word tokens."""
    return re.findall(r'\w+', text.lower())


def _make_bigrams(tokens: List[str]) -> List[str]:
    """Return all adjacent bigrams as '_'-joined strings."""
    if len(tokens) < 2:
        return []
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def _session_tokens(s: Session, project_map: Dict[int, str]) -> List[str]:
    """Build a flat token list (unigrams + bigrams) for a session document."""
    parts = []
    p_name = project_map.get(s.project_id, "Other") if s.project_id is not None else "Other"
    # Weight project name and AI summary more heavily by repeating them
    parts.extend([p_name] * 3)
    if s.ai_summary:
        parts.extend([s.ai_summary] * 2)
    for cmd in s.commands:
        parts.append(cmd.command)
    for commit in s.commits:
        parts.append(commit.get("cleaned_message", "") or commit.get("message", ""))

    raw_text = " ".join(filter(None, parts))
    unigrams = _tokenize(raw_text)
    bigrams  = _make_bigrams(unigrams)
    return unigrams + bigrams


def _query_terms(words: List[str]) -> List[str]:
    """Expand query words into unigrams + bigrams for scoring."""
    bigrams = _make_bigrams(words)
    return words + bigrams


def _bm25_score(
    query_terms: List[str],
    doc_counter: Counter,
    doc_len: int,
    avg_doc_len: float,
    df: Dict[str, int],
    N: int,
) -> float:
    """
    Compute BM25 score for a single document.

    Unigrams support prefix matching (e.g., query term 'dep' matches 'deploy').
    Bigrams must match exactly (since they encode adjacency).
    """
    score = 0.0
    if doc_len == 0 or avg_doc_len == 0:
        return score

    len_norm = 1.0 - _BM25_B + _BM25_B * (doc_len / avg_doc_len)

    for q_t in query_terms:
        is_bigram = "_" in q_t

        # Count raw term frequency in this document
        if is_bigram:
            # Exact match only for bigrams
            tf_raw = doc_counter.get(q_t, 0)
        else:
            # Prefix match for unigrams
            tf_raw = sum(v for k, v in doc_counter.items()
                         if "_" not in k and k.startswith(q_t))

        if tf_raw == 0:
            continue

        # IDF (smoothed, additive)
        doc_freq = df.get(q_t, 0)
        idf = math.log((N - doc_freq + 0.5) / (doc_freq + 0.5) + 1)

        # BM25 TF component
        tf_bm25 = (tf_raw * (_BM25_K1 + 1)) / (tf_raw + _BM25_K1 * len_norm)

        score += idf * tf_bm25

    return score


def search_ask(query: str, db) -> List[Session]:
    """
    Search shell history sessions using SQLite FTS5 matching as candidate retrieval,
    then rank them using BM25 with unigram + bigram support.

    Falls back to LIKE-based candidate retrieval if FTS5 is unavailable.
    """
    if not query.strip():
        return []

    words = _tokenize(query)
    if not words:
        return []

    # 1. Candidate Retrieval ──────────────────────────────────────────────────
    conn = db.get_connection()
    session_ids: List[int] = []
    try:
        cursor = conn.cursor()

        # Build FTS5 prefix query: each word becomes "word"*
        sanitized_terms = []
        for w in words:
            escaped_w = w.replace('"', '""')
            sanitized_terms.append(f'"{escaped_w}"*')
        fts_query = " OR ".join(sanitized_terms)

        sql = """
            WITH fts_matches AS (
                SELECT type, ref_id, project_id, timestamp
                FROM search_index
                WHERE search_index MATCH ?
            )
            SELECT DISTINCT s.id
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            LEFT JOIN fts_matches f ON (
                (f.type = 'session_summary' AND CAST(f.ref_id AS INTEGER) = s.id)
                OR (f.type = 'command' AND CAST(f.ref_id AS INTEGER) = s.id)
                OR (f.type = 'commit' AND s.project_id = CAST(f.project_id AS INTEGER)
                    AND CAST(f.timestamp AS INTEGER) >= s.start_time - 300
                    AND CAST(f.timestamp AS INTEGER) <= s.end_time + 600)
            )
            WHERE f.ref_id IS NOT NULL
        """

        # Project-name LIKE clauses as an additional OR branch
        project_clauses = []
        project_params: List = []
        for w in words:
            project_clauses.append("p.name LIKE ?")
            project_params.append(f"%{w}%")

        if project_clauses:
            sql += f" OR ( {' OR '.join(project_clauses)} )"

        params = [fts_query] + project_params
        cursor.execute(sql, params)
        session_ids = [row[0] for row in cursor.fetchall()]

    except sqlite3.OperationalError:
        # FTS5 not available – fall back to LIKE-based retrieval
        try:
            cursor = conn.cursor()
            where_clauses = []
            params = []
            for w in words:
                term_like = f"%{w}%"
                where_clauses.append(
                    "(p.name LIKE ? OR c.command LIKE ? OR "
                    "co.message LIKE ? OR co.cleaned_message LIKE ? OR s.ai_summary LIKE ?)"
                )
                params.extend([term_like, term_like, term_like, term_like, term_like])

            sql = f"""
                SELECT DISTINCT s.id
                FROM sessions s
                LEFT JOIN projects p ON s.project_id = p.id
                LEFT JOIN commands c ON s.id = c.session_id
                LEFT JOIN commits co ON s.project_id = co.project_id
                    AND co.timestamp >= s.start_time - 300
                    AND co.timestamp <= s.end_time + 600
                WHERE {" OR ".join(where_clauses)}
            """
            cursor.execute(sql, params)
            session_ids = [row[0] for row in cursor.fetchall()]
        except Exception:
            session_ids = []
    finally:
        conn.close()

    if not session_ids:
        return []

    # 2. Retrieve full Session objects ────────────────────────────────────────
    sessions = db.get_sessions_by_ids(session_ids)
    if not sessions:
        return []

    project_map = _get_project_names_map()

    # 3. BM25 Ranking (unigrams + bigrams) ────────────────────────────────────
    q_terms = _query_terms(words)

    # Tokenise each session into a Counter for efficient lookup
    docs: List[Tuple[Counter, int]] = []
    for s in sessions:
        toks = _session_tokens(s, project_map)
        docs.append((Counter(toks), len(toks)))

    N = len(sessions)
    avg_doc_len = sum(doc_len for _, doc_len in docs) / N if N > 0 else 1.0

    # Document frequency per query term (with prefix matching for unigrams)
    df: Dict[str, int] = {}
    for q_t in q_terms:
        is_bigram = "_" in q_t
        count = 0
        for counter, _ in docs:
            if is_bigram:
                if counter.get(q_t, 0) > 0:
                    count += 1
            else:
                if any("_" not in k and k.startswith(q_t) for k in counter):
                    count += 1
        df[q_t] = count

    # Score every session
    scored: List[Tuple[float, Session]] = []
    for idx, s in enumerate(sessions):
        counter, doc_len = docs[idx]
        bm25 = _bm25_score(q_terms, counter, doc_len, avg_doc_len, df, N)
        scored.append((bm25, s))

    # Sort descending by score; break ties by recency (most recent first)
    scored.sort(key=lambda x: (x[0], x[1].start_time), reverse=True)

    return [s for _, s in scored]


def generate_answer(query: str, sessions: List[Session], ai_client) -> Optional[str]:
    """
    Constructs a contextual Q&A prompt using the given query and matched sessions,
    and runs it against the configured LLM client.

    Security: session commands are sanitized via sanitize_session_commands() before
    being embedded in the prompt. Sessions containing blacklisted commands (vault,
    aws configure, gh auth, raw token strings, etc.) have their entire COMMANDS block
    replaced with '[REDACTED: Security/Authentication Operations]'.
    Git commit messages are sanitized via redact_command() on each message string.
    """
    if not query.strip():
        return "Please provide a valid query."

    if not sessions:
        return "I could not find any sessions matching your query in the shell history."

    # Extract credentials and provider settings from ai_client
    api_key = ""
    api_base_url = ""
    model_name = ""
    provider = "disabled"

    if isinstance(ai_client, dict):
        provider = ai_client.get("provider") or ai_client.get("active_provider") or "disabled"
        providers = ai_client.get("providers", {})
        if provider in providers:
            api_key = providers[provider].get("api_key") or ""
            api_base_url = providers[provider].get("api_base_url") or ""
            model_name = providers[provider].get("model_name") or ""
        else:
            api_key = ai_client.get("api_key") or ""
            api_base_url = ai_client.get("api_base_url") or ""
            model_name = ai_client.get("model_name") or ""
        request_timeout_seconds = ai_client.get("request_timeout_seconds", 30.0)
    else:
        provider = getattr(ai_client, "provider", None) or getattr(ai_client, "active_provider", "disabled")
        providers = getattr(ai_client, "providers", None)
        if isinstance(providers, dict) and provider in providers:
            api_key = providers[provider].get("api_key") or ""
            api_base_url = providers[provider].get("api_base_url") or ""
            model_name = providers[provider].get("model_name") or ""
        else:
            api_key = getattr(ai_client, "api_key", "")
            api_base_url = getattr(ai_client, "api_base_url", "")
            model_name = getattr(ai_client, "model_name", "")
        request_timeout_seconds = getattr(ai_client, "request_timeout_seconds", 30.0)

    # config.json is loaded via json.load() with no per-key type/range
    # validation (see config.py:load_config), and `termstory config set
    # request_timeout_seconds <value>` will happily store 0, a negative
    # number, or (if the stored value has ever been a float) "inf". Each
    # of those reaches ai.py's timeout handling differently and badly:
    #   - non-numeric (None/str): timeout + 1.0 raises an uncaught
    #     TypeError outside the worker thread's try/except, crashing
    #     generate_answer entirely.
    #   - bool: a subclass of int in Python, so isinstance(x, (int, float))
    #     alone lets it through as timeout=1/0 (True==1/False==0).
    #   - 0: urlopen puts the socket in non-blocking mode, so every
    #     request fails instantly with EINPROGRESS regardless of network
    #     conditions.
    #   - negative: join_timeout = timeout + 1.0 goes negative, so the
    #     join loop's elapsed-time check is already true on its first
    #     iteration -- it never actually waits for the worker.
    #   - inf/nan: urlopen(timeout=float('inf')) raises OverflowError,
    #     which isn't in the worker's caught exception tuple, so it dies
    #     silently in the background thread instead of returning an
    #     answer or a clean error.
    # Falls back to the same 30.0 default for any value outside
    # (0, +inf) rather than trying to enumerate every bad case above.
    if (
        isinstance(request_timeout_seconds, bool)
        or not isinstance(request_timeout_seconds, (int, float))
        or not math.isfinite(request_timeout_seconds)
        or request_timeout_seconds <= 0
    ):
        request_timeout_seconds = 30.0

    if not provider or provider == "disabled":
        return "AI capabilities are currently disabled."

    # Fetch project names map
    project_map = _get_project_names_map()

    # Format session contexts into a technical audit block
    context_blocks = []
    for idx, s in enumerate(sessions):
        p_name = project_map.get(s.project_id, "Other") if s.project_id is not None else "Other"

        block = [
            f"Session #{idx + 1}",
            f"Date: {s.date_str} ({s.start_time_formatted}, Duration: {s.duration_readable})",
            f"Project: {p_name}",
        ]

        if s.ai_summary:
            block.append(f"Summary: {s.ai_summary.strip()}")

        if s.commands:
            # Check blacklist against ALL commands, not just the displayed
            # slice — a sensitive op at index N>40 must still gate the session.
            all_cmds = [cmd.command for cmd in s.commands]
            _, is_blacklisted = sanitize_session_commands(all_cmds)
            block.append("Commands:")
            if is_blacklisted:
                block.append("  - [REDACTED: Security/Authentication Operations]")
            else:
                sanitized_cmds, _ = sanitize_session_commands(all_cmds[:40])
                for sc in sanitized_cmds:
                    block.append(f"  - {sc}")
                if len(s.commands) > 40:
                    block.append(f"  - ... ({len(s.commands) - 40} more commands)")

        if s.commits:
            block.append("Git Commits:")
            for commit in s.commits[:15]:
                msg = commit.get("cleaned_message") or commit.get("message") or ""
                if msg.strip():
                    block.append(f"  - {redact_command(msg.strip())}")

        context_blocks.append("\n".join(block))

    context_text = "\n\n=========================================\n\n".join(context_blocks)

    prompt = (
        "You are TermStory Q&A Assistant, an AI helper that answers queries about the user's shell history and development activity.\n"
        "You are given a query and a set of matched shell sessions containing commands, git commits, and session summaries.\n\n"
        "Here is the context of matched sessions:\n"
        "-----------------------------------------\n"
        f"{context_text}\n"
        "-----------------------------------------\n\n"
        f"User Query: {query}\n\n"
        "INSTRUCTIONS:\n"
        "1. Answer the user's query as accurately and concisely as possible using ONLY the provided context.\n"
        "2. If the context does not contain the answer, say so clearly (e.g. 'I could not find information matching your query in the history.').\n"
        "3. Provide relevant command examples, project names, or commit messages when applicable.\n"
        "4. Be technical, developer-friendly, and avoid unnecessary filler or fluff.\n"
        "5. Never output API keys, tokens, passwords, or credential values verbatim, even if present in the context. Refer to them generically (e.g. 'an API key was configured') instead.\n\n"
        "Answer:"
    )

    result = _send_llm_request(
        prompt=prompt,
        api_key=api_key,
        api_base_url=api_base_url,
        model_name=model_name,
        provider=provider,
        max_tokens=_DEFAULT_MAX_TOKENS,
        timeout=request_timeout_seconds,
    )
    return result
