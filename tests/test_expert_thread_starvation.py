import pytest
import asyncio
import time
from unittest.mock import patch, MagicMock
from termstory.tui import TermStoryWorkspace
from textual.app import App
from termstory.models import Session

from termstory.database import Database

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_thread_starvation(tmp_path):
    db_file = tmp_path / "test.db"
    db = Database(str(db_file))
    db.init_db()
    app = TermStoryWorkspace(db=db)

    async with app.run_test() as pilot:
        # spawn 50 concurrent @work tasks
        sessions = [Session(id=i, start_time=i, end_time=i+10, duration_seconds=10, project_id=1, commands=[]) for i in range(50)]

        start = time.time()
        for i, s in enumerate(sessions):
            # This runs in thread pool
            app.generate_single_session_story(s)

        # Give some time
        await asyncio.sleep(2)
        end = time.time()
        print(f"Elapsed: {end - start:.2f}s")
        assert end - start < 5, "Thread starvation detected, blocked main thread"

        # Cancel all in-flight workers BEFORE exiting run_test(). With
        # `exclusive=True` on the @work decorator, only one worker runs at
        # a time and the others wait in a queue — exiting run_test() with
        # pending workers caused CI to hang indefinitely.
        app.workers.cancel_all()
        await pilot.pause()
