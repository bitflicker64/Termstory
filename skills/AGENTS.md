# skills/ — Custom Skills

## Structure

- `termstory-tui/` — Skill for interacting with the TermStory TUI.

## Adding a New Skill

1. Create a new directory under `skills/` using the skill name.
2. Add a `SKILL.md` file with YAML frontmatter (`name`, `description`, and trigger conditions).
3. Add any supporting files (references, templates, scripts, etc.) as needed.
4. Test the skill using the documented agent/skill loader command for this repository.

## Conventions

- Skill directory names are lowercase and hyphenated.
- Each skill directory contains a `SKILL.md` file.
- `SKILL.md` uses YAML frontmatter followed by a Markdown body.