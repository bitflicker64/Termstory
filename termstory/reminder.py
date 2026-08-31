import json
import logging
import math
import os
import re
import sqlite3
import time
from typing import List, Dict, Optional, Tuple
from termstory.config import get_app_dir, load_config
from termstory.sanitizer import sanitize_session_commands

logger = logging.getLogger(__name__)

def get_reminders_file_path() -> str:
    """Return path to reminders JSON file"""
    return os.path.join(get_app_dir("data"), "reminders.json")

def load_reminders() -> List[Dict]:
    """Load all reminders from the JSON file"""
    path = get_reminders_file_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        logger.exception("Failed to load reminders from %s", path)
        return []

def save_reminders(reminders: List[Dict]) -> None:
    """Save all reminders to the JSON file"""
    path = get_reminders_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=4)

def parse_reminder_text(text: str) -> Tuple[str, int]:
    """Parse a phrase like 'remind me about X in N days' or 'X in N days'
    to extract description X and days N.
    """
    text = re.sub(r'\s+', ' ', text.strip())
    
    # Pattern 1: (remind me )?(about|to) <X> in <N> day(s)
    pattern1 = re.compile(
        r"^(?:remind\s+me\s+)?(?:about|to)\s+(.+?)\s+in\s+(\d+)\s+days?$",
        re.IGNORECASE
    )
    m1 = pattern1.match(text)
    if m1:
        return m1.group(1).strip(), int(m1.group(2))
        
    # Pattern 2: <X> in <N> day(s)
    pattern2 = re.compile(r"^(.+?)\s+in\s+(\d+)\s+days?$", re.IGNORECASE)
    m2 = pattern2.match(text)
    if m2:
        return m2.group(1).strip(), int(m2.group(2))
        
    raise ValueError(
        "Could not parse reminder phrase. Please use format like "
        "'remind me about X in N days' or 'X in N days'."
    )

def add_reminder(
    text: str,
    days: Optional[int] = None,
    db = None
) -> Dict:
    """Parse, create, and save a new reminder.
    Associates the reminder with the latest session in the database if available.
    """
    if days is not None:
        # Normalize whitespace and consistently strip prefix/suffix
        text_clean = re.sub(r'\s+', ' ', text.strip())
        prefix_pattern = re.compile(r"^(?:remind\s+me\s+)?(?:about|to)\s+", re.IGNORECASE)
        about = prefix_pattern.sub("", text_clean)
        suffix_pattern = re.compile(r"\s+in\s+(\d+)\s+days?$", re.IGNORECASE)
        about = suffix_pattern.sub("", about).strip()
    else:
        about, days = parse_reminder_text(text)
        
    if type(days) is not int:
        raise TypeError("Days must be an integer.")

    if not 0 <= days <= 3650:
        raise ValueError("Days must be between 0 and 3650.")

    created_at = int(time.time())
    due_at = created_at + (days * 86400)
    
    # Get latest session if database is provided
    session_id = None
    project_name = "Other"
    
    if db is not None:
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.id, p.name
                FROM sessions s
                LEFT JOIN projects p ON s.project_id = p.id
                ORDER BY s.start_time DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                session_id = row[0]
                project_name = row[1] or "Other"
        except Exception as exc:
            logger.warning(
                "add_reminder: failed to fetch latest session from DB; "
                "reminder will be created without project association. Error: %s",
                exc,
            )
        finally:
            conn.close()

    reminders = load_reminders()
    
    # Generate next ID
    existing_ids = [r.get("id") for r in reminders if isinstance(r.get("id"), int)]
    next_id = max(existing_ids) + 1 if existing_ids else 1
    
    new_reminder = {
        "id": next_id,
        "about": about,
        "days": days,
        "created_at": created_at,
        "due_at": due_at,
        "session_id": session_id,
        "project_name": project_name,
        "status": "pending"
    }
    
    reminders.append(new_reminder)
    save_reminders(reminders)
    return new_reminder

def complete_reminder(reminder_id: int) -> bool:
    """Mark a reminder as completed"""
    reminders = load_reminders()
    updated = False
    for r in reminders:
        if r.get("id") == reminder_id:
            r["status"] = "completed"
            updated = True
            break
            
    if updated:
        save_reminders(reminders)
    return updated


_DEFAULT_CLUSTERING_THRESHOLD = 0.6


def _resolve_clustering_threshold() -> float:
    """Read ``clustering_threshold`` from config.json.

    Falls back to :data:`_DEFAULT_CLUSTERING_THRESHOLD` if config is
    unavailable, malformed, or the value cannot be coerced to float.
    Shared by :func:`cluster_commands` (per-call default) and
    :func:`consolidate_sleep_contexts` (resolved once per run).
    """
    try:
        cfg = load_config()
        return float(cfg.get("clustering_threshold", _DEFAULT_CLUSTERING_THRESHOLD))
    except (OSError, TypeError, ValueError) as exc:
        logger.exception("Failed to resolve clustering threshold; using default")
        return _DEFAULT_CLUSTERING_THRESHOLD


def cluster_commands(commands: List[str], threshold: Optional[float] = None) -> List[List[str]]:
    """Cluster similar commands using sentence-transformers embeddings.

    Args:
        commands: Commands to cluster.
        threshold: Cosine similarity threshold above which two commands are
            merged into the same cluster. Lower values over-merge unrelated
            commands; higher values split related ones. Defaults to the
            ``clustering_threshold`` config value (falls back to
            ``_DEFAULT_CLUSTERING_THRESHOLD`` if config is unavailable).
    """
    if not commands:
        return []
        
    # Clean and deduplicate commands
    unique_cmds = []
    seen = set()
    for cmd in commands:
        cleaned = cmd.strip()
        if cleaned and cleaned not in seen:
            unique_cmds.append(cleaned)
            seen.add(cleaned)
            
    if not unique_cmds:
        return []
        
    # Attempt to use sentence-transformers
    try:
        from termstory.rag import get_embeddings, SENTENCE_TRANSFORMERS_AVAILABLE
    except ImportError:
        SENTENCE_TRANSFORMERS_AVAILABLE = False

    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        # Fallback: group by first word
        verb_clusters = {}
        for cmd in unique_cmds:
            verb = cmd.split()[0] if cmd.split() else "other"
            if verb not in verb_clusters:
                verb_clusters[verb] = []
            verb_clusters[verb].append(cmd)
        return list(verb_clusters.values())

    try:
        embeddings = get_embeddings(unique_cmds)
        # Convert to list of lists if numpy array
        if hasattr(embeddings, "tolist"):
            emb_list = embeddings.tolist()
        else:
            emb_list = embeddings
    except Exception:
        # Fallback if encoding failed
        logger.exception("Failed to generate embeddings for command clustering")
        verb_clusters = {}
        for cmd in unique_cmds:
            verb = cmd.split()[0] if cmd.split() else "other"
            if verb not in verb_clusters:
                verb_clusters[verb] = []
            verb_clusters[verb].append(cmd)
        return list(verb_clusters.values())
        
    # Simple leader clustering
    clusters = []
    cluster_embs = []
    if threshold is None:
        threshold = _resolve_clustering_threshold()
    
    for cmd, emb in zip(unique_cmds, emb_list):
        placed = False
        for i, (cluster, c_emb) in enumerate(zip(clusters, cluster_embs)):
            dot = sum(x * y for x, y in zip(emb, c_emb))
            norm1 = math.sqrt(sum(x * x for x in emb))
            norm2 = math.sqrt(sum(x * x for x in c_emb))
            sim = dot / (norm1 * norm2) if (norm1 > 0 and norm2 > 0) else 0.0
            
            if sim >= threshold:
                new_center = []
                for x, y in zip(c_emb, emb):
                    new_center.append((x * len(cluster) + y) / (len(cluster) + 1))
                cluster_embs[i] = new_center
                cluster.append(cmd)
                placed = True
                break
        if not placed:
            clusters.append([cmd])
            cluster_embs.append(emb)
            
    return clusters


def generate_cluster_summary(commands: List[str]) -> str:
    """Generate a single-line, high-density summary of a command cluster.

    Security: commands are sanitized via sanitize_session_commands() before being
    embedded in the LLM prompt (same contract as generate_ai_summary()). Clusters
    containing blacklisted commands (vault, aws configure, gh auth, raw token
    strings, etc.) never reach the LLM; the standard redaction marker is returned
    instead.

    Uses the configured AI provider when one is active, otherwise falls back
    to a local, first-token-per-command summary (the ``active_provider ==
    "disabled"`` path, which performs no network I/O and is unchanged).

    Raises:
        Exception: Whatever the configured LLM layer raises (e.g. provider,
            request, timeout, or response-processing errors not normalized to
            ``None`` by :func:`termstory.ai._send_llm_request`). Background
            consolidation treats such exceptions as isolated, per-cluster
            failures that remain recoverable on the next run (see
            :func:`consolidate_sleep_contexts`); callers should not assume an
            exception here means any sibling cluster failed.
    """
    from termstory.config import load_config, get_config_value
    config = load_config()
    provider = config.get("active_provider", "disabled")
    
    if provider == "disabled":
        unique = []
        for c in commands:
            base = c.split()[0] if c.strip() else ""
            if base and base not in unique:
                unique.append(base)
        if not unique:
            return "Idle session"
        return f"Worked on commands: {', '.join(unique[:3])}"

    # Sanitize before anything reaches the LLM prompt. A blacklisted cluster
    # (any vault/aws configure/gh auth-style command) never leaves the machine.
    sanitized_cmds, is_blacklisted = sanitize_session_commands(commands)
    if is_blacklisted:
        # sanitized_cmds is None here — never iterate it.
        return "[REDACTED: Security/Authentication Operations]"

    # Query LLM
    from termstory.ai import _send_llm_request
    prompt = (
        "You are a developer memory engine. Summarize the following cluster of raw terminal commands "
        "into a single-line, high-density, tech-dense summary of what the developer was doing (e.g. 'Set up Docker container and verified logs').\n\n"
        "Commands:\n" + "\n".join(f"- {c}" for c in sanitized_cmds) + "\n\n"
        "Return ONLY the single line summary. No markdown formatting, no conversational filler, and no surrounding quotes."
    )
    
    api_key = get_config_value(config, f"providers.{provider}.api_key") or ""
    api_base_url = get_config_value(config, f"providers.{provider}.api_base_url") or ""
    model_name = get_config_value(config, f"providers.{provider}.model_name") or ""
    
    summary = _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=100, timeout=15.0
    )
    if summary:
        from rich.markup import escape
        return escape(summary.strip())
    
    # Fallback if request failed (local string only — still derived from the
    # sanitized list so raw command text is never reused downstream).
    unique = []
    for c in sanitized_cmds:
        base = c.split()[0] if c.strip() else ""
        if base and base not in unique:
            unique.append(base)
    return f"Worked on commands: {', '.join(unique[:3])}"


def consolidate_sleep_contexts(db, force: bool = False) -> int:
    """Detect idle periods (30+ min gaps in command history or since last command)
    and consolidate command contexts into summaries.

    Failure isolation contract (per-cluster recovery):

    Each cluster produced by :func:`cluster_commands` is summarized
    independently. If :func:`generate_cluster_summary` raises for one cluster
    (LLM/provider timeout, malformed response, request failure, or any other
    per-cluster processing error), that exception is contained: the cluster
    contributes no summary this run, sibling clusters keep processing, and the
    chunk is persisted with only the summaries gathered before the failure.
    Persistence failures from ``db.save_consolidated_context()`` are *not*
    swallowed here — they propagate to the caller (a background daemon that
    already logs uncaught exceptions), because a failed write means successful
    work was not committed and must not be reported as consolidated.

    Recovery semantics: ``rem_sleep_consolidation`` has no separate watermark
    column — the next run's eligibility boundary is
    ``MAX(end_time)`` over persisted rows. Eligible commands are strictly those
    newer than that boundary. Consequently:

    - A chunk whose clusters all fail persists nothing, so its commands stay
      eligible and are retried on the next run.
    - A partially-failed chunk is persisted only up to the point of failure:
      summaries for clusters whose commands are all strictly older than every
      command of the earliest failed cluster form a leading chronological
      prefix, which is saved with an ``end_time`` below the earliest failed
      timestamp. The surviving work (the failed cluster's commands onward)
      therefore remains eligible and is retried — overlapping-free, so a
      retry cannot produce duplicate rows for already-persisted windows.
    - Retry idempotency: a run starts after the existing watermark and only
      writes rows ending below it; previously persisted work is never rewritten,
      so no duplicates can be created despite the absence of a uniqueness
      constraint on ``(start_time, end_time)``.

    Returns:
        Number of chunks successfully persisted this run (unchanged meaning).
    """
    # 1. Get the last consolidated end_time
    conn = db.get_connection()
    last_end = 0
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(end_time) FROM rem_sleep_consolidation")
        row = cursor.fetchone()
        if row and row[0] is not None:
            last_end = row[0]
    except (sqlite3.DatabaseError, ValueError, TypeError, OSError) as exc:
        logger.exception("Failed to read the last sleep consolidation marker")
    finally:
        conn.close()

    # 2. Fetch all commands since last_end ordered by timestamp ASC
    conn = db.get_connection()
    commands = []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT command, timestamp
            FROM commands
            WHERE timestamp > ?
            ORDER BY timestamp ASC
        """, (last_end,))
        rows = cursor.fetchall()
        for r in rows:
            commands.append({"command": r[0], "timestamp": r[1]})
    except (sqlite3.DatabaseError, ValueError, TypeError, OSError) as exc:
        logger.exception("Failed to fetch commands for sleep consolidation")
    finally:
        conn.close()

    if not commands:
        return 0

    # 3. Group commands into chunks separated by gaps of >= 1800 seconds (30 minutes)
    chunks = []
    current_chunk = [commands[0]]
    
    for cmd in commands[1:]:
        if (cmd["timestamp"] - current_chunk[-1]["timestamp"]) >= 1800:
            chunks.append(current_chunk)
            current_chunk = [cmd]
        else:
            current_chunk.append(cmd)
    if current_chunk:
        chunks.append(current_chunk)

    # 4. Filter chunks followed by an idle period
    now = int(time.time())
    chunks_to_consolidate = []
    
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        if not is_last:
            chunks_to_consolidate.append(chunk)
        else:
            if force or (now - chunk[-1]["timestamp"] >= 1800):
                chunks_to_consolidate.append(chunk)

    if not chunks_to_consolidate:
        return 0

    # 5. Consolidate each chunk
    clustering_threshold = _resolve_clustering_threshold()
    consolidated_count = 0
    for chunk in chunks_to_consolidate:
        start_time = chunk[0]["timestamp"]
        end_time = chunk[-1]["timestamp"]
        cmd_strs = [c["command"] for c in chunk]
        
        clusters = cluster_commands(cmd_strs, threshold=clustering_threshold)

        # Attach each cluster's earliest command timestamp (set intersection,
        # as dedup leaves multiple timestamps per command string) so the
        # checkpoint can be kept below any failed cluster's commands. Without
        # a per-cluster marker in rem_sleep_consolidation, this chronological
        # bookkeeping is what keeps failed work discoverable on the next run.
        cmd_min_ts = {}
        for c in chunk:
            if c["command"] not in cmd_min_ts or c["timestamp"] < cmd_min_ts[c["command"]]:
                cmd_min_ts[c["command"]] = c["timestamp"]

        cluster_summaries = []
        succeeded_with_ts = []  # (summary, latest command timestamp of its cluster)
        failed_clusters = []
        failed_earliest_ts = None
        for cluster in clusters:
            # Per-cluster failure isolation: a summarize failure (e.g. LLM
            # timeout, malformed response, provider error) affects only that
            # cluster. Sibling clusters still run; a failed cluster contributes
            # no summary and is never reported as consolidated — its commands
            # stay recoverable because the checkpoint is not advanced past
            # them (see persistence below).
            try:
                summ = generate_cluster_summary(cluster)
            except Exception:
                logger.exception(
                    "Sleep consolidation: failed to summarize a cluster "
                    "(commands=%d); its commands remain eligible for retry",
                    len(cluster),
                )
                failed_clusters.append(cluster)
                fail_floor = min(cmd_min_ts.get(c, start_time) for c in cluster)
                if failed_earliest_ts is None or fail_floor < failed_earliest_ts:
                    failed_earliest_ts = fail_floor
                continue
            if summ:
                cluster_summaries.append(summ)
                # Defaulting unknown strings to end_time keeps such a cluster
                # out of the safe prefix (withheld -> retried), never the
                # reverse, so success is never claimed prematurely.
                succeeded_with_ts.append(
                    (summ, max(cmd_min_ts.get(c, end_time) for c in cluster))
                )
                
        # If no cluster produced a summary, persist nothing and leave the
        # whole chunk untouched: it stays fully eligible for the next run.
        # (Preserves normal empty-result behavior as before.)
        if not cluster_summaries:
            continue

        # Persist following the existing checkpoint model. The next run
        # discovers commands strictly newer than MAX(end_time), so a saved row
        # must never end at/after a failed cluster's commands — otherwise that
        # failed work would become permanently undiscoverable while looking
        # consolidated. With failures present, successful summaries split into:
        #   - a leading chronological prefix covering only commands strictly
        #     older than every failure (safe to persist now; a retry starts
        #     after it, so it cannot be duplicated), and
        #   - trailing work at/after the failure (withheld, retried later,
        #     including any successful clusters overlapping that region).
        # Chunks with no failed cluster keep the original single-write path.
        if failed_clusters:
            prefix_summaries = [
                s for s, cluster_last_ts in succeeded_with_ts
                if cluster_last_ts < failed_earliest_ts
            ]
            if not prefix_summaries:
                # Nothing fully precedes the failure: withhold everything,
                # including successful clusters, so the whole chunk retries
                # intact on the next run (no partial-window rewrites).
                continue
            # Anchor the saved row to the actual work being persisted, never
            # past the earliest failed command (guaranteed by the filter above).
            save_start = start_time
            save_end = max(ts for s, ts in succeeded_with_ts if ts < failed_earliest_ts)
            save_commands = [
                c["command"] for c in chunk if save_start <= c["timestamp"] <= save_end
            ]
        else:
            prefix_summaries = cluster_summaries
            save_start, save_end, save_commands = start_time, end_time, cmd_strs
            
        if len(prefix_summaries) == 1:
            final_summary = prefix_summaries[0]
        else:
            final_summary = " | ".join(prefix_summaries)
            
        db.save_consolidated_context(save_start, save_end, final_summary, save_commands)
        consolidated_count += 1

    return consolidated_count


# Bounded stale threshold, in seconds, after which an empty/unparseable
# sleep-daemon PID file is considered abandoned and safe to reclaim.
#
# When a caller wins the atomic ``O_CREAT | O_EXCL`` claim it creates the PID
# file and then publishes its PID. Concurrent callers that observe the file
# before that PID is written must NOT delete it merely because it does not yet
# parse: doing so would mistake a freshly-claimed, in-progress claim for a
# stale one and let a second caller reclaim it, spawning two daemons. A
# recently-created empty/unparseable claim is therefore treated as an active
# in-progress claim; only once this threshold has elapsed without a parseable
# PID appearing is it safe to reclaim as abandoned.
_SLEEP_DAEMON_CLAIM_STALE_SECONDS = 2.0


def _pid_from_file(pid_file: str) -> Optional[int]:
    """Return the PID stored in the sleep-daemon PID file, or None.

    Returns None when the file cannot be read or does not hold a parseable,
    positive integer PID - e.g. it is empty because the claiming caller has not
    published its PID yet, or it is malformed.
    """
    try:
        with open(pid_file, "r") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid


def _claim_is_stale(pid_file: str) -> bool:
    """Return True when an existing sleep-daemon claim is safe to reclaim.

    A claim is stale (and therefore reclaimable) only in two situations:

      * it holds a valid PID whose process is no longer alive (a daemon that
        died without cleaning up), or
      * it is empty/unparseable *and* older than the bounded stale threshold
        ``_SLEEP_DAEMON_CLAIM_STALE_SECONDS``, i.e. a genuinely abandoned claim
        whose creator never published a PID.

    Every other state - a live daemon, or a fresh in-progress claim whose PID
    has not been published yet - is treated as active and NOT reclaimable. This
    is what prevents the race where a freshly created-but-unpopulated PID file
    is mistaken for a stale one and deleted, allowing a second claimant to
    spawn a duplicate daemon.
    """
    pid = _pid_from_file(pid_file)
    if pid is not None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True  # daemon process has gone away
        except (PermissionError, OSError):
            # Cannot confirm the process is dead; conservatively defer rather
            # than race over a possibly-live claim.
            return False
        return False  # process is alive; defer

    # Empty or unparseable PID file. Use its modification time (which for a
    # freshly created, not-yet-populated claim reflects its creation) to tell
    # an in-progress claim apart from an abandoned one.
    try:
        age = time.time() - os.path.getmtime(pid_file)
    except OSError:
        # The file disappeared or is unreadable; it no longer represents a
        # valid claim, so retrying the atomic claim is harmless.
        return True
    if age < _SLEEP_DAEMON_CLAIM_STALE_SECONDS:
        return False  # in-progress claim; defer, do not delete
    return True  # abandoned empty/unparseable claim; reclaim


def _remove_claim_safely(pid_file: str, owner_pid: Optional[int]) -> None:
    """Remove a claim we created, without removing a replacement claim.

    Between claiming the file and cleaning it up (e.g. on a failure), another
    caller may have reclaimed the file and re-populated it with its own PID. To
    avoid deleting that replacement's claim we only remove the file while it
    still carries our content: ``owner_pid`` if we published one, or an empty
    file if we had not published yet.
    """
    current = _pid_from_file(pid_file)
    if owner_pid is not None:
        if current != owner_pid:
            return  # someone else owns the claim now; leave it alone
    else:
        if current is not None:
            return  # a replacement published a PID; leave it alone
    try:
        os.remove(pid_file)
    except FileNotFoundError:
        pass  # already gone; that is fine
    except OSError:
        logger.exception("Failed to remove sleep daemon PID file %s", pid_file)


def start_sleep_daemon(db_path: str):
    """Spawns the sleep daemon in the background if it's not already running.

    Daemon ownership is claimed atomically: the PID file is created with
    ``O_CREAT | O_EXCL`` so that, of any number of concurrent invocations,
    exactly one wins the race and goes on to spawn the daemon. Every other
    invocation finds the file already exists and defers to the running (or
    just-started) daemon, which later overwrites the placeholder PID with its
    own.

    The protocol distinguishes four states of an existing PID file:

      * a valid PID belonging to a live process -> defer (daemon running),
      * a valid PID belonging to a dead process -> reclaim as stale,
      * a fresh empty/unparseable file -> defer (the owner created the claim
        but has not published its PID yet; never deleted just because parsing
        fails),
      * an empty/unparseable file older than the bounded stale threshold
        ``_SLEEP_DAEMON_CLAIM_STALE_SECONDS`` -> reclaim as abandoned.
    """
    import sys
    import subprocess

    pid_file = os.path.join(get_app_dir("data"), "sleep_daemon.pid")
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)

    our_pid = os.getpid()
    for _attempt in range(2):
        try:
            fd = os.open(pid_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            observed = _pid_from_file(pid_file)
            if not _claim_is_stale(pid_file):
                # A live daemon runs, or another caller is about to run it
                # (including a fresh in-progress claim whose PID has not been
                # published yet). Defer and leave the PID file untouched.
                return
            # The existing claim is stale (a daemon died without cleaning up,
            # or an abandoned empty/unparseable claim past the stale threshold).
            # Reclaim it, then retry the atomic claim on the next iteration.
            #
            # We never remove the file blindly: between our stale inspection
            # and the removal, another invocation may have reclaimed the file
            # and published its own live PID. ``_remove_claim_safely`` only
            # deletes the file while it still carries the exact stale content
            # we observed (a dead PID, or an empty/unparseable claim), so a
            # concurrently-created replacement claim is left untouched. If the
            # file disappeared in the meantime it is a no-op; either way we
            # simply retry the atomic claim below.
            _remove_claim_safely(pid_file, owner_pid=observed)
            continue
        else:
            # We own the claim. Publish our PID as a placeholder so concurrent
            # invocations can recognise it as a claim held by a live owner.
            published = True
            try:
                os.write(fd, str(our_pid).encode("ascii"))
            except OSError:
                logger.exception("Failed to write sleep daemon PID file %s", pid_file)
                published = False
            finally:
                os.close(fd)
            if not published:
                # We could not publish a PID for our claim. Clean it up, but
                # never remove a replacement claim that another process may
                # have created in the meantime.
                _remove_claim_safely(pid_file, owner_pid=None)
                return
            break
    else:
        # Exhausted the bounded reclaim attempts without acquiring the claim.
        return

    # Inherit and configure the python path
    env = os.environ.copy()
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = package_root + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = package_root

    try:
        subprocess.Popen(
            [sys.executable, "-c", f"from termstory.reminder import run_sleep_daemon; run_sleep_daemon({repr(db_path)})"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env
        )
    except OSError:
        logger.exception("Failed to start sleep daemon")
        # We claimed ownership but failed to spawn; release the claim so a
        # later invocation can start the daemon. Only remove the file while it
        # still carries our placeholder PID, so we never delete a replacement
        # claim created by another process.
        _remove_claim_safely(pid_file, owner_pid=our_pid)


def run_sleep_daemon(db_path: str):
    """Run a daemon loop checking for idle periods and consolidating contexts."""
    import sys
    import signal
    from termstory.database import Database
    from termstory.config import load_config

    config = load_config()
    poll_interval = config.get("reminder_poll_interval", 300)
    if (
        isinstance(poll_interval, bool)
        or not isinstance(poll_interval, (int, float))
        or not math.isfinite(poll_interval)
        or poll_interval <= 0
    ):
        poll_interval = 300
    pid_file = os.path.join(get_app_dir("data"), "sleep_daemon.pid")
    
    def cleanup_pid(signum, frame):
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError as exc:
            logger.exception("Failed to remove sleep daemon PID file %s", pid_file)
        sys.exit(0)
        
    signal.signal(signal.SIGTERM, cleanup_pid)
    signal.signal(signal.SIGINT, cleanup_pid)

    try:
        try:
            with open(pid_file, "w") as f:
                f.write(str(os.getpid()))
        except OSError as exc:
            logger.exception("Failed to write sleep daemon PID file %s", pid_file)
            
        db = Database(db_path)
        while True:
            try:
                consolidate_sleep_contexts(db, force=False)
            except Exception:
                logger.exception("Failed to run consolidate_sleep_contexts in sleep daemon")
            time.sleep(poll_interval)
    finally:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError as exc:
            logger.exception("Failed to remove sleep daemon PID file %s", pid_file)
