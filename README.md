# TermStory — Your Personal Developer Memory Engine

[![PyPI version](https://img.shields.io/pypi/v/termstory.svg)](https://pypi.org/project/termstory/)
[![CI](https://github.com/bitflicker64/Termstory/actions/workflows/ci.yml/badge.svg)](https://github.com/bitflicker64/Termstory/actions/workflows/ci.yml)
[![Python Versions](https://img.shields.io/pypi/pyversions/termstory.svg)](https://pypi.org/project/termstory/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/bitflicker64/Termstory)

> Parse your shell history. Recover your past. Understand your work.

TermStory turns your terminal history into a searchable, AI-narrated timeline of your development life. It groups shell commands into sessions, correlates Git commits, resolves project names, and renders everything into a high-density TUI dashboard — with a built-in forensic engine that can **recover the real dates of commands you typed before you even knew timestamps were missing**.

## Install

**One-liner (recommended):**
```bash
curl -fsSL https://raw.githubusercontent.com/bitflicker64/Termstory/main/scripts/install.sh | bash
```

**Or from PyPI:**
```bash
pip install termstory
```

## Quick Start

### 1. Enable timestamps (zsh only — one time setup)
TermStory works best when your shell records timestamps.
```bash
echo '\nsetopt EXTENDED_HISTORY\nsetopt HIST_STAMPS="yyyy-mm-dd"' >> ~/.zshrc
source ~/.zshrc
```
*(If you have old history without timestamps, TermStory's Timestamp Detective will automatically forensically recover real dates.)*

### 2. First Run
```bash
# Launch the interactive TUI Dashboard
termstory ui

# View your developer activity for today
termstory today

# Search across your history and session summaries
termstory search auth
```

## TUI Dashboard

![TermStory TUI Dashboard](https://raw.githubusercontent.com/bitflicker64/termstory/main/docs/assets/tui-dashboard.png)

*TermStory v0.6.0 — AI-narrated daily chronicle, project timeline, focus metrics, and command playback.*

## Documentation & Key Features

Learn more about TermStory by exploring the detailed documentation below:

- **[Architecture & Core Concepts](docs/architecture.md)** — Project layout, ingestion pipeline, timestamp detective, and git correlation.
- **[Database Schema](docs/database-schema.md)** — Thread-safe SQLite WAL schema and concurrency handling.
- **[Privacy Sanitizer](docs/privacy.md)** — Learn how TermStory redacts credentials and protects local PII.
- **[AI Integration](docs/ai-integration.md)** — Zero-dependency LLM client supporting Groq, OpenAI, and Ollama.
- **[TUI & AI Narratives](docs/tui.md)** — Dashboard layout, interactive features, and AI-generated logs.
- **[CLI Reference](docs/cli-reference.md)** — Extended subcommands (Predict, Ask, RPG Classes, Replay, etc.).
- **[Configuration](docs/configuration.md)** — Setup guide for AI providers and settings.
- **[Troubleshooting](docs/troubleshooting.md)** — Tips for recovering history and handling errors.

## Contributing

- We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to get started, set up your development environment, and submit pull requests.
- Please read our [Code of Conduct](CODE_OF_CONDUCT.md).

## Uninstall

Use the dedicated uninstaller script — it removes the venv, data directory, and the PATH line that the installer added to your shell RC file:

```bash
curl -fsSL https://raw.githubusercontent.com/bitflicker64/Termstory/main/scripts/uninstall.sh | bash -s -- --yes
```

Or run locally:

```bash
bash scripts/uninstall.sh --yes
```

Or uninstall by hand (a subset of what the script does):

```bash
pip uninstall termstory -y 2>/dev/null
rm -rf ~/.termstory-venv
rm -rf ~/.termstory
```

## License

MIT © TermStory Contributors

**GitHub:** https://github.com/bitflicker64/Termstory  
**PyPI:** https://pypi.org/project/termstory/
