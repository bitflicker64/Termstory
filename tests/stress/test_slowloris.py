import asyncio
import time
import socket
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
from unittest.mock import patch, MagicMock
from termstory.ai import _send_llm_request

# We need a Slowloris-style server that trickles data.

class SlowlorisHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        # Trickle data at 1 byte per second (or minute, but let's do second to keep the test reasonable)
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
                time.sleep(1.0) # 1 byte per second
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

def test_slowloris_tarpit():
    port = find_free_port()
    server = start_slow_server(port)

    
    # Let the server start
    time.sleep(0.5)
    
    start_time = time.time()
    
    # We will simulate a call that hits the slowloris server with a 2 second timeout
    try:
        response = _send_llm_request(
            prompt="Hello",
            api_key="test",
            api_base_url=f"http://localhost:{port}",
            model_name="test",
            provider="openai",
            max_tokens=100,
            timeout=2.0 # Wait for 2 seconds
        )
        print("Response:", response)
    except urllib.error.URLError as e:
        print("Caught expected URLError (timeout):", e)
    except TimeoutError as e:
        print("Caught TimeoutError:", e)
    except socket.timeout as e:
        print("Caught socket.timeout:", e)
    except Exception as e:
        print("Caught unexpected exception:", e)
    finally:
        server.shutdown()
        server.server_close()

    end_time = time.time()
    duration = end_time - start_time
    
    print(f"Request took {duration:.2f} seconds.")

if __name__ == '__main__':
    test_slowloris_tarpit()
