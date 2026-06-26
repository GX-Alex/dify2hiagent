---
name: dify2hiagent
description: Convert Dify workflow export YAML files into HiAgent workflow YAML imports, especially for 火山引擎/HiAgent workflow migration. Use when the user asks to migrate, transform, debug, or validate Dify workflows for HiAgent, including mapping Start/End/LLM/Code/Knowledge nodes, preserving prompts, wrapping Dify code nodes for HiAgent, diagnosing HiAgent import/runtime errors, or generating reusable conversion scripts and reports.
---

# Dify to HiAgent

Use this skill to convert Dify workflow exports into HiAgent-importable workflow YAML and to debug import/runtime errors from the converted workflows. This repository also works as a standalone CLI project for Claude Code, OpenCode, Hermes, OpenClaw, and other AI coding tools; those tools should read `README.md` and `AGENTS.md`.

## Workflow

1. Inspect the Dify workflow YAML:
   - Read `workflow.graph.nodes` and `workflow.graph.edges`.
   - Record node IDs, node types, variable selectors, model names, code outputs, and knowledge retrieval settings.
2. Inspect at least one HiAgent export YAML when available:
   - Prefer a sample with non-empty LLM nodes.
   - Copy `Depends.ModelMap` and model feature fields from the sample when the target workspace should reuse the same model.
3. Run or adapt `scripts/convert_dify_to_hiagent.py`.
4. Validate the generated HiAgent YAML:
   - It parses as YAML.
   - Every `Depends[].NodeCode` and variable `NodeCode` points to an existing node `Code`.
   - `Type: Start` has `Name: Start`; `Type: End` has `Name: End`.
   - Python Code nodes compile.
   - Code nodes use `dify_main(...)` plus `handler(params)`, not `main(...)`.
   - Plugin Tool nodes copied from a HiAgent template include the matching `ToolMap` / `PluginMap` dependencies.
5. Ask the user to import into HiAgent and report the exact import/runtime error.
6. Patch the converter, regenerate the YAML, and rerun the static checks.

## Converter

Script:

```bash
python scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --template hiagent_sample_with_llm.yaml \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

If no HiAgent template is available, pass placeholders explicitly:

```bash
python scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --model-id REPLACE_WITH_HIAGENT_MODEL_ID \
  --model-name REPLACE_WITH_HIAGENT_MODEL_NAME \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

## Required HiAgent Details

- `DLVersion: v2`
- Top-level `Depends` contains resource maps such as `ModelMap`, `KnowledgeMap`, `ToolMap`, and `PluginMap`.
- Nodes are a flat `Nodes` list.
- Edges are expressed as per-node `Depends: [{NodeCode: ...}]`.
- Node references use `NodeCode`, `Path`, `RefType: node_field`.
- LLM prompts go in `Prompt` and `SystemPrompt`; `PromptConfig` and `SystemPromptConfig` can remain `null`.
- Start and End node names are reserved:
  - `Type: Start` must have `Name: Start`.
  - `Type: End` must have `Name: End`.

Read [references/mapping.md](references/mapping.md) for the field mapping and known pitfalls.

## Template Transform Nodes

Dify `template-transform` nodes should become HiAgent `TextProcessing` nodes with `TextProcessingType: Concat`. Insert a small default-value Code node before TextProcessing so optional upstream fields still emit values, normalize simple `{{ var }}` placeholders to `{{var}}`, convert `{{ var or 'default' }}` into `{{var}}` plus a Code-node fallback, and expose `output` for downstream references. Warn when the Dify template uses Jinja control flow because HiAgent text processing may not evaluate it.

## Assigner Nodes

Dify `assigner` is a variable assignment node. Convert it to a HiAgent Code node that returns the assigned variable names as outputs, then resolve downstream `conversation.*` references to the nearest upstream assigner output. This preserves normal in-run data flow; cross-turn conversation persistence still needs HiAgent-native review.

## Plugin Nodes

When the source workflow has Dify document extraction or tool nodes, pass a HiAgent template that already contains the target plugin nodes. Known mappings include `document-extractor` -> `convert_to_markdown`, `markdown_to_docx_converter` -> `md_to_docx`, plus direct mappings for `browser_basic` and `QuerySQLDatabase` when the template has those tools. See [references/mapping.md](references/mapping.md) for parameter and output paths.

## Code Node Pitfalls

HiAgent's code sandbox may append its own async `main()`. Dify Code nodes commonly define `def main(...)`. Always rename the Dify function to `dify_main(...)` and make `handler(params)` call `dify_main(...)`.

Correct pattern:

```python
def dify_main(input_text: str) -> dict:
    return {"output": input_text}

def handler(params):
    return dify_main(input_text=params.get("input_text"))
```

Avoid:

```python
def main(input_text: str) -> dict:
    return {"output": input_text}

def handler(params):
    return main(input_text=params.get("input_text"))
```

## Knowledge Nodes

Dify knowledge retrieval commonly returns `result`; HiAgent samples may expose `outputList`. The original Dify code often only consumes text content and score. Before changing schemas, verify the downstream Code nodes:

- Text content is commonly read with `item.get("content") or item.get("text") or item`.
- Score is commonly read from `metadata.score` or top-level `score`.

Do not overfit the schema until the user's HiAgent runtime output proves a mismatch.

## Import And Runtime Error Triage

- `start node name should be "Start"`: force Start node `Name` to `Start`.
- Similar End errors: force End node `Name` to `End`.
- `TypeError: main() got an unexpected keyword argument ...`: rename Dify `main` to `dify_main` and update `handler`.
- LLM prompt missing after import: inspect a real HiAgent LLM export and use `Prompt`/`SystemPrompt`.
- Knowledge node empty or wrong field: compare HiAgent runtime output with downstream Code node reads before changing `OutputSchema`.

