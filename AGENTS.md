# Agent Guide for dify2hiagent

This repository is designed to be used by generic AI coding agents including Codex, Claude Code, OpenCode, Hermes, and OpenClaw.

## Primary Task

Convert Dify workflow export YAML files into HiAgent-importable workflow YAML files.

## Preferred Workflow

1. Inspect the input Dify workflow:
   - `workflow.graph.nodes`
   - `workflow.graph.edges`
   - node types, variable selectors, code outputs, model names, retrieval settings
2. Inspect a HiAgent export sample if available, especially one with LLM nodes.
3. Run the converter instead of reimplementing conversion logic:

```bash
python3 scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --template hiagent_sample.yaml \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

4. Validate the generated YAML:
   - YAML parses.
   - `Start` and `End` node names are exactly `Start` and `End`.
   - All `NodeCode` references are valid.
   - Python Code nodes compile.
   - Dify Code business functions are named `dify_main`, and `handler(params)` calls `dify_main`.
5. If HiAgent import/runtime errors occur, patch the converter and regenerate the YAML.

## Important Conventions

- Keep Codex skill files valid: `SKILL.md`, `agents/openai.yaml`, `references/`, and `scripts/`.
- Keep the CLI standalone for non-Codex agents.
- Do not manually edit generated HiAgent YAML unless debugging; prefer improving the converter.
- Do not overfit Knowledge output schemas before seeing actual HiAgent runtime output.
- Do not commit workflow exports that may contain private business data unless explicitly requested.

## Validation Commands

```bash
python3 -m pip install -r requirements.txt
python3 scripts/convert_dify_to_hiagent.py --help
```

If this repository is installed as a Codex skill, validate it with:

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py .
```
