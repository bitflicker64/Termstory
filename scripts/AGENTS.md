# scripts/ — Shell Scripts

## Files

- `install.sh` — One-line installer (`curl | bash`) that downloads the project source and installs it.
- `uninstall.sh` — Uninstaller that removes the installed command, TermStory installation artifacts, shell configuration entries, and the `~/.termstory` data directory. Use `--yes` to skip the confirmation prompt.
- `pocs/` — Proof-of-concept scripts used for experimentation. These are not part of the released package. `poc_fts5.py` targets `~/.termstory/termstory.db` and mutates live local data, so do not run it unless you intend to alter that database.

## Conventions

- The install script checks for a Python 3 interpreter and uses `pip` to install the project.
- The installer attempts a virtual environment installation first and falls back to a user installation if needed.
- Scripts in `pocs/` are experimental and are not intended as stable tooling; inspect them before running because some target local TermStory data rather than isolated fixtures.