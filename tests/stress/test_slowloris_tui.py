import pytest
import os
import time
import socket
import threading
import json
import asyncio
from unittest.mock import patch, MagicMock
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
import sqlite3
from termstory.database import Database
from termstory.models import Session, Project, Command
from termstory.tui import TermStoryWorkspace

# --- Slowloris Server Mock ---

class SlowlorisHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        # Trickle data at 1 byte per 60 seconds (or similar very slow rate)
        data = json.dumps({
            "choices": [
                {
                    "message": {
                        "content": "This is a slowloris response."
                    }
                }
            ]
        }).encode('utf-8')

        try:
            for byte in data:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                # Simulate the slow trickling
                time.sleep(1.0)
        except (ConnectionResetError, BrokenPipeError):
            pass

def find_free_port():
    import socket
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def start_slow_server(port):
    server = HTTPServer(('localhost', port), SlowlorisHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

# --- The Test ---

@pytest.mark.asyncio
async def test_slowloris_tarpit(tmp_path, monkeypatch):
    port = find_free_port()
    server = start_slow_server(port)
    time.sleep(0.5)


    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.init_db()

    # Create dummy data
    p = Project(id=1, name="dummy_project", path="/dummy", first_seen=1000, last_seen=1100, session_count=100, total_time=1000)
    
    # Create thousands of sessions to simulate a flooded @work queue when down arrow is held
    sessions = []
    commands = []
    for i in range(100):
        s = Session(id=i, start_time=1000 + i, end_time=1000 + i + 10, duration_seconds=10, project_id=1)
        c = Command(id=i, timestamp=1000 + i, command="echo test", session_id=i)
        c.project_id = 1
        s.commands = [c]
        sessions.append(s)
        commands.append(c)

    # Use database's save_data to persist them correctly. Order: projects, sessions, commands
    db.save_data([p], sessions, commands)
    
    config = {
        "active_provider": "openai",
        "providers": {
            "openai": {
                "api_key": "test_key",
                "api_base_url": f"http://localhost:{port}",
            }
        },
        "request_timeout_seconds": 1.0  # Set a short timeout for the test
    }
    
    # Mock config loader
    monkeypatch.setattr("termstory.config.load_config", lambda: config)

    # Reset circuit breaker
    import termstory.ai
    termstory.ai._circuit_breaker_failures = 0
    termstory.ai._circuit_breaker_open_until = 0.0

    # TermStoryWorkspace expects the db, optional days_limit, and optional config_override
    app = TermStoryWorkspace(db=db, days_limit=30, config_override=config)
    # We must explicitly populate sessions and projects so they are ready
    app.sessions = sessions
    app.projects = [p]
    
    try:
        # Start the app in a background task
        async with app.run_test() as pilot:
            # Simulate holding down the down-arrow key at 60 FPS
            # By querying the Tree and sending cursor down events or just calling the selection event directly
            tree = app.query_one("#history-navigator")
            
            start_time = time.time()
            for i in range(50):
                # We trigger the generation of single session stories rapidly
                app.generate_single_session_story(sessions[i])
                # Sleep slightly to allow the scheduler to queue them
                await asyncio.sleep(0.01)
            
            # Wait a bit for the workers to process
            await asyncio.sleep(2.0)

            # Let's check if the main thread is blocked. We can verify by checking if the TUI responds to another action.
            assert time.time() - start_time < 10.0, "Main thread was blocked by slowloris!"
            
            # Since we use exclusive=True (for some workers) and thread=True, the worker pool could be exhausted.
            # But our secondary wall-clock threading timeout `worker_thread.join(timeout + 1.0)` in ai.py should protect us.
            
            # Wait for circuit breaker to trip
            await asyncio.sleep(1.0)
            import termstory.ai
            assert termstory.ai._circuit_breaker_failures > 0, "Circuit breaker should have registered failures from timeouts"
            
    finally:
        server.shutdown()
        server.server_close()
