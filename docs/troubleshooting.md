## 18. Troubleshooting

### Reset everything

```bash
rm -rf ~/.termstory/
termstory ui   # Fresh start, re-runs onboarding
```

### Force a re-ingest

```bash
termstory today
```

Reads the history file fresh and updates the database.

### Enable EXTENDED_HISTORY manually

```bash
echo '\nsetopt EXTENDED_HISTORY' >> ~/.zshrc
source ~/.zshrc
```

Future commands will get real timestamps. The Timestamp Detective handles everything you typed before enabling this.

### Local AI (Ollama)

```bash
ollama pull llama3
ollama run llama3
termstory ui   # Press 'o' → Ctrl+L to select Ollama
```

### My old history all shows up as "today"

This is expected if you never had `EXTENDED_HISTORY`. The Timestamp Detective will recover what it can using git commits, file stat, and package manager artifacts. Commands it can't place with evidence are grouped in `📦 Legacy Archive` or `🔍 Recovered Archive` in the TUI.

### The detective didn't recover my history

The Detective needs forensic artifacts — git repos, installed packages, or created files — that still exist on disk. If you've wiped your machine since typing those commands, the artifacts are gone and full recovery isn't possible. Enable `EXTENDED_HISTORY` now so this doesn't happen going forward.

---
