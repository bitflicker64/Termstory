# TermStory Fun Feature Backlog 🚀

A collection of wild, creative, and slightly humorous feature ideas brainstormed by the AI agents for future integration into the TermStory Developer Memory Engine.

---

## 🤖 AI Features

### 1. The 'Git-Blame' Anger Translator 🤬
Instead of just showing your clean, generic "fix stuff" commit messages, the AI cross-references them with the preceding 3 hours of frantic shell errors, furious `kill -9` commands, and rapid recompiles. It then generates the *real*, dramatically unfiltered commit message describing what you were actually feeling (e.g., "fix typo" translates to "I spent 3 hours debugging because I forgot a single trailing slash in a config file and my soul has left my body.").

### 2. Daily RPG Class & Archetype Assigner 🧙‍♂️
The AI analyzes your command history patterns and assigns you a dynamic daily RPG class or superhero alter ego. Did you spend the whole day piping `awk`, `sed`, and `grep` together? You're crowned a **"Level 12 Regex Sorcerer."** Fought with container orchestration for 6 hours? You're **"The Docker Demolitionist."** It could even generate a custom ASCII-art crest for your daily summary based on your class.

### 3. The Predictive Bug Fortune Teller 🔮
An AI feature that analyzes the chaotic, unhinged nature of your late-night, caffeinated terminal sessions—looking for things like frantic `git add .`, bypassed tests, and fast force-pushes. It then generates an ominous, mystical fortune predicting exactly what kind of catastrophic, deeply hidden bug your future self will inevitably have to deal with when Monday morning arrives.

### 4. `termstory agy` — AI Pair Programmer Bridge ✅ (v0.4.0)
One-shot subcommand that launches `agy -p` from within TermStory, bridging your shell history context into a live AI pair-programming session. Gracefully errors if `agy` is not on PATH.

---

## 🎨 TUI/UX Polish

### 5. "The Matrix Defrag" (Data Ingestion Animation)
**Concept:** When the user boots TermStory for the first time or ingests a massive batch of new shell history, ditch the standard progress bar. Instead, turn the entire `DetailsCanvas` into a cascading, Matrix-style data stream.
**Execution:** Rapidly scroll raw shell commands interlaced with hex codes in dim green or cyan down the screen. As the `Parser Engine` locks commands into the SQLite DB, specific lines "snap" into bright white, readable text for a split second before scrolling away. It gives a visceral, hacker-movie feel to the ingestion process, visualizing the engine crunching through thousands of lines without adding any extra UI panels.

### 6. "Heatmap Pulse & Cyber-Glitch" (Streak Micro-animations)
**Concept:** Reward insane coding streaks with subtle, cyberpunk-inspired micro-animations within the existing GitHub-style stats header.
**Execution:** If a user hovers over a day in the heatmap where they hit a personal best or worked an 8+ hour continuous session, that specific heatmap block and the corresponding "Total Duration" text begin to slowly "pulse" (e.g., cycling from dim magenta to neon pink). If they hit an all-time record, the "Current Streak" counter applies a brief text "glitch" effect—rapidly swapping random ASCII characters for 0.5 seconds before settling on the actual number. It adds a highly dynamic, rewarding feel to the top bar while keeping the layout completely flat.

### 7. "Ghost Typer Playback" (Chronicle Animation)
**Concept:** Turn the static Daily Chronicle into an interactive movie of the developer's day.
**Execution:** Add a `p` (Playback) keybinding when viewing a specific date's node. When pressed, the `DetailsCanvas` clears and begins "re-typing" the AI narrative and the day's commands as if a ghost is sitting at the terminal. The typing speed scales dynamically: fast bursts of commands (like a rapid sequence of `git add`, `git commit`, `git push`) appear instantly, while long pauses between sessions pause the typing momentarily. It transforms a static log into an over-the-top, nostalgic timeline replay, using only standard text rendering.

---

## 🎮 Gamified Metrics

### 8. The Vampire Coder Index 🧛‍♂️
*What it tracks:* The percentage and intensity of your coding sessions that occur between midnight and 5 AM. 
*Why it's fun:* It playfully roasts developers for their late-night coding binges. A high index means you run entirely on caffeine and moonlight, producing your best (or at least, your most chaotic) code while the rest of the world sleeps.

### 9. The Project Necromancer Score 🧟‍♂️
*What it tracks:* How often you return to a "dead" project (a repository untouched for 6+ months) and actually complete a meaningful coding session in it.
*Why it's fun:* We all have a graveyard of abandoned side projects. This metric rewards you for bravely resurrecting old codebases instead of just running `git status`, sighing, and closing the terminal.

### 10. The "Rage-Quit" Signature 🛑
*What it tracks:* The specific command that most frequently serves as your *final* command before a long period of inactivity. 
*Why it's fun:* It answers the question: "How do you end your day?" Is your signature sign-off a triumphant `git push origin main`, or is it a frustrated `killall node` followed by 14 hours of total terminal silence?

---

## 🔧 Shipped in v0.6.0

- ✅ **`termstory agy` subcommand**: Bridges TermStory to `agy -p` for instant AI pair-programming.
- ✅ **`termstory ask` subcommand**: Search and Q&A over history with TF-IDF and LLMs.
- ✅ **`termstory optimize` (VACUUM)**: Reclaims SQLite disk space via `VACUUM`.
- ✅ **`termstory --version` flag**: Reports current version from `__version__`.
- ✅ **TUI status bar version + last ingestion**: StatsHeader now shows `v0.6.0` and `Synced: <date>`.
- ✅ **GitHub Actions CI**: Automated test pipeline on every push via `.github/workflows/ci.yml`.
- ✅ **RPG Class Alter Ego (`termstory rpg-class`)**: Assigns daily RPG classes with LLM biographies.
- ✅ **Vampire Coder Index (`termstory vampire-index`)**: Late-night terminal activity metric.
- ✅ **Project Necromancer Score (`termstory necromancer`)**: Track resurrection rate of dormant repositories.
- ✅ **Rage-Quit Signatures (`termstory rage-quit`)**: Final command signature before inactivity.
- ✅ **Anger Translator (`termstory anger-translator`)**: Translate frustrating errors/commits to funny roasts.
- ✅ **Predictive Bug Fortunes (`termstory fortune-teller`)**: Forecast Monday bugs from late-night sessions.
- ✅ **TUI Cyberpunk Aesthetics**: Matrix falling code ingestion, heatmap pulses, streak glitches.
- ✅ **Ghost Typer Playback**: Interactive playbacks for daily chronicles in TUI.
- ✅ **Local RAG Search (`termstory search --semantic`)**: Hybrid BM25/semantic history search.
- ✅ **Project Contexts (`termstory project context`)**: Feed custom goals/descriptions into AI prompts.
- ✅ **MCP Snapshots (`termstory replay --mcp`)**: Capture and display active IDE, Git, and shell states.
- ✅ **Database Profiler (`termstory profile`)**: Query execution profiling and N+1 query prevention.
