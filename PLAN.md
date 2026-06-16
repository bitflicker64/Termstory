# TermStory Development Plan: Batches 5-8

This document outlines the next four developmental batches for the TermStory Developer Memory Engine, mapped directly from the feature proposals in `features.md`.

---

## 📦 Batch 5: AI-Driven Git Translation & Predictive Bug Fortunes
* **Branch Name:** `feat/batch-5-anger-translator-bug-fortunes`
* **Target Features:** 
  - "Git-Blame" Anger Translator (Feature 1)
  - The Predictive Bug Fortune Teller (Feature 3)

### Tasks
- [ ] **Task 1: anger-translator heuristics**
  - Implement Git correlation to fetch preceding 3 hours of shell error patterns, recompiles, and commands prior to a commit.
  - Implement LLM prompt logic in [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py) to translate clean, generic commit messages into realistic, unfiltered developer emotion logs.
- [ ] **Task 2: predictive-bug-fortunes AI prompts**
  - Implement heuristic analyzer to flag chaotic, late-night shell sessions (frantic `git add .`, bypassed tests, fast force-pushes).
  - Add LLM prompts to generate warning developer fortunes predicting future Monday-morning bugs.
- [ ] **Task 3: CLI & TUI endpoints**
  - Expose anger translations and fortunes via CLI subcommands in [cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py).
  - Add optional display segments inside [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py)'s Details Canvas.
- [ ] **Task 4: Testing**
  - Add unit tests verifying prompt generation and translation output structure in [test_ai.py](file:///Users/himanshuverma/personal/termstory/tests/test_ai.py).

### Files to Modify
* Core AI/Git Logic: [termstory/ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py), [termstory/git_integration.py](file:///Users/himanshuverma/personal/termstory/termstory/git_integration.py)
* UI & Formatting: [termstory/formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py), [termstory/cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py), [termstory/tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py)
* Test Coverage: [tests/test_ai.py](file:///Users/himanshuverma/personal/termstory/tests/test_ai.py)

---

## 📦 Batch 6: RPG Classes & Vampire Coder Metrics
* **Branch Name:** `feat/batch-6-rpg-classes-vampire-coder`
* **Target Features:**
  - Daily RPG Class & Archetype Assigner (Feature 2)
  - The Vampire Coder Index (Feature 8)

### Tasks
- [ ] **Task 1: RPG Class & Archetype analyzer**
  - Add command type pattern matcher (mapping pipe commands to Sorcerer, containers to Demolitionist, etc.).
  - Implement dynamic prompt inside [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py) to return a brief developer biography and customized ASCII crest based on assigned class.
- [ ] **Task 2: Vampire Coder Index calculations**
  - Implement analytical logic inside [insights.py](file:///Users/himanshuverma/personal/termstory/termstory/insights.py) tracking percentage of commits/commands executed between midnight and 5:00 AM.
- [ ] **Task 3: Integration & presentation**
  - Expose RPG classes and the Vampire Index in `termstory insights` CLI and inside the TUI dashboard's stats header.
- [ ] **Task 4: Testing**
  - Write unit tests for vampire calculations and archetype tagging in [test_insights.py](file:///Users/himanshuverma/personal/termstory/tests/test_insights.py).

### Files to Modify
* Metrics & Analytics: [termstory/insights.py](file:///Users/himanshuverma/personal/termstory/termstory/insights.py)
* AI/Biographies: [termstory/ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py)
* UI & Display: [termstory/tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [termstory/cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py), [termstory/formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py)
* Test Coverage: [tests/test_insights.py](file:///Users/himanshuverma/personal/termstory/tests/test_insights.py)

---

## 📦 Batch 7: Project Necromancy & Rage-Quit Signatures
* **Branch Name:** `feat/batch-7-project-necromancer-rage-quit`
* **Target Features:**
  - The Project Necromancer Score (Feature 9)
  - The "Rage-Quit" Signature (Feature 10)

### Tasks
- [ ] **Task 1: Project Resurrection Tracking**
  - Add database helper query in [database.py](file:///Users/himanshuverma/personal/termstory/termstory/database.py) to check projects with no commands for 6+ months that suddenly have active sessions.
  - Implement Necromancer scoring metrics based on reactivation frequency.
- [ ] **Task 2: Rage-Quit command analytics**
  - Query database for commands executed directly prior to long periods of inactivity (12+ hours).
  - Group, normalize, and count final command endings to extract signature developer termination commands.
- [ ] **Task 3: CLI outputs**
  - Register new metrics in CLI formatters for Project and Insights commands.
- [ ] **Task 4: Testing**
  - Write test fixtures in [test_database_queries.py](file:///Users/himanshuverma/personal/termstory/tests/test_database_queries.py) simulating dormant databases and rage-quit timelines.

### Files to Modify
* SQLite Schema / Query Layer: [termstory/database.py](file:///Users/himanshuverma/personal/termstory/termstory/database.py)
* Analytics: [termstory/insights.py](file:///Users/himanshuverma/personal/termstory/termstory/insights.py)
* Formatters: [termstory/formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py), [termstory/cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py)
* Test Coverage: [tests/test_database_queries.py](file:///Users/himanshuverma/personal/termstory/tests/test_database_queries.py)

---

## 📦 Batch 8: Cyberpunk TUI/UX Polish
* **Branch Name:** `feat/batch-8-cyberpunk-tui-animations`
* **Target Features:**
  - "The Matrix Defrag" Ingestion Animation (Feature 5)
  - "Heatmap Pulse & Cyber-Glitch" Streak Animations (Feature 6)
  - "Ghost Typer Playback" TUI Integration (Feature 7)

### Tasks
- [ ] **Task 1: The Matrix Defrag animation**
  - Implement a Textual custom widget or dynamic screen inside [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py) rendering a green cascading Matrix stream during new history ingestion.
- [ ] **Task 2: Heatmap Pulsing and Glitching**
  - Apply CSS classes and timers inside the stats header to pulse cells on hover.
  - Introduce random character ASCII-swapping glitches to the streak count component when records are broken.
- [ ] **Task 3: Ghost Typer Chronicle Replays**
  - Add a `p` (Playback) keybinding inside the TUI.
  - Clear `DetailsCanvas` on trigger and output text letter-by-letter with variable speeds to simulate active coding.
- [ ] **Task 4: Testing**
  - Mock TUI timers and keyboard inputs in [test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py) to verify key handling and animation triggers.

### Files to Modify
* TUI Engine & Styling: [termstory/tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py)
* Test Coverage: [tests/test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py)
