# tests/ - Test Suite

This directory contains the Termstory pytest suite. Follow these notes when
adding or changing tests so new coverage matches the existing patterns.

## Running tests

- Full suite used in CI:
  `pytest --timeout=60 tests/ --ignore=tests/stress`
- Focused TUI example:
  `pytest tests/test_tui.py -k "onboarding" --timeout=30 -v`
- Stress tests live under `tests/stress/` and are excluded from the normal CI
  command.

## Async and Textual patterns

- Use `@pytest.mark.asyncio` for async Textual tests.
- Use `async with app.run_test() as pilot:` for Textual app tests.
- Call `await pilot.pause()` after actions that need Textual's message loop to
  process callbacks or screen updates.
- For modal dismiss tests that would hang under Textual 8.x, install the
  workaround before entering `run_test()`:

  ```python
  from termstory.testing import install_sync_dismiss_workaround

  install_sync_dismiss_workaround(monkeypatch)
  ```

## Fixtures and isolation

- Prefer `tempfile.TemporaryDirectory()` or `tmp_path` for filesystem isolation.
- Use `Database(":memory:")` when a test only needs an in-memory SQLite database.
- Use files under `tests/fixtures/` for reusable sample input data.
- Keep `tests/stress/` for slow, adversarial, or timeout-oriented coverage.

## Mocking external behavior

- Mock AI providers rather than making network calls. Existing tests patch
  request helpers or use patterns such as:

  ```python
  monkeypatch.setattr("termstory.tui.generate_ai_summary", mock_fn)
  ```

- Keep provider responses small and deterministic.
- Do not require real API keys, shell history, external services, or a user's
  local terminal state.

## Database and CLI conventions

- Create `Database` instances in temporary locations and call `init_db()` before
  saving data.
- Assert on persisted rows or returned models, not on private implementation
  details unless the test is explicitly about a migration or cache behavior.
- CLI tests should use pytest helpers such as `tmp_path`, `monkeypatch`, and
  Typer test utilities already present in nearby tests.

## Style

- Keep tests direct and readable. The suite favors explicit setup over shared
  fixtures when the setup is small.
- Match existing import style in the neighboring test file.
- Avoid adding sleeps unless the Textual pilot needs a message-loop tick; prefer
  `await pilot.pause()` for UI synchronization.
