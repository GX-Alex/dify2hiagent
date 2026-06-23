# Claude Code Instructions

Use this repository as a standalone Dify-to-HiAgent conversion tool.

- Read `README.md` for user-facing usage.
- Read `AGENTS.md` for agent workflow rules.
- Prefer running `scripts/convert_dify_to_hiagent.py`; do not rewrite conversion logic manually.
- When changing conversion behavior, update `references/mapping.md` if the mapping changes.
- Validate the Codex skill structure after editing when the validator is available.
- Keep generated customer workflow files out of commits unless the user explicitly asks to include them.
