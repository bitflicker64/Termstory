from termstory.sanitizer import (
    should_blacklist_command,
    redact_command,
    sanitize_session_commands
)

def test_blacklist_commands():
    assert should_blacklist_command("vault read secret/data") is True
    assert should_blacklist_command("aws configure set aws_access_key_id 123") is True
    assert should_blacklist_command("gh auth login --with-token") is True
    assert should_blacklist_command("kubectl create secret generic db-user-pass --from-literal=username=dev") is True
    
    # Safe commands
    assert should_blacklist_command("git status") is False
    assert should_blacklist_command("docker compose up") is False

def test_redact_environment_variables():
    # Exports
    assert redact_command("export DATABASE_URL=mysql://root:pass@localhost/db") == "export DATABASE_URL=[REDACTED_URI_CREDENTIALS]"
    assert redact_command("export AWS_SECRET_ACCESS_KEY='secret key'") == "export AWS_SECRET_ACCESS_KEY=[REDACTED]"
    assert redact_command('export PORT="8080"') == 'export PORT=[REDACTED]'
    
    # Inline high-risk env vars
    assert redact_command("DB_PASSWORD=123 python3 app.py") == "DB_PASSWORD=[REDACTED] python3 app.py"
    assert redact_command("API_TOKEN='token' npm run start") == "API_TOKEN=[REDACTED] npm run start"
    
    # Safe assignments (should not redact)
    assert redact_command("i=0") == "i=0"
    assert redact_command("name=app") == "name=app"

def test_redact_password_flags():
    assert redact_command("mysql -u root -pPassword123") == "mysql -u root -p[REDACTED]"
    assert redact_command("pg_dump -h localhost -U postgres --password=my-pass db") == "pg_dump -h localhost -U postgres --password=[REDACTED] db"
    assert redact_command("curl -H 'Authorization: Bearer secret-token' http://api") == "curl -H 'Authorization: Bearer [REDACTED_TOKEN]' http://[REDACTED_HOST]"
    assert redact_command("cli --token secret-token-value") == "cli --token [REDACTED]"

def test_redact_ips_and_fqdns():
    # IPs
    assert redact_command("ssh admin@192.168.1.105") == "ssh admin@[REDACTED_IP]"
    
    # FQDNs
    assert redact_command("curl https://api.internal.domain.local/v1/users") == "curl https://[REDACTED_HOST]/v1/users"
    assert redact_command("ping dev-server.local") == "ping [REDACTED_HOST]"
    
    # Excluded files (should not be redacted)
    assert redact_command("python3 main.py") == "python3 main.py"
    assert redact_command("cat config.json") == "cat config.json"
    assert redact_command("vim README.md") == "vim README.md"

def test_redact_secrets_patterns():
    # AWS Key
    assert redact_command("aws s3 ls --access-key AKIAIOSFODNN7EXAMPLE") == "aws s3 ls --access-key [REDACTED_AWS_KEY]"
    # Slack token
    assert redact_command("curl -d 'token=xoxb-123456789012-abcdefghijklmnopqrstuvwx'") == "curl -d 'token=[REDACTED_SLACK_TOKEN]'"

def test_sanitize_session_commands():
    # Normal session
    cmds = ["cd project", "git status", "python3 main.py"]
    sanitized, is_blacklisted = sanitize_session_commands(cmds)
    assert is_blacklisted is False
    assert sanitized == ["cd project", "git status", "python3 main.py"]
    
    # Sensitive session
    cmds = ["export DB_PASS=123", "python3 main.py"]
    sanitized, is_blacklisted = sanitize_session_commands(cmds)
    assert is_blacklisted is False
    assert sanitized == ["export DB_PASS=[REDACTED]", "python3 main.py"]
    
    # Blacklisted session
    cmds = ["cd project", "vault read secret"]
    sanitized, is_blacklisted = sanitize_session_commands(cmds)
    assert is_blacklisted is True
    assert sanitized is None

import os
from unittest.mock import patch

def test_custom_termstoryignore(tmp_path):
    import termstory.sanitizer as sanitizer
    
    ignore_file = tmp_path / ".termstoryignore"
    ignore_file.write_text("my_custom_secret_pattern\nanother_secret")
    
    with patch("termstory.sanitizer.os.path.expanduser") as mock_expanduser:
        # Mock expanduser to return our temp ignore file for the first path only
        mock_expanduser.side_effect = lambda x: str(ignore_file) if x == '~/.termstoryignore' else str(tmp_path / "nonexistent")
        
        # Clear existing patterns and reload
        original_patterns = sanitizer.CUSTOM_REDACTION_PATTERNS
        sanitizer.CUSTOM_REDACTION_PATTERNS = tuple()
        
        sanitizer.CUSTOM_REDACTION_PATTERNS = sanitizer.load_custom_ignore_rules()
        
        assert len(sanitizer.CUSTOM_REDACTION_PATTERNS) == 2
        
        # Test if it redacts
        cmd = "echo my_custom_secret_pattern is here"
        redacted = sanitizer.redact_command(cmd)
        assert redacted == "echo [REDACTED_CUSTOM] is here"
        
        # Restore original patterns
        sanitizer.CUSTOM_REDACTION_PATTERNS = original_patterns

def test_high_entropy_heuristic():
    # Length >= 24, high entropy
    # e.g., "aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3wX4yZ5"
    high_entropy_token = "aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3w"
    
    # Check if length is >= 24
    assert len(high_entropy_token) >= 24
    
    # Should be redacted
    cmd = f"echo {high_entropy_token}"
    redacted = redact_command(cmd)
    
    assert "[REDACTED_ENTROPY]" in redacted
    assert high_entropy_token not in redacted
    
    # Low entropy string of length >= 24 should NOT be redacted
    low_entropy_token = "aaaaaaaaaaaaaaaaaaaaaaaa"
    assert len(low_entropy_token) >= 24
    cmd2 = f"echo {low_entropy_token}"
    redacted2 = redact_command(cmd2)
    assert "[REDACTED_ENTROPY]" not in redacted2
    assert low_entropy_token in redacted2

def test_redact_specific_api_keys():
    # Google API Key literal
    assert redact_command("curl AIzaSyD-1234567890_abcdefghijklmnopqrstuv") == "curl [REDACTED_GOOGLE_KEY]"
    # Anthropic API Key literal
    assert redact_command("sk-ant-sid01-1234567890abcdef1234567890abcdef12345678") == "[REDACTED_ANTHROPIC_KEY]"
    # OpenAI API Key (using sk-proj- or longer token)
    assert redact_command("sk-proj-1234567890abcdef1234567890abcdef1234567890abcdef") == "[REDACTED_OPENAI_KEY]"
    # DeepSeek API Key literal (sk- followed by 32 chars)
    assert redact_command("sk-1234567890abcdef1234567890abcdef") == "[REDACTED_DEEPSEEK_KEY]"
    
    # Flags and Env vars
    assert redact_command("my_script.py --google-api-key mysecretkey") == "my_script.py --google-api-key [REDACTED]"
    assert redact_command("OPENAI_API_KEY=mysecretkey python3 app.py") == "OPENAI_API_KEY=[REDACTED] python3 app.py"
    assert redact_command("export DEEPSEEK_API_KEY=something") == "export DEEPSEEK_API_KEY=[REDACTED]"


def test_blacklist_sk_false_positives():
    # A safe filename with hyphens/underscores starting with sk- should NOT match blacklist
    assert should_blacklist_command("ls sk-production-deployment-config") is False
    assert should_blacklist_command("cat sk-dataset-version-2-csv") is False
    
    # But a real sk- key prefix should still be blacklisted
    assert should_blacklist_command("export KEY=sk-proj-1234567890abcdef1234567890abcdef1234567890abcdef") is True


def test_redact_command_handles_commit_message_form():
    """Deterministic token-prefix / natural-language password patterns must
    be redacted by redact_command() itself — they are NOT gated by the
    BLACKLIST_PATTERNS path (which only runs for full commands)."""
    from termstory.sanitizer import redact_command
    # GitHub PAT-style token
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in redact_command(
        "fix token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    # Separator-form password in commit message
    assert "ChroniclePassword123!" not in redact_command(
        "rotate password: 而***d!"
    )
    # Whitespace-form password in commit message
    assert "ChroniclePassword123!" not in redact_command(
        "rotate password ***d!"
    )


def test_custom_redaction_patterns_race_condition():
    import sys
    import threading
    
    if "termstory.sanitizer" in sys.modules:
        del sys.modules["termstory.sanitizer"]
        
    errors = []
    def import_sanitizer():
        try:
            import termstory.sanitizer
        except Exception as e:
            errors.append(e)
        
    threads = []
    for _ in range(20):
        t = threading.Thread(target=import_sanitizer)
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    assert not errors, f"Exceptions occurred in threads: {errors}"
    import termstory.sanitizer
    assert isinstance(termstory.sanitizer.CUSTOM_REDACTION_PATTERNS, tuple)
