import re
import os
import math
from typing import List, Tuple, Optional

# Load custom redaction patterns from .termstoryignore
CUSTOM_REDACTION_PATTERNS = []
def load_custom_ignore_rules():
    global CUSTOM_REDACTION_PATTERNS
    paths = [
        os.path.expanduser('~/.termstoryignore'),
        os.path.expanduser('~/.termstory/.termstoryignore')
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            try:
                                CUSTOM_REDACTION_PATTERNS.append(re.compile(line, re.IGNORECASE))
                            except re.error:
                                pass
            except Exception:
                pass

load_custom_ignore_rules()
# Blacklist patterns - if a command matches any of these, the entire session is dropped from AI
BLACKLIST_PATTERNS = [
    re.compile(r'\bvault\b', re.IGNORECASE),
    re.compile(r'\baws\s+configure\b', re.IGNORECASE),
    re.compile(r'\bgh\s+auth\b', re.IGNORECASE),
    re.compile(r'\bkubectl\s+.*?\bcreate\s+secret\b', re.IGNORECASE),
    # Modern token prefixes
    re.compile(r'\bgithub_pat_[a-zA-Z0-9_]+\b', re.IGNORECASE),
    re.compile(r'\bsk_live_[a-zA-Z0-9_]+\b', re.IGNORECASE),
    re.compile(r'\bnpm_[a-zA-Z0-9]{36}\b', re.IGNORECASE),
    re.compile(r'\bsk-(?:proj-|ant-api03-)?[a-zA-Z0-9_-]{20,}\b', re.IGNORECASE)
]

# Hardcoded redaction patterns
ENV_EXPORT_PATTERN = re.compile(
    r'(export\s+[A-Za-z0-9_]*(?:pass|key|secret|token|auth|cred|pwd|passphrase|url|uri|host|port|user)[A-Za-z0-9_]*=)(?!\[REDACTED)([^\s\'"]+|\'[^\']*\'|"[^"]*")',
    re.IGNORECASE
)
HIGH_RISK_ENV_PATTERN = re.compile(
    r'\b([A-Za-z0-9_]*(?:pass|key|secret|token|auth|cred|pwd|passphrase|url|uri|host|port|user)[A-Za-z0-9_]*)=(?!\[REDACTED)([^\s\'"]+|\'[^\']*\'|"[^"]*")',
    re.IGNORECASE
)

PASSWORD_FLAG_PATTERN = re.compile(
    r'(--password=|\b--password\s+|\b--pass=|\b--pass\s+|--token=|--token\s+|--api-key=|--api-key\s+)(?!\[REDACTED)([^\s\'"]+|\'[^\']*\'|"[^"]*")',
    re.IGNORECASE
)

IP_ADDRESS_PATTERN = re.compile(r'(?<![\d\.])(?:\d{1,3}\.){3}\d{1,3}(?![\d\.])')
IPV6_ADDRESS_PATTERN = re.compile(
    r'\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b|'
    r'\b(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}\b|'
    r'\b[0-9a-fA-F]{1,4}::[0-9a-fA-F]{1,4}\b|'
    r'\b::[0-9a-fA-F]{1,4}\b|\b[0-9a-fA-F]{1,4}::\b|\b::1\b'
)


# Common programming file extensions to exclude from FQDN redaction
FILE_EXTENSIONS = {
    'py', 'json', 'db', 'sh', 'xml', 'yml', 'yaml', 'md', 'txt', 'c', 'cpp',
    'h', 'go', 'java', 'js', 'ts', 'html', 'css', 'sqlite', 'sqlite3', 'rs',
    'toml', 'lock', 'sql', 'cfg', 'ini', 'git', 'egg-info', 'class', 'jar',
    'png', 'jpg', 'jpeg', 'gif', 'svg', 'zip', 'tar', 'gz', 'log', 'out'
}

# Match standard FQDNs (e.g. host.domain.com)
FQDN_PATTERN = re.compile(r'\b([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)\b')

# Match URL hosts: e.g. http://api, https://myhost
URL_HOST_PATTERN = re.compile(r'\b(https?|ftp|ssh)://([a-zA-Z0-9-.]+)\b', re.IGNORECASE)
# Match SSH connection-like hosts: e.g. admin@host
SSH_HOST_PATTERN = re.compile(r'\b([a-zA-Z0-9_.-]+)@([a-zA-Z0-9-.]+)\b')

# Standard secrets patterns (AWS access keys, slack tokens, bearer tokens, etc.)
AWS_KEY_PATTERN = re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')
SLACK_TOKEN_PATTERN = re.compile(r'\bxoxb-[0-9]{11,13}-[a-zA-Z0-9]{24}\b')
BEARER_TOKEN_PATTERN = re.compile(r'\bbearer\s+([a-zA-Z0-9\-._~+/]+=*)\b', re.IGNORECASE)
SSH_PRIVATE_KEY_PATTERN = re.compile(r'-----BEGIN\s+[A-Z ]+\s+PRIVATE\s+KEY-----.*?-----END\s+[A-Z ]+\s+PRIVATE\s+KEY-----', re.DOTALL | re.IGNORECASE)

GOOGLE_API_KEY_PATTERN = re.compile(r'\bAIzaSy[a-zA-Z0-9_-]{30,45}\b')
OPENAI_API_KEY_PATTERN = re.compile(r'\bsk-[a-zA-Z0-9_-]{32,}\b')
ANTHROPIC_API_KEY_PATTERN = re.compile(r'\bsk-ant-[a-zA-Z0-9_-]{40,}\b')
DEEPSEEK_API_KEY_PATTERN = re.compile(r'\bsk-[a-zA-Z0-9_-]{32}\b')

SPECIFIC_API_KEYS_PATTERN = re.compile(
    r'(\b(?:GOOGLE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|DEEPSEEK_API_KEY)\s*=\s*|'
    r'--(?:google-api-key|openai-api-key|anthropic-api-key|deepseek-api-key)=|'
    r'(?:^|\s)--(?:google-api-key|openai-api-key|anthropic-api-key|deepseek-api-key)\s+)'
    r'(?!\[REDACTED)([^\s\'"]+|\'[^\']*\'|"[^"]*")',
    re.IGNORECASE
)

def calculate_entropy(s: str) -> float:
    if not s:
        return 0.0
    entropy = 0.0
    for x in set(s):
        p_x = float(s.count(x)) / len(s)
        entropy += - p_x * math.log2(p_x)
    return entropy

def redact_high_entropy(cmd: str) -> str:
    def replacer(match):
        s = match.group(0)
        # Avoid redacting git commit hashes and normal text by requiring entropy > 4.3
        if calculate_entropy(s) > 4.3:
            return "[REDACTED_ENTROPY]"
        return s
    
    # Match strings of length >= 24 that consist of base64-like characters
    return re.sub(r'\b[a-zA-Z0-9_+/=-]{24,}\b', replacer, cmd)

def should_blacklist_command(cmd: str) -> bool:
    """Check if the command is blacklisted from AI processing"""
    return any(pattern.search(cmd) for pattern in BLACKLIST_PATTERNS)

def redact_command(cmd: str) -> str:
    """Sanitize and redact secrets from a command string"""
    # 1. SSH Private Keys
    cmd = SSH_PRIVATE_KEY_PATTERN.sub('[REDACTED_PRIVATE_KEY]', cmd)
    
    # 2. AWS Keys, Slack Tokens, and AI API Keys
    cmd = AWS_KEY_PATTERN.sub('[REDACTED_AWS_KEY]', cmd)
    cmd = SLACK_TOKEN_PATTERN.sub('[REDACTED_SLACK_TOKEN]', cmd)
    cmd = BEARER_TOKEN_PATTERN.sub('Bearer [REDACTED_TOKEN]', cmd)
    cmd = GOOGLE_API_KEY_PATTERN.sub('[REDACTED_GOOGLE_KEY]', cmd)
    cmd = ANTHROPIC_API_KEY_PATTERN.sub('[REDACTED_ANTHROPIC_KEY]', cmd)
    cmd = DEEPSEEK_API_KEY_PATTERN.sub('[REDACTED_DEEPSEEK_KEY]', cmd)
    cmd = OPENAI_API_KEY_PATTERN.sub('[REDACTED_OPENAI_KEY]', cmd)
    cmd = SPECIFIC_API_KEYS_PATTERN.sub(r'\1[REDACTED]', cmd)
    
    # 3. Environment Variable Exports & High-risk Inline Env Vars
    cmd = ENV_EXPORT_PATTERN.sub(r'\1[REDACTED]', cmd)
    cmd = HIGH_RISK_ENV_PATTERN.sub(r'\1=[REDACTED]', cmd)
    
    # 4. Password and Secret flags
    cmd = PASSWORD_FLAG_PATTERN.sub(r'\1[REDACTED]', cmd)
    if re.search(r'\b(mysql|mysqldump|mongo|influx)\b', cmd, re.IGNORECASE):
        mysql_password_pattern = re.compile(r'((?<!-)-p\s*)(?!\[REDACTED)([^\s\'"]+|\'[^\']*\'|"[^"]*")', re.IGNORECASE)
        cmd = mysql_password_pattern.sub(r'\1[REDACTED]', cmd)
    
    # 5. IP Addresses
    cmd = IP_ADDRESS_PATTERN.sub('[REDACTED_IP]', cmd)
    cmd = IPV6_ADDRESS_PATTERN.sub('[REDACTED_IP]', cmd)
    
    # 6. FQDNs (excluding files)
    def fqdn_replacer(match):
        full_match = match.group(1)
        parts = full_match.split('.')
        ext = parts[-1].lower()
        if ext in FILE_EXTENSIONS:
            # Keep as-is, looks like a source/config file
            return full_match
        return '[REDACTED_HOST]'
        
    cmd = FQDN_PATTERN.sub(fqdn_replacer, cmd)
    
    # 7. URL Hosts
    def url_replacer(match):
        proto = match.group(1)
        host = match.group(2)
        if host == '[REDACTED_IP]' or host == '[REDACTED_HOST]':
            return f"{proto}://{host}"
        parts = host.split('.')
        ext = parts[-1].lower()
        if len(parts) > 1 and ext in FILE_EXTENSIONS:
            return f"{proto}://{host}"
        return f"{proto}://[REDACTED_HOST]"
        
    cmd = URL_HOST_PATTERN.sub(url_replacer, cmd)

    # 8. SSH User@Host
    def ssh_replacer(match):
        user = match.group(1)
        host = match.group(2)
        if host == '[REDACTED_IP]' or host == '[REDACTED_HOST]':
            return f"{user}@{host}"
        parts = host.split('.')
        ext = parts[-1].lower()
        if len(parts) > 1 and ext in FILE_EXTENSIONS:
            return f"{user}@{host}"
        return f"{user}@[REDACTED_HOST]"
        
    cmd = SSH_HOST_PATTERN.sub(ssh_replacer, cmd)
    
    # 9. Entropy-based heuristic for high-entropy strings
    cmd = redact_high_entropy(cmd)
    
    # 10. Custom User Rules
    for pattern in CUSTOM_REDACTION_PATTERNS:
        cmd = pattern.sub('[REDACTED_CUSTOM]', cmd)
        
    return cmd

def sanitize_session_commands(commands: List[str]) -> Tuple[Optional[List[str]], bool]:
    """Sanitize a list of commands for a session.
    Returns (sanitized_commands, is_blacklisted).
    If is_blacklisted is True, sanitized_commands will be None."""
    for cmd in commands:
        if should_blacklist_command(cmd):
            return None, True
            
    sanitized = [redact_command(cmd) for cmd in commands]
    return sanitized, False
