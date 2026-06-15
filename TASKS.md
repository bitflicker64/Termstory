# TermStory Master Task List

> Generated: June 15, 2026. Updated after each batch.

---

## ✅ Batch 1 — Complete (PR #7)
- [x] Parser engine fixes & session builder improvements
- [x] TUI integration & AI summary enhancements

## ✅ Batch 2 — Complete (PR #9, v0.5.0)
- [x] Fix `mix_stderr=False` for Python 3.9 compat
- [x] Bump v0.5.0 release

## 🔄 Batch 3 — In Progress (FTS5 + Stress + AI Contexts)
- [ ] SQLite FTS5 Integration (full-text search)
- [ ] Concurrency Stress Tests & massive history simulation
- [ ] Project-Specific AI Contexts

---

## 📋 v0.5.x — Ready for Batch 4+

### Priority
- [ ] **PyPI automated release workflow** — tag-triggered build & publish to PyPI
  - `.github/workflows/release.yml`
  - `pyproject.toml` build config
  - TestPyPI → PyPI pipeline

- [ ] **`termstory profile` command** — CLI profiler surfacing slowest DB queries
  - `--queries` flag showing top N+1 patterns
  - `--sessions` flag showing longest-running + highest-command-count sessions
  - Integration with `database.py` timing hooks

### Quality & Hardening
- [ ] **Pre-existing CI failure investigation** — check what tests failed before changes
- [ ] **`test_exporter.py` Greptile feedback refactor** — use `CliRunner(mix_stderr=False)` constructor instead of monkeypatch wrapper

### Future Concepts (Longer-Term)
- [ ] **"REM Sleep" Context Consolidation** — overnight heavy AI meta-analysis
- [ ] **MCP Time-Machine Snapshots** — semantic snapshots via Model Context Protocol
- [ ] **Semantic Deep-Dive via Local RAG** — zero-keyword search w/ local embeddings
