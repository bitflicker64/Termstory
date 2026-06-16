import json
import urllib.request
import urllib.error
import threading
import time
import socket
from typing import List, Optional, Dict
from termstory.sanitizer import sanitize_session_commands

_local_ai_state = threading.local()

# Circuit Breaker Configuration
_circuit_breaker_lock = threading.Lock()
_circuit_breaker_failures = 0
_circuit_breaker_open_until = 0.0
MAX_FAILURES = 3
COOLDOWN_SECONDS = 60.0

def reset_circuit_breaker() -> None:
    """Reset the global circuit breaker state. Primarily used for testing."""
    global _circuit_breaker_failures, _circuit_breaker_open_until
    with _circuit_breaker_lock:
        _circuit_breaker_failures = 0
        _circuit_breaker_open_until = 0.0

def get_last_ai_error() -> Optional[str]:
    """Retrieve the last AI call error message, if any, for the current thread."""
    return getattr(_local_ai_state, "last_error", None)

def clear_last_ai_error() -> None:
    """Clear the last AI call error message for the current thread."""
    if hasattr(_local_ai_state, "last_error"):
        delattr(_local_ai_state, "last_error")

def _is_current_worker_cancelled() -> bool:
    try:
        from textual.worker import get_current_worker, NoActiveWorker
        try:
            worker = get_current_worker()
            return worker.is_cancelled
        except NoActiveWorker:
            return False
    except ImportError:
        return False

def _send_llm_request(
    prompt: str,
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    max_tokens: int = 1500,
    timeout: float = 30.0
) -> Optional[str]:
    """Shared helper to construct and send the OpenAI-compatible chat completion request."""
    _local_ai_state.last_error = None

    if _is_current_worker_cancelled():
        return None

    if provider == "disabled":
        return None
        
    global _circuit_breaker_failures, _circuit_breaker_open_until
    with _circuit_breaker_lock:
        if time.time() < _circuit_breaker_open_until:
            _local_ai_state.last_error = "Circuit breaker open: Too many LLM timeouts. Requests temporarily disabled."
            return None

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TermStory/1.0"
    }
    if api_key and isinstance(api_key, str) and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
        
    # OpenAI compatibility endpoint (normalize trailing slash)
    if not api_base_url or not isinstance(api_base_url, str):
        _local_ai_state.last_error = "API Base URL is not configured or invalid."
        return None
    endpoint = api_base_url.strip().rstrip('/')
    if not endpoint.endswith('/chat/completions'):
        endpoint = f"{endpoint}/chat/completions"
        
    body = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens
    }
    
    req_data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
    
    max_retries = 3
    backoff = 1.0
    
    for attempt in range(max_retries + 1):
        if attempt > 0:
            sleep_start = time.time()
            while time.time() - sleep_start < backoff:
                if _is_current_worker_cancelled():
                    return None
                time.sleep(0.1)
            backoff *= 2.0
            
        if _is_current_worker_cancelled():
            return None

        result_box = []
        error_box = []

        def _worker():
            try:
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(timeout)
                try:
                    # Set a reasonable timeout for the TUI background thread
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        resp_data = response.read().decode("utf-8")
                        resp_json = json.loads(resp_data)
                        result = resp_json["choices"][0]["message"]["content"].strip()
                        # Clean up any quotes added by the LLM
                        if result.startswith('"') and result.endswith('"'):
                            result = result[1:-1]
                        if result.startswith("'") and result.endswith("'"):
                            result = result[1:-1]
                        result_box.append(result)
                finally:
                    socket.setdefaulttimeout(old_timeout)
            except Exception as e:
                error_box.append(e)

        worker_thread = threading.Thread(target=_worker, daemon=True)
        worker_thread.start()
        
        # Strict wall-clock timeout wrapper using threading
        start_time = time.time()
        join_timeout = timeout + 1.0
        while worker_thread.is_alive():
            if _is_current_worker_cancelled():
                return None
            worker_thread.join(0.05)
            if time.time() - start_time > join_timeout:
                break
                
        if _is_current_worker_cancelled():
            return None
            
        if worker_thread.is_alive():
            # Half-open socket zombie or severe hang detected
            with _circuit_breaker_lock:
                _circuit_breaker_failures += 1
                if _circuit_breaker_failures >= MAX_FAILURES:
                    _circuit_breaker_open_until = time.time() + COOLDOWN_SECONDS
            _local_ai_state.last_error = f"Strict timeout exceeded ({timeout}s). LLM process hung."
            continue
            
        if error_box:
            e = error_box[0]
            # Identify if error is a timeout
            is_timeout = False
            if isinstance(e, socket.timeout) or isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                is_timeout = True
            elif isinstance(e, urllib.error.URLError) and hasattr(e, "reason") and (
                isinstance(e.reason, socket.timeout) or
                isinstance(e.reason, TimeoutError) or
                "timed out" in str(e.reason).lower()
            ):
                is_timeout = True
                
            if is_timeout:
                with _circuit_breaker_lock:
                    _circuit_breaker_failures += 1
                    if _circuit_breaker_failures >= MAX_FAILURES:
                        _circuit_breaker_open_until = time.time() + COOLDOWN_SECONDS
            else:
                with _circuit_breaker_lock:
                    _circuit_breaker_failures = 0
                    
            if isinstance(e, urllib.error.HTTPError):
                try:
                    raw_body = e.read().decode("utf-8").strip()
                    if not raw_body:
                        reason_str = " ".join(str(e.reason).split())
                        if len(reason_str) > 200:
                            reason_str = reason_str[:200] + "..."
                        _local_ai_state.last_error = f"HTTP Error {e.code}: {reason_str}"
                    else:
                        try:
                            err_json = json.loads(raw_body)
                            msg = err_json.get("error", {}).get("message")
                            if msg:
                                msg_str = " ".join(str(msg).split())
                                if len(msg_str) > 200:
                                    msg_str = msg_str[:200] + "..."
                                _local_ai_state.last_error = f"HTTP Error {e.code}: {msg_str}"
                            else:
                                body_str = " ".join(raw_body.split())
                                if len(body_str) > 200:
                                    body_str = body_str[:200] + "..."
                                _local_ai_state.last_error = f"HTTP Error {e.code}: {body_str}"
                        except Exception:
                            body_str = " ".join(raw_body.split())
                            if len(body_str) > 200:
                                body_str = body_str[:200] + "..."
                            _local_ai_state.last_error = f"HTTP Error {e.code}: {body_str}"
                except Exception:
                    reason_str = " ".join(str(e.reason).split())
                    if len(reason_str) > 200:
                        reason_str = reason_str[:200] + "..."
                    _local_ai_state.last_error = f"HTTP Error {e.code}: {reason_str}"
            else:
                # Gracefully fail and capture normalized exception
                msg = " ".join(str(e).split())
                if len(msg) > 200:
                    msg = msg[:200] + "..."
                _local_ai_state.last_error = msg
            continue

        # Success
        with _circuit_breaker_lock:
            _circuit_breaker_failures = 0
            
        return result_box[0] if result_box else None

    return None

def generate_ai_summary(
    commands: List[str],
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    project_name: str = "Other",
    commits: Optional[List[str]] = None,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Scrub commands and query the configured LLM API (Groq or Ollama) to generate a summary."""
    if not commands:
        return None
        
    # 1. Sanitization Pipeline
    sanitized_cmds, is_blacklisted = sanitize_session_commands(commands)
    if is_blacklisted:
        return "Security/Authentication Operations"
        
    if not sanitized_cmds:
        return None
        
    # If AI provider is disabled, return None (fallback to local heuristics)
    if provider == "disabled":
        return None
        
    # 2. Formulate the prompt
    MAX_COMMANDS_PER_PROMPT = 80
    if len(sanitized_cmds) > MAX_COMMANDS_PER_PROMPT:
        sanitized_cmds = sanitized_cmds[-MAX_COMMANDS_PER_PROMPT:]  # keep most recent
    commands_block = "\n".join(f"- {cmd}" for cmd in sanitized_cmds)
    
    commits_block = ""
    if commits:
        unique_commits = sorted(list(set(commits)))
        commits_block = "\nGit Commit Messages:\n" + "\n".join(f"- {c}" for c in unique_commits)
        
    prompt = (
        "Translate the developer's raw shell commands and Git commits into a high-density, CLI-styled terminal log of their work session.\n\n"
        "YOUR CORE GOAL:\n"
        "Generate a 3-line bulleted dev log. It must resemble a clean, tech-dense terminal audit output using ASCII connection lines or tech symbols.\n\n"
        "Choose ONE of the following formats to return, matching the inputs:\n\n"
        "Format Option A (ASCII branch log style):\n"
        "[💻 Dev Log]\n"
        "├─ 🔨 Built: <short, punchy action phrase of what was built or coded, using tech keywords>\n"
        "├─ 🔧 Flow: <brief sequence of tools used, tests run, or configurations edited>\n"
        "└─ 🚀 Result: <final milestone shipped, fixed, or pushed>\n\n"
        "Format Option B (Tech bullet list style):\n"
        "[🤖 Codebase Pulse]\n"
        "• Hacked: <what was designed, refactored, or debugged>\n"
        "• Tooling: <commands run, docker setups, or libraries configured>\n"
        "• Outcome: <what was successfully verified, resolved, or shipped>\n\n"
        "Choose either Option A or Option B at random or based on the inputs to provide variation, but always output EXACTLY the selected format.\n"
        "Never output any paragraphs of text, conversational filler, markdown formatting, or surrounding quotes. Only return the raw 4 lines of console text.\n\n"
        "STYLE & TONE RULES:\n"
        "1. NO MARKETING FLUFF: Never write paragraphs like 'Ultimately, the hard work paid off...'. Keep it purely developer-centric and density-focused.\n"
        "2. START WITH ACTION VERBS: Each bullet line must start directly with an active, past-tense engineering verb (e.g., 'wired up', 'refactored', 'debugged', 'spun up', 'implemented').\n"
        "3. Keep each line extremely concise, informative, and technical.\n\n"
        "Input Data to Summarize:\n"
        f"Project: {project_name}\n"
        "Commands Executed:\n"
        f"{commands_block}\n"
        f"{commits_block}\n\n"
        "Output format: Return ONLY the raw, polished console text block. No markdown formatting, no conversational filler, and no surrounding quotes."
    )
    
    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=500, timeout=effective_timeout
    )


def generate_timeframe_summary(
    stats_summary: str,
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Query LLM to generate a professional action-oriented summary of a timeframe."""
    if provider == "disabled":
        return None
        
    from termstory.formatter import get_operator_handle
    github_username = get_operator_handle()
    
    prompt = (
        "Write a highly-personalized, modern engineering review of the developer's work over this entire period based on their commits, session summaries, and tooling stats.\n\n"
        "You are the master narrator for termstory, a developer memory engine. "
        "Your task is to transform the provided developer's time logs, project distribution, commits, and tools into a highly-personalized, creative, and viral terminal chronicle summarizing their entire work period.\n\n"
        
        "YOUR CORE GOAL:\n"
        "Generate one of the following two high-density, console-styled reports. You must choose the format that best fits the input log context to tell a compelling story, but you must output EXACTLY the selected layout structure:\n\n"
        
        "--- FORMAT OPTION 1: THE \"TERMINAL WRAPPED\" (Spotify Wrapped Style) ---\n"
        "This layout must resemble a clean Spotify-Wrapped style CLI card using box drawing characters:\n"
        "┌──────────────────────────────────────────────────────────┐\n"
        "│ ✨ TERMINAL WRAPPED // <TIMEFRAME OR DATE RANGE>          │\n"
        "├──────────────────────────────────────────────────────────┤\n"
        "│ 🎭 ARCHETYPE: \"<Creative Developer Archetype based on work>\" │\n"
        "│               (<Witty sub-description about their stats>) │\n"
        "│                                                          │\n"
        "│ 🎧 TOP GENRE: <Main Tech/Domain category worked on>      │\n"
        "│                                                          │\n"
        "│ 🕒 TIME IN THE SHADOWS: <Total Hours logged>             │\n"
        "│ ├── Focus Distribution: <Percentages on top 2 projects>  │\n"
        "│ └── Heaviest Lift:     `<Key branch, commit or project>` │\n"
        "│                                                          │\n"
        "│ 📂 MOST-EDITED BUFFERS (The Time Sinks)                  │\n"
        "│ ├── 1. `<Project A / File>`       [XX% of time]          │\n"
        "│ ├── 2. `<Project B / File>`       [XX% of time]          │\n"
        "│ └── 3. `<Project C / File>`       [XX% of time]          │\n"
        "│                                                          │\n"
        "│ 🔴 MOMENT OF DESPAIR / GLORY                             │\n"
        "│ • <A funny, high-signal developer reality or debugging    │\n"
        "│   combat situation extracted from logs/commits>          │\n"
        "└──────────────────────────────────────────────────────────┘\n\n"
        
        "--- FORMAT OPTION 2: THE \"DEVELOPER RPG SPEC SHEET\" ---\n"
        "This layout must resemble a retro video game character sheet card:\n"
        "============================================================\n"
        "🎮 CHARACTER SHEET: @<username> // LEVEL <XX> <CLASS NAME>\n"
        "============================================================\n\n"
        "[⚔️ CHARACTER ATTRIBUTES]\n"
        " • CLASS:     <Witty class name based on tools, e.g. Graph Alchemist>\n"
        " • ALIGNMENT: <e.g. Chaotic Green (Tests pass but nobody knows why)>\n"
        " • WEAPON:    [list of top 3 tools/commands detected]\n"
        " • MANA:      <Witty coffee/espresso/late-night description>\n\n"
        "[📈 PERIOD COMBAT TELEMETRY]\n"
        " • CRIT STRIKE:       <A major achievement or refactor/deletion block>\n"
        " • STAMINA:           <Total hours logged> across <number of sessions> runs.\n"
        " • SHIELD ACCURACY:   <Estimation of success rate based on commits/activity>\n"
        " • SPELL COOLDOWN:    <Time spent waiting or building key things>\n\n"
        "[🎒 INVENTORY BUFFERS]\n"
        " ├── 🧪 `<Top Project A Name>` (<Focus hours or percentage>)\n"
        " ├── 💥 `<Top Project B Name>` (<Focus hours or percentage>)\n"
        " └── 🛡️ `<Top Project C Name>` (<Focus hours or percentage>)\n\n"
        "AI LORE SUMMARY: \"<2-sentence epic story of their developer journey during this timeframe>\"\n"
        "============================================================\n\n"
        
        "RULES FOR GENERATION:\n"
        "1. Start directly with the box outline or character sheet. Do not output any preamble, markdown code blocks, conversational filler, or surrounding quotes.\n"
        "2. Do not show empty brackets or placeholders. Extract names, project shares, and commit messages from the context.\n"
        "3. Incorporate actual telemetry: total hours, project distributions, git commit details, and tool keywords.\n"
        "4. Tone must be cool, modern, developer-to-developer, with dry humor, but authentic to the logged data.\n"
        "5. Output must fit in a single terminal screen/grid view cleanly.\n\n"
        f"Developer Work Log Context:\n"
        f"OPERATOR: {github_username}\n"
        f"{stats_summary}\n\n"
        "Output format: Return ONLY the raw, polished console card block. No markdown formatting, no conversational filler, and no surrounding quotes."
    )
    
    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=1500, timeout=effective_timeout
    )


def generate_daily_chronicle_prompt(
    github_username: str,
    session_date: str,
    sessions: List,
    projects: List
) -> str:
    """Generate the Daily Chronicle AI prompt detailing sessions, commands, and inferred gaps."""
    import os
    from datetime import datetime
    from termstory.models import format_duration
    from termstory.formatter import _is_noise_command
    
    # Load template reference
    example_text = ""
    try:
        template_path = os.path.join(os.path.dirname(__file__), "templates", "example_daily_chronicle.txt")
        with open(template_path, "r", encoding="utf-8") as f:
            example_text = f.read().strip()
    except Exception:
        example_text = (
            "🌅 ACT I: THE OPENING ARCHITECTURE [09:15 - 12:02]\n"
            "You opened the terminal and immediately focused on core package wiring.\n"
            "├─ 📂 Project: TermStory (`main` branch)\n"
            "├─ ⌨️  Action:  Modified `parser.py` and `session.py` sequentially.\n"
            "└─ 🧠 Story:   Wired up the Zsh extended format regex tracking rules. You successfully structured the flat sequence pipeline, passing 8 consecutive local syntax tests. Off to a clean start.\n\n"
            "🍕 THE LUNCH BREAK INTERMISSION [12:02 - 13:45]\n"
            "[System Status: Idle for 1h 43m]\n"
            "The terminal went completely cold right after midday. The engine safely infers you stepped away to forage for food.\n\n"
            "====================================================================\n"
            "[CHRONICLE END] You clocked 5h 42m of active focus across 3 projects.\n"
            "===================================================================="
        )
    
    MAX_SESSIONS_PER_PROMPT = 20
    if len(sessions) > MAX_SESSIONS_PER_PROMPT:
        sessions = sessions[-MAX_SESSIONS_PER_PROMPT:]  # keep most recent

    project_map = {p.id: p.name for p in projects if p.id is not None}
    chrono_lines = []
    
    for idx, s in enumerate(sessions):
        dt_start = datetime.fromtimestamp(s.start_time)
        dt_end = datetime.fromtimestamp(s.end_time)
        start_str = dt_start.strftime("%H:%M")
        end_str = dt_end.strftime("%H:%M")
        duration_str = format_duration(s.duration_seconds)
        proj_name = project_map.get(s.project_id, "Other")
        if proj_name == "General / No Project":
            proj_name = "Other"
            
        # Classify time of day for context
        start_hour = dt_start.hour
        if 0 <= start_hour < 5:
            time_context = f"Late-Night/Goblin Mode ({start_str})"
        elif 5 <= start_hour < 8:
            time_context = f"Early Bird Coding ({start_str})"
        elif 8 <= start_hour < 12:
            time_context = f"Morning Session ({start_str})"
        elif 12 <= start_hour < 14:
            time_context = f"Midday Session ({start_str})"
        elif 14 <= start_hour < 17:
            time_context = f"Afternoon Session ({start_str})"
        elif 17 <= start_hour < 21:
            time_context = f"Evening Session ({start_str})"
        else:
            time_context = f"Night Owl Session ({start_str})"
            
        # Analyze activities based on commands
        raw_cmds = [cmd.command for cmd in s.commands]
        activities = []
        
        # Test detection
        if any(any(x in cmd.lower() for x in ["test", "pytest", "unittest", "cargo test", "npm test", "go test"]) for cmd in raw_cmds):
            activities.append("Debugging/Running Tests")
        # Build detection
        if any(any(x in cmd.lower() for x in ["build", "compile", "cargo build", "go build", "npm run build", "make"]) for cmd in raw_cmds):
            activities.append("Compiling/Building Code")
        # Editor detection
        if any(any(x in cmd.lower() for x in ["vim", "nvim", "nano", "code", "emacs"]) for cmd in raw_cmds):
            activities.append("Deep Focused Editing / Coding")
        # Container/cloud detection
        if any(any(x in cmd.lower() for x in ["docker", "kubectl", "aws", "terraform", "gcloud"]) for cmd in raw_cmds):
            activities.append("Wrangling Containers/Infrastructure")
        # Git detection
        if any(any(x in cmd.lower() for x in ["git commit", "git push", "git add"]) for cmd in raw_cmds):
            activities.append("Committing and Pushing Changes")
            
        activity_str = ", ".join(activities) if activities else "General Development"
            
        chrono_lines.append(f"SESSION: [{start_str} - {end_str}] ({duration_str})")
        chrono_lines.append(f"TIME CONTEXT: {time_context}")
        chrono_lines.append(f"PROJECT: {proj_name}")
        chrono_lines.append(f"DETECTED ACTIVITY: {activity_str}")
        
        # Git commits
        if s.commits:
            chrono_lines.append("GIT COMMITS:")
            for c in s.commits:
                msg = c.get("cleaned_message") or c.get("message") or ""
                chrono_lines.append(f"  - {msg}")
                
        # Commands (filter noise, include exit codes for failed ones)
        cmds = [cmd for cmd in s.commands if not _is_noise_command(cmd.command)]
        if cmds:
            chrono_lines.append("COMMANDS:")
            for cmd in cmds[:15]:
                exit_str = f" (Exit Code {cmd.exit_code})" if cmd.exit_code != 0 else ""
                chrono_lines.append(f"  - {cmd.command}{exit_str}")
                
        chrono_lines.append("")
        
        # Check for gap between this session and the next one
        if idx < len(sessions) - 1:
            next_s = sessions[idx + 1]
            gap_seconds = next_s.start_time - s.end_time
            if gap_seconds >= 600: # 10 minutes or more
                gap_hours = gap_seconds // 3600
                gap_mins = (gap_seconds % 3600) // 60
                gap_str = []
                if gap_hours > 0:
                    gap_str.append(f"{gap_hours} hour{'s' if gap_hours > 1 else ''}")
                if gap_mins > 0:
                    gap_str.append(f"{gap_mins} minute{'s' if gap_mins > 1 else ''}")
                gap_display = " and ".join(gap_str) if gap_str else f"{gap_seconds} seconds"
                
                s_end_str = dt_end.strftime("%H:%M")
                next_start_str = datetime.fromtimestamp(next_s.start_time).strftime("%H:%M")
                
                # Classify break type based on time
                end_hour = dt_end.hour
                if 11 <= end_hour < 14:
                    break_context = "Lunch Time Break"
                elif 18 <= end_hour < 21:
                    break_context = "Dinner Time Break"
                elif 23 <= end_hour or end_hour < 5:
                    break_context = "Midnight Sleep/AFK"
                else:
                    break_context = "General Break / Away From Keyboard"
                    
                chrono_lines.append(f"[INFERRED BREAK]: Gap of {gap_display} (from {s_end_str} to {next_start_str})")
                chrono_lines.append(f"BREAK CONTEXT: {break_context}")
                chrono_lines.append("")
                
    chrono_blocks = "\n".join(chrono_lines)
    
    prompt = (
        "You are the master bard and core storyteller for termstory, a developer memory engine. "
        "Your task is to transform raw command telemetry, git commits, and inferred time gaps into a beautiful, personalized, and slightly humorous 'Story of You' for a single developer day.\n\n"
        
        "YOUR CORE RULES:\n"
        "1. DO NOT GENERATE HEADER: Start directly with the Acts (like ACT I). The header (with the DYNAMIC HANDLE, date, stats, and ASCII avatar) is generated by python and must NOT be output by you.\n"
        "2. USE SECOND-PERSON: Always address the developer as 'You' (e.g., 'You stepped into the arena at...', 'You woke up and immediately chose violence against bugs').\n"
        "3. ACT-BY-ACT CHRONOLOGY: Group the sessions into chronological Acts (e.g., '🌅 ACT I: THE OPENING ARCHITECTURE [09:15 - 12:02]', '🌋 ACT II: THE TRENCH WAR LOOP [13:45 - 16:10]'). For each act, show the exact time range [HH:MM - HH:MM] and explicitly comment on the timing (e.g., call out if they are coding at 3 AM in 'Late-Night/Goblin Mode', or early morning, or afternoon fatigue).\n"
        "4. ACT DETAIL STRUCTURE & INFER HUMANITY: Under each Act, output exactly three bullet points:\n"
        "   - ├─ 📂 Project: <Project Name> (<git branch name if checkout/branch command or commits tell you, or main branch by default>)\n"
        "   - ├─ ⌨️  Action:  <Concise description of key files modified or commands run, e.g. 'Modified parser.py and session.py sequentially.'>\n"
        "     (Note: If there is a repetitive pattern of failing commands, change this bullet to: ├─ 🔄 Pattern: High-Frustration Loop Detected. You executed a pattern of: `command` ──► `test` (Exit Code 1). This diagnostic loop repeated N times over X minutes.)\n"
        "   - └─ 🧠 Story:   <A witty 2-3 sentence narrative describing what was done, why/how, the technical struggle, and the outcome. Keep it dense and engineering-authentic.>\n"
        "5. INTEGRATE BREAKS & INFER HUMANITY: Between Acts, use the BREAK CONTEXT markers to write fun, perceptive descriptions of what they did in between sessions. For example, if it's a Lunch Time Break, infer that they went for lunch, grabbed coffee, or stared blankly at a wall. If they were stuck in a loop of failing tests/compiles, highlight their stubborn determination with dry developer humor.\n"
        "6. NO CORPORATE SLOP: Absolutely no generic, robotic wrap-ups or corporate management phrases like 'All in all, it was a productive day!'. Keep it authentic, slightly sarcastic, and developer-to-developer.\n"
        "7. THE VERDICT CARD: End the chronicle with a high-density, double-equals ASCII box summarizing the day's highlights (e.g. total active hours, test streaks broken, secrets protected) matching the exact format below:\n"
        "   ====================================================================\n"
        "   [CHRONICLE END] <1-2 sentences summarizing the day's engineering combat>\n"
        "   ====================================================================\n"
        "8. NO MARKDOWN OR EXTRA WRAPPER: Absolutely do not output code blocks (like ```text or ```), and do not add any introductory or trailing filler text. Just output the clean, terminal-formatted story.\n\n"
        
        "REFERENCE EXAMPLE TO EMULATE:\n"
        f"{example_text}\n\n"
        
        "Input Data Payload:\n"
        f"USER_HANDLE: {github_username}\n"
        f"DATE: {session_date}\n"
        "CHRONO_BLOCKS:\n"
        f"{chrono_blocks}\n\n"
        "Output format: Return ONLY the raw daily chronicle story."
    )
    return prompt



def generate_daily_chronicle(
    github_username: str,
    session_date: str,
    sessions: List,
    projects: List,
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Scrub inputs, build prompt, and call LLM chat completions endpoint to generate the Daily Chronicle."""
    if provider == "disabled" or not sessions:
        return None
        
    prompt = generate_daily_chronicle_prompt(github_username, session_date, sessions, projects)
    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=2000, timeout=effective_timeout
    )


def generate_wrapped_summary(
    github_username: str,
    focus_hours: float,
    total_sessions: int,
    additions: int,
    deletions: int,
    merged_prs: int,
    branch_names_list: str,
    cleaned_commits_block: str,
    project_distributions_percentages: str,
    top_editor_buffers_with_durations: str,
    amends_count: int,
    midnight_percentage: float,
    success_rate: float,
    failed_builds: int,
    passed_builds: int,
    tool_keywords_list: str,
    redacted_secrets_count: int,
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Query LLM to generate the wittiest and sharpest Behavioral Audit & Verdict for TermStory Wrapped."""
    if provider == "disabled":
        return None
        
    prompt = (
        "You are the elite biographer for TermStory Wrapped. Your task is to analyze a developer's 30-day aggregated terminal metrics, local/pushed git commits, editor buffers, tool loops, and shell habits, and write the final \"BEHAVIORAL AUDIT\" text block.\n\n"
        "CRITICAL TEXT GENERATION RULES:\n"
        "1. VOICE: Write in a sharp, witty, highly perceptive second-person (\"You\") voice. Blend authentic software engineering terminology with dry developer humor.\n"
        "2. NO MARKDOWN: Absolutely do not include markdown headers, bold syntax (**), or outside quotes. Return only the raw text intended to sit inside the console panel box lines.\n"
        "3. CONSTRAINTS: Keep each section description under 4 lines of console length (approx 70 characters per line) to avoid text-wrapping breaking the terminal box alignment.\n\n"
        "INFERRED MULTI-DIMENSIONAL PATTERNS TO AUDIT:\n"
        "- IMPOSTER SYNDROME INDEX: Deduce this from the frequency of `git commit --amend`. Frame it as you gaslighting your reviewers into thinking you write perfect code on the first try.\n"
        "- MIDNIGHT OIL FACTOR: Evaluate late-night timestamps and key ratios. Call out sleep hygiene, dark mode addiction, and caffeine habits.\n"
        "- THE ARCHITECTURAL PURGE: Highlight if they achieved a net-negative line change (deleting more than adding), framing it as a heroic war against codebase bloat.\n"
        "- THE TRENCH WAR DIAGNOSTIC: Look at the exit code success-to-failure ratio. Call out their stubbornness or debugging grit when fighting repetitive compiler/test errors.\n"
        "- SIDE-QUEST DISTRACTION: Compare time spent on core humanized projects vs. time lost tweaking system configurations, dotfiles, or automation bots.\n"
        "- SANITIZER INTERCEPTS: Mention if any API keys/credentials were redacted.\n\n"
        "Input Context Telemetry:\n"
        "────────────────────────────────────────────────────────────────────\n"
        "OPERATOR DATA:\n"
        f"• Username: @{github_username}\n"
        f"• Total Monthly Focus: {focus_hours} hours across {total_sessions} sessions\n\n"
        "VCS & COMMIT INTENT LOGS:\n"
        f"• Git Diff Stats: +{additions} additions, -{deletions} deletions\n"
        f"• Merged PRs: {merged_prs} | Active Branches Tracked: {branch_names_list}\n"
        "• Raw Local Commit Messages (Cleaned/Stripped):\n"
        f"{cleaned_commits_block}\n\n"
        "EDITOR BUFFERS & TRACKED WORKSPACES:\n"
        f"• Active Project Distributions: {project_distributions_percentages}\n"
        f"• Top Edited File Buffers: {top_editor_buffers_with_durations}\n\n"
        "SHELL BEHAVIOR & COMPILATION TELEMETRY:\n"
        f"• Amends Count: {amends_count} executions of `git commit --amend`\n"
        f"• Late Night Key Ratio: {midnight_percentage}% of strokes between 11PM-5AM\n"
        f"• Terminal Success Rate: {success_rate}% ({failed_builds} failed exit codes, {passed_builds} successful)\n"
        f"• Notable Toolchain Keywords Detected: {tool_keywords_list}\n"
        f"• Security Intercepts: {redacted_secrets_count} local credential redactions\n"
        "────────────────────────────────────────────────────────────────────\n\n"
        "Output Format Requirements:\n"
        "You must return EXACTLY the following structure (with emojis and headings). Do NOT add markdown formatting. Each description must be 2 to 3 lines long:\n\n"
        "🧠 IMPOSTER SYNDROME INDEX: <calculated_percentage>%\n"
        "   <Wit/roast description>\n\n"
        "🌌 THE MIDNIGHT OIL FACTOR: <Calculated level (e.g. High/Low/Extreme)>\n"
        "   <Wit/roast description>\n\n"
        "🩸 THE TRENCH WAR DIAGNOSTIC\n"
        "   <Wit/roast description>\n\n"
        "🛡️ THE ARCHITECTURAL PURGE\n"
        "   <Wit/roast description>\n\n"
        "🤖 SIDE-QUEST DISTRACTION\n"
        "   <Wit/roast description>\n\n"
        "🔒 SANITIZER INTERCEPTS: <Status, e.g. Clean or Redacted>\n"
        "   <Wit/roast description>\n\n"
        "[VERDICT] <2-3 lines summarizing the month's performance and developers' traits>\n\n"
        "Output format: Return ONLY the raw, polished console text lines matching the segments of the behavioral audit panel. No introductory conversational text, no markdown styling, no explanations."
    )
    
    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=1500, timeout=effective_timeout
    )


def translate_git_anger(
    commit_data: List[Dict],
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Query LLM to translate git commits and preceding terminal errors into raw emotions and developer roasts."""
    if provider == "disabled" or not commit_data:
        return None

    formatted_blocks = []
    for item in commit_data:
        commit_hash = item.get("hash", "unknown")[:7]
        commit_msg = item.get("message", "")
        errors = item.get("preceding_errors", [])
        error_lines = "\n".join(f"  - [FAIL] {err}" for err in errors) if errors else "  - (No preceding terminal errors detected)"
        formatted_blocks.append(
            f"Commit: {commit_msg} ({commit_hash})\n"
            f"Preceding Errors:\n{error_lines}"
        )
    
    payload = "\n\n".join(formatted_blocks)

    prompt = (
        "You are the 'Git-Blame Anger Translator', a developer memory engine sub-module designed to read between the lines.\n"
        "Analyze the following Git commits and the terminal shell command errors that immediately preceded them.\n"
        "Your task is to translate these technical activities into the raw, unfiltered emotional state of the developer at the time of commit.\n\n"
        "YOUR CORE GOALS:\n"
        "1. Identify the predominant emotion (e.g. Rage, Despair, Exhaustion, Triumph, Sarcasm, Confusion) for each commit.\n"
        "2. Write a highly humorous, sarcastic, developer-authentic translation/roast explaining the transition from the failing commands to the commit message.\n"
        "3. Output a clean, CLI-friendly list/table using ASCII symbols (like 😡, 😩, 🏆, ├─, └─). No markdown formatting, no code blocks, no conversational preamble/filler.\n\n"
        "EMOTIONAL ANALYSIS PAYLOAD:\n"
        f"{payload}\n\n"
        "Output format: Return ONLY the formatted emotional translations. Keep it dense, punchy, and highly developer-focused."
    )

    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=1500, timeout=effective_timeout
    )


def predict_bugs_from_sessions(
    sessions_data: List[Dict],
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Query LLM to predict potential bugs based on late-night chaotic session command telemetry."""
    if provider == "disabled" or not sessions_data:
        return None
        
    session_blocks = []
    for s in sessions_data:
        hour_str = f"{s['hour']}:00"
        failed_cmds = s.get("failed_commands", [])
        failed_lines = "\n".join(f"  - [FAIL] {cmd}" for cmd in failed_cmds) if failed_cmds else "  - (No command execution errors)"
        all_cmds = "\n".join(f"  - {cmd}" for cmd in s.get("commands", [])[:10])
        commits = "\n".join(f"  - {c}" for c in s.get("commits", []))
        session_blocks.append(
            f"Session ID: {s['session_id']} | Project: {s['project_name']} | Time: {hour_str}\n"
            f"Failed Commands:\n{failed_lines}\n"
            f"Recent Commands Executed:\n{all_cmds}\n"
            f"Git Commits: {commits}"
        )
    
    payload = "\n\n".join(session_blocks)
    
    prompt = (
        "You are the 'Predictive Bug Fortune Teller', a developer memory engine sub-module designed to foresee bugs.\n"
        "Analyze the following late-night chaotic work sessions. These sessions were conducted in the middle of the night, "
        "often with failing commands, repeating test cycles, or desperate commit amends.\n"
        "Your task is to predict the potential bugs/issues the developer likely introduced during these sessions.\n\n"
        "YOUR CORE GOALS:\n"
        "1. Predict 1-2 highly plausible, funny, yet technically accurate bug types (e.g. race conditions, off-by-one errors, unresolved merge markers, config path typos, logic reversals) for each chaotic session.\n"
        "2. Formulate your predictions as a warning in a sarcastic, developer-to-developer style.\n"
        "3. Output a clean, high-density, CLI-styled console block using ASCII symbols. No markdown formatting, no code blocks, no conversational preamble/filler.\n\n"
        "Telemetry Payload:\n"
        f"{payload}\n\n"
        "Output format: Return ONLY the raw bug prediction logs."
    )
    
    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=1500, timeout=effective_timeout
    )


def generate_rpg_bio(
    rpg_class: str,
    commands: List[str],
    api_key: str,
    api_base_url: str,
    model_name: str,
    provider: str,
    timeout: Optional[float] = None
) -> Optional[str]:
    """Query LLM to generate a brief developer biography and customized ASCII crest based on the assigned RPG class."""
    if provider == "disabled":
        return None

    # Sanitise commands
    sanitized_cmds, is_blacklisted = sanitize_session_commands(commands)
    if is_blacklisted:
        commands_summary = "Security/Authentication Operations"
    else:
        commands_summary = "\n".join(f"  - {cmd}" for cmd in sanitized_cmds[:15])

    prompt = (
        "You are the 'TermStory RPG Class Biographer', a developer memory engine sub-module.\n"
        f"The developer has been assigned the RPG Class: '{rpg_class}' based on their command pattern.\n\n"
        "Here are some recent commands they ran:\n"
        f"{commands_summary}\n\n"
        "YOUR CORE GOALS:\n"
        "1. Write a witty, humorous, brief biography (3-4 sentences) describing their quest as a developer of this class.\n"
        "2. Generate a custom, small ASCII-art crest or badge (under 8 lines, under 40 chars wide) representing their class.\n"
        "3. Output a clean, high-density, CLI-styled console block using ASCII symbols. Do not use markdown formatting, bold text, or conversational preamble.\n\n"
        "Output format: Return ONLY the raw ASCII crest and biography."
    )

    from termstory.config import load_config
    config = load_config()
    effective_timeout = timeout if timeout is not None else config.get("request_timeout_seconds", 30.0)
    return _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=1500, timeout=effective_timeout
    )



