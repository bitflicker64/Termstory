"""
Issue #42 — "Heatmap Pulse & Cyber-Glitch" (Streak Micro-animations)

Tests for the two new animation systems:

  1. **Heatmap pulse** — days with personal-best command counts OR 8+ hour
     continuous sessions pulse magenta↔neon-pink in sync with the existing
     scan-line phase. The "Time logged" text pulses in sync via the
     ``pulse_active`` flag.

  2. **Streak glitch** — when the streak counter hits a new all-time record
     (strict increase from a previously-known best), the displayed streak
     number is replaced by random ASCII for ~0.5s (10 frames × 50ms) before
     settling on the real value.

These tests run WITHOUT a full Textual app mount — they exercise the pure
logic functions (``_compute_highlight_days``, ``generate_heatmap`` with
``highlight_days``, ``calculate_dashboard_stats`` pulse_active flag, and
``_glitch_string``) plus the StatsHeader's glitch state machine via direct
method calls. This avoids the flakiness of timer-based assertions in
Textual's test harness while still covering every acceptance criterion.
"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from termstory.models import Command, Project, Session
from termstory.tui import (
    EIGHT_HOURS_SECONDS,
    GLITCH_TICKS,
    StatsHeader,
    _glitch_string,
    _GLITCH_CHARS,
    _compute_highlight_days,
    calculate_dashboard_stats,
    calculate_streak,
    generate_heatmap,
)
from termstory.database import Database


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _make_session(
    session_id: int,
    start_time: int,
    duration_seconds: int,
    command_count: int = 1,
    project_id: int = 1,
) -> Session:
    """Build a Session with the given parameters and `command_count` commands."""
    cmds = [
        Command(
            timestamp=start_time + i,
            command=f"cmd_{i}",
            session_id=session_id,
            project_id=project_id,
        )
        for i in range(command_count)
    ]
    return Session(
        id=session_id,
        start_time=start_time,
        end_time=start_time + duration_seconds,
        duration_seconds=duration_seconds,
        project_id=project_id,
        commands=cmds,
    )


# ────────────────────────────────────────────────────────────────────────────
# 1. _compute_highlight_days — personal-best detection
# ────────────────────────────────────────────────────────────────────────────


def test_highlight_days_empty_when_no_sessions():
    """No sessions → no highlight days."""
    assert _compute_highlight_days({}, []) == set()


def test_highlight_days_personal_best_command_count():
    """The day with the highest command count is highlight-eligible."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()
    yesterday = today - timedelta(days=1)

    day_counts = {today: 50, yesterday: 10}
    # The sessions list is only used for the 8-hour check, so pass empty.
    highlight = _compute_highlight_days(day_counts, [])
    assert highlight == {today}


def test_highlight_days_ties_for_personal_best_all_highlighted():
    """When multiple days share the max command count, all are highlighted."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    day_counts = {today: 30, yesterday: 30, two_days_ago: 10}
    highlight = _compute_highlight_days(day_counts, [])
    assert highlight == {today, yesterday}


def test_highlight_days_zero_count_days_not_highlighted():
    """A day with 0 commands must never be highlighted even if it's the 'max'."""
    today = datetime.now().date()
    day_counts = {today: 0}
    assert _compute_highlight_days(day_counts, []) == set()


# ────────────────────────────────────────────────────────────────────────────
# 2. _compute_highlight_days — 8+ hour continuous session detection
# ────────────────────────────────────────────────────────────────────────────


def test_highlight_days_eight_hour_session_highlighted():
    """A session with duration_seconds >= 8*3600 highlights its day."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    # 8-hour session today, 0 commands so it won't trigger via personal-best.
    s = _make_session(1, now, EIGHT_HOURS_SECONDS, command_count=0)
    day_counts = {today: 0}
    highlight = _compute_highlight_days(day_counts, [s])
    assert today in highlight


def test_highlight_days_just_under_eight_hours_not_highlighted():
    """A session of 7h59m59s must NOT trigger the 8-hour highlight."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    s = _make_session(1, now, EIGHT_HOURS_SECONDS - 1, command_count=0)
    day_counts = {today: 0}
    highlight = _compute_highlight_days(day_counts, [s])
    assert today not in highlight


def test_highlight_days_eight_hour_exactly_is_highlighted():
    """Exactly 8*3600 seconds = 8 hours → highlight (>= boundary)."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    s = _make_session(1, now, EIGHT_HOURS_SECONDS, command_count=0)
    day_counts = {today: 0}
    highlight = _compute_highlight_days(day_counts, [s])
    assert today in highlight


def test_highlight_days_union_of_personal_best_and_eight_hour():
    """Both criteria contribute to the highlight set (union)."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()
    yesterday = today - timedelta(days=1)

    # Today: personal best (50 commands, no 8h session)
    # Yesterday: 8h session (but only 10 commands, not the max)
    s_today = _make_session(1, now, 600, command_count=50)
    s_yesterday = _make_session(
        2,
        int((now - 86400).timestamp()) if False else now - 86400,
        EIGHT_HOURS_SECONDS,
        command_count=10,
    )

    day_counts = {today: 50, yesterday: 10}
    highlight = _compute_highlight_days(day_counts, [s_today, s_yesterday])
    assert today in highlight      # via personal best
    assert yesterday in highlight  # via 8h session


# ────────────────────────────────────────────────────────────────────────────
# 3. generate_heatmap — magenta/pink pulse on highlighted days
# ────────────────────────────────────────────────────────────────────────────


def test_generate_heatmap_highlighted_day_uses_magenta_on_even_phase():
    """A highlighted day renders with magenta markup on even pulse_phase."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    s = _make_session(1, now, 600, command_count=25)  # 25 commands → █ block
    heatmap = generate_heatmap(
        [s],
        days_limit=1,
        pulse_phase=0,  # even
        highlight_days={today},
    )
    # Even phase → dim magenta (not bold deep_pink).
    assert "magenta" in heatmap
    assert "deep_pink" not in heatmap


def test_generate_heatmap_highlighted_day_uses_neon_pink_on_odd_phase():
    """A highlighted day renders with neon pink (deep_pink) on odd pulse_phase."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    s = _make_session(1, now, 600, command_count=25)
    heatmap = generate_heatmap(
        [s],
        days_limit=1,
        pulse_phase=1,  # odd
        highlight_days={today},
    )
    # Odd phase → bold deep_pink.
    assert "deep_pink" in heatmap


def test_generate_heatmap_non_highlighted_day_keeps_scan_line_behavior():
    """A non-highlighted day must NOT use magenta/deep_pink — it keeps the
    existing green scan-line colouring."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    s = _make_session(1, now, 600, command_count=25)
    heatmap = generate_heatmap(
        [s],
        days_limit=1,
        pulse_phase=0,
        highlight_days=set(),  # no highlights
    )
    assert "magenta" not in heatmap
    assert "deep_pink" not in heatmap
    # Should use the existing green/white scan-line colours.
    assert "green" in heatmap or "white" in heatmap


def test_generate_heatmap_highlighted_day_block_intensity_follows_command_count():
    """A highlighted day with 0 commands shows ░, with 25 commands shows █."""
    now = int(datetime.now().timestamp())
    today = datetime.fromtimestamp(now).date()

    # 0-command highlighted day (e.g. an 8h session with 0 commands tracked).
    s_zero = _make_session(1, now, EIGHT_HOURS_SECONDS, command_count=0)
    hm_zero = generate_heatmap([s_zero], days_limit=1, pulse_phase=0, highlight_days={today})
    assert "░" in hm_zero

    # 25-command highlighted day.
    s_full = _make_session(2, now, 600, command_count=25)
    hm_full = generate_heatmap([s_full], days_limit=1, pulse_phase=0, highlight_days={today})
    assert "█" in hm_full


# ────────────────────────────────────────────────────────────────────────────
# 4. calculate_dashboard_stats — pulse_active flag + highlight_days in output
# ────────────────────────────────────────────────────────────────────────────


def test_calculate_dashboard_stats_includes_highlight_days_and_pulse_active():
    """The returned dict must include 'highlight_days' (set) and 'pulse_active' (bool)."""
    now = int(datetime.now().timestamp())
    s = _make_session(1, now, 600, command_count=25)
    stats = calculate_dashboard_stats([s], [], days_limit=1, pulse_phase=0)
    assert "highlight_days" in stats
    assert "pulse_active" in stats
    assert isinstance(stats["highlight_days"], set)
    assert isinstance(stats["pulse_active"], bool)


def test_calculate_dashboard_stats_pulse_active_flips_with_phase():
    """pulse_active must be True on odd phases (when there are highlights) and False on even."""
    now = int(datetime.now().timestamp())
    s = _make_session(1, now, 600, command_count=25)

    stats_even = calculate_dashboard_stats([s], [], days_limit=1, pulse_phase=0)
    stats_odd = calculate_dashboard_stats([s], [], days_limit=1, pulse_phase=1)

    # There IS a highlight (personal best), so pulse_active should flip.
    assert stats_even["pulse_active"] is False  # even phase
    assert stats_odd["pulse_active"] is True    # odd phase


def test_calculate_dashboard_stats_pulse_active_false_when_no_highlights():
    """When there are no highlight days, pulse_active must always be False."""
    now = int(datetime.now().timestamp())
    # Empty sessions → no highlights.
    stats = calculate_dashboard_stats([], [], days_limit=1, pulse_phase=1)
    assert stats["pulse_active"] is False


# ────────────────────────────────────────────────────────────────────────────
# 5. _glitch_string — the streak glitch text generator
# ────────────────────────────────────────────────────────────────────────────


def test_glitch_string_correct_length():
    """_glitch_string must return a string of exactly the requested length."""
    for length in [1, 2, 3, 5, 10]:
        result = _glitch_string("123", length)
        assert len(result) == length


def test_glitch_string_uses_only_glitch_charset():
    """Every character in the glitch string must come from _GLITCH_CHARS."""
    from termstory.tui import _GLITCH_CHARS
    result = _glitch_string("123", 50)
    for ch in result:
        assert ch in _GLITCH_CHARS, f"Unexpected char {ch!r} in glitch string"


def test_glitch_string_empty_for_zero_length():
    assert _glitch_string("123", 0) == ""


def test_glitch_string_is_random():
    """Two calls should (almost certainly) produce different strings."""
    # 50 chars → probability of collision is astronomically low.
    a = _glitch_string("123", 50)
    b = _glitch_string("123", 50)
    assert a != b


# ────────────────────────────────────────────────────────────────────────────
# 6. StatsHeader — glitch state machine
#
# The glitch is driven by the existing step_heatmap_pulse() interval (0.5s),
# NOT by set_timer. When a new streak record is detected, _glitch_ticks_remaining
# is set to GLITCH_TICKS. Each subsequent update_stats() call decrements it
# and renders glitch text until it reaches 0, at which point the real streak
# number is shown.
# ────────────────────────────────────────────────────────────────────────────


def test_stats_header_no_glitch_on_first_update():
    """The first update_stats call must NOT trigger a glitch (baseline set only)."""
    sh = StatsHeader(id="test-stats")
    sh.update_stats({
        "streak": 5,
        "total_time": "10m",
        "active_days": 1,
        "projects_count": 1,
        "heatmap": "░",
        "last_ingestion": "",
        "vampire_index": 0,
        "rpg_class": "Test",
        "highlight_days": set(),
        "pulse_active": False,
    })
    assert sh._glitch_ticks_remaining == 0
    assert sh._all_time_best_streak == 5
    assert sh._displayed_streak == 5


def test_stats_header_glitch_triggers_on_new_record():
    """A strict streak increase from a known baseline must set _glitch_ticks_remaining > 0."""
    sh = StatsHeader(id="test-stats")

    # First call: establishes baseline at 3.
    sh.update_stats({
        "streak": 3, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    assert sh._glitch_ticks_remaining == 0

    # Second call: new record (3 → 7) → glitch should start.
    sh.update_stats({
        "streak": 7, "total_time": "20m", "active_days": 2,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    assert sh._glitch_ticks_remaining > 0
    assert sh._all_time_best_streak == 7
    assert sh._displayed_streak == 7


def test_stats_header_no_glitch_on_equal_streak():
    """Same streak as before → no glitch."""
    sh = StatsHeader(id="test-stats")
    sh.update_stats({
        "streak": 5, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    sh.update_stats({
        "streak": 5, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    assert sh._glitch_ticks_remaining == 0


def test_stats_header_no_glitch_on_lower_streak():
    """Streak going down → no glitch, baseline stays at the old best."""
    sh = StatsHeader(id="test-stats")
    sh.update_stats({
        "streak": 5, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    sh.update_stats({
        "streak": 3, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    assert sh._glitch_ticks_remaining == 0
    assert sh._all_time_best_streak == 5  # baseline unchanged
    assert sh._displayed_streak == 3      # but displayed value tracks current


def test_stats_header_glitch_settles_after_ticks():
    """After GLITCH_TICKS update_stats calls, the glitch must settle (ticks == 0)."""
    sh = StatsHeader(id="test-stats")

    # Establish baseline.
    sh.update_stats({
        "streak": 3, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    # Trigger glitch with a new record.
    sh.update_stats({
        "streak": 7, "total_time": "20m", "active_days": 2,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    })
    assert sh._glitch_ticks_remaining > 0

    # Simulate GLITCH_TICKS more update_stats calls (from step_heatmap_pulse).
    # Each call should decrement _glitch_ticks_remaining until it reaches 0.
    for _ in range(GLITCH_TICKS):
        sh.update_stats({
            "streak": 7, "total_time": "20m", "active_days": 2,
            "projects_count": 1, "heatmap": "░", "last_ingestion": "",
            "vampire_index": 0, "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
        })
    assert sh._glitch_ticks_remaining == 0


def test_stats_header_glitch_settles_on_correct_tick_not_one_late():
    """PR #368 review fix: the glitch must settle (show the real streak
    number) on the SAME tick that _glitch_ticks_remaining hits 0 — NOT one
    tick later.

    Before the fix, the code rendered glitch text even after decrementing
    to 0, making the glitch last GLITCH_TICKS+1 ticks instead of
    GLITCH_TICKS ticks.
    """
    sh = StatsHeader(id="test-stats")

    # Capture what _render_header sends to self.update().
    rendered_content = []
    sh.update = lambda c: rendered_content.append(str(c))  # type: ignore[assignment]

    stats_base = {
        "total_time": "10m", "active_days": 1, "projects_count": 1,
        "heatmap": "░", "last_ingestion": "", "vampire_index": 0,
        "rpg_class": "T", "highlight_days": set(), "pulse_active": False,
    }

    # 1. Establish baseline at streak=3.
    sh.update_stats({**stats_base, "streak": 3})
    assert sh._glitch_ticks_remaining == 0

    # 2. Trigger glitch with new record (3 → 7).
    rendered_content.clear()
    sh.update_stats({**stats_base, "streak": 7})
    assert sh._glitch_ticks_remaining == GLITCH_TICKS  # 1
    # The record-triggering call must render glitch text.
    assert len(rendered_content) == 1
    glitch_render = rendered_content[0]
    assert "7" not in glitch_render or any(c in glitch_render for c in _GLITCH_CHARS)

    # 3. Next tick (0.5s later): glitch must settle IMMEDIATELY.
    rendered_content.clear()
    sh.update_stats({**stats_base, "streak": 7})
    assert sh._glitch_ticks_remaining == 0
    # The settle tick must render the REAL streak number, not glitch text.
    assert len(rendered_content) == 1
    settle_render = rendered_content[0]
    assert "7" in settle_render  # real streak number visible

# ────────────────────────────────────────────────────────────────────────────
# 7. StatsHeader — pulse_active colours the "Time logged" text
# ────────────────────────────────────────────────────────────────────────────


def test_stats_header_pulse_active_colours_time_logged_pink():
    """When pulse_active=True, the Time logged text must use deep_pink markup."""
    sh = StatsHeader(id="test-stats")
    # Capture what _render_header sends to self.update().
    updated_content = []
    orig_update = sh.update

    def capture_update(content):
        updated_content.append(content)
        # Don't actually call orig_update — we're not mounted.

    sh.update = capture_update  # type: ignore[assignment]

    sh._last_stats = {
        "streak": 5, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T",
        "highlight_days": set(), "pulse_active": True,
    }
    sh._displayed_streak = 5
    sh._render_header()

    assert len(updated_content) == 1
    content = str(updated_content[0])
    assert "deep_pink" in content
    assert "10m" in content


def test_stats_header_pulse_inactive_keeps_default_colour():
    """When pulse_active=False, the Time logged text must NOT use deep_pink."""
    sh = StatsHeader(id="test-stats")
    updated_content = []
    sh.update = lambda c: updated_content.append(c)  # type: ignore[assignment]

    sh._last_stats = {
        "streak": 5, "total_time": "10m", "active_days": 1,
        "projects_count": 1, "heatmap": "░", "last_ingestion": "",
        "vampire_index": 0, "rpg_class": "T",
        "highlight_days": set(), "pulse_active": False,
    }
    sh._displayed_streak = 5
    sh._render_header()

    assert len(updated_content) == 1
    content = str(updated_content[0])
    assert "deep_pink" not in content
    assert "10m" in content


# ────────────────────────────────────────────────────────────────────────────
# 8. Integration — full calculate_dashboard_stats → generate_heatmap pipeline
# ────────────────────────────────────────────────────────────────────────────


def test_full_pipeline_eight_hour_session_pulses_in_heatmap():
    """End-to-end: an 8+ hour session causes its heatmap block to pulse magenta/pink
    across two pulse phases, and pulse_active flips in sync."""
    now = int(datetime.now().timestamp())
    s = _make_session(1, now, EIGHT_HOURS_SECONDS, command_count=5)

    stats_even = calculate_dashboard_stats([s], [], days_limit=1, pulse_phase=0)
    stats_odd = calculate_dashboard_stats([s], [], days_limit=1, pulse_phase=1)

    # Even phase: magenta in heatmap, pulse_active=False
    assert "magenta" in stats_even["heatmap"]
    assert "deep_pink" not in stats_even["heatmap"]
    assert stats_even["pulse_active"] is False

    # Odd phase: deep_pink in heatmap, pulse_active=True
    assert "deep_pink" in stats_odd["heatmap"]
    assert stats_odd["pulse_active"] is True


def test_full_pipeline_personal_best_pulses_in_heatmap():
    """A day with the personal-best command count pulses even without an 8h session."""
    now = int(datetime.now().timestamp())
    s = _make_session(1, now, 600, command_count=25)  # 10-minute session, 25 commands

    stats = calculate_dashboard_stats([s], [], days_limit=1, pulse_phase=1)
    assert "deep_pink" in stats["heatmap"]
    assert stats["pulse_active"] is True
    # The day must be in the highlight set.
    assert len(stats["highlight_days"]) == 1


def test_full_pipeline_quiet_day_does_not_pulse():
    """A day that is neither a personal best nor an 8h session must not pulse.

    To guarantee the day is NOT a personal best, we include a busier day in
    the same window — the quiet day then has no chance of being the max.
    """
    now = int(datetime.now().timestamp())
    # Today: 3 commands, 10 minutes (quiet).
    s_quiet = _make_session(1, now, 600, command_count=3)
    # Yesterday: 50 commands, 10 minutes (will be the personal best).
    s_busy = _make_session(2, now - 86400, 600, command_count=50)

    stats = calculate_dashboard_stats([s_quiet, s_busy], [], days_limit=2, pulse_phase=1)

    # The heatmap for the quiet day (today, the last block) must NOT contain
    # magenta/deep_pink — only the busy yesterday block should pulse.
    # We check the whole heatmap: both magenta and deep_pink should appear
    # (from yesterday), but the quiet today block must use the default colours.
    # The simplest assertion: the highlight set contains only yesterday.
    from datetime import datetime as dt
    today = dt.fromtimestamp(now).date()
    yesterday = today - timedelta(days=1)
    assert today not in stats["highlight_days"]
    assert yesterday in stats["highlight_days"]
