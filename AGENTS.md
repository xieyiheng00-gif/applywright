# Agent guidelines: jobfill

This file is for agent runtimes other than Claude Code (Codex, Cursor, Gemini CLI, Antigravity...).
It points at the single source of truth instead of repeating it, so the runtimes cannot drift apart.

1. **Project rules and the tier model:** [CLAUDE.md](CLAUDE.md). They apply to every agent.
2. **Workflows:** the step-by-step specs in [.claude/commands/](.claude/commands/) are canonical:
   - `setup.md` - onboarding (documents folder, single resume, questionnaire, or the setup portal)
   - `apply.md` - "apply to <job URL>": detect the application system and auto-fill it
   - `fill.md` - answering a form given as field JSON
   - `learn.md` - classifying questions the bank did not recognize
   - `update.md` - keeping the profile current
   When the user says "set me up", "apply to <url>", "fill this application", "learn the new
   questions" or "update my profile", follow the matching file even though slash commands may not
   exist in your runtime. Where `apply.md` says "built-in browser", use whatever browser tool your
   runtime provides.
3. **Interface:** everything goes through `python -m jobfill <command>`; run
   `python -m jobfill --help` for the list. Data commands print JSON.
4. **Data:** personal data stays in `data/` and `documents/` (gitignored). Do not copy it elsewhere.
