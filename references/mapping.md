# Dify to HiAgent Mapping Reference

## Node Mapping

| Dify node type | HiAgent Type | Notes |
| --- | --- | --- |
| `start` | `Start` | Convert variables into `Configs.Start.InputSchema` and `OutputSchema`; `Name` must be `Start`. |
| `end` | `End` | Convert outputs into `Configs.End.InputVariables`; `Name` must be `End`. |
| `llm` | `LLM` | Use HiAgent `Prompt` and `SystemPrompt`; output is usually `raw_output`. |
| `code` | `Code` | Wrap with `handler(params)` and rename Dify `main` to `dify_main`. |
| `knowledge-retrieval` | `Knowledge` | Use `QueryVariable`; knowledge resources must be rebound in HiAgent. |
| `if-else` | `Condition` | Needs explicit branch mapping; implement after seeing a HiAgent Condition export. |
| `tool` | `Tool` | Requires a matching HiAgent tool/plugin ID in the target workspace; known plugin mappings are copied from a HiAgent template. |
| `http-request` | `Http` | Map method, URL, headers/body/auth when present. |

## Plugin And Tool Mapping

Pass a HiAgent export template that contains plugin Tool nodes when converting Dify workflows with document extraction or tool calls. The converter builds a Tool catalog from template `Nodes` and copies matching `Depends.ToolMap` / `Depends.PluginMap` entries into the generated workflow.

| Dify node/tool | HiAgent ToolName | Parameter mapping | Output mapping / notes |
| --- | --- | --- | --- |
| `document-extractor` | `convert_to_markdown` | Dify `variable_selector` file URL -> `uri` | Dify `text` references map to `content[0].text`; if the Dify input is `file-list`, use the first file URL as `<variable>[0].url` because observed HiAgent plugin input accepts one `uri`. |
| `tool_name: markdown_to_docx_converter` | `md_to_docx` | `markdown_content` -> `md_text`; `title` -> `output_filename` | Dify `files` references map to `content[0].files`; HiAgent also exposes `structuredContent.filepath` and `structuredContent.name`. |
| `tool_name: md_to_docx` | `md_to_docx` | `md_text` -> `md_text`; `output_filename` -> `output_filename` | Use when Dify already names the tool like HiAgent. |
| `tool_name: convert_to_markdown` | `convert_to_markdown` | `uri` -> `uri` | Direct tool-name match. |
| `tool_name: browser_basic` | `browser_basic` | `url` -> `url`; `full_page_ocr` -> `full_page_ocr` | Requires the Browser plugin node in the template. |
| `tool_name: QuerySQLDatabase` | `QuerySQLDatabase` | `dsn` -> `dsn`; `query` -> `query` | Requires the SQL plugin node in the template. |

Dify `conversation.*` variables do not exist as a HiAgent global conversation store. For common Dify assigner patterns, convert `assigner` to a pass-through Code node and bind downstream tool inputs to that node's output path. Review this after import if the original workflow relies on persistent conversation semantics.

## Type Mapping

| Dify type | HiAgent Type code |
| --- | --- |
| string, paragraph, text-input | `0` |
| integer | `1` |
| boolean | `2` |
| number | `3` |
| object | `4` |
| array string | `5` |
| array number | `8` |
| array object | `9` |
| file object from HiAgent samples | `10` |

## Variable References

Dify selectors such as:

```yaml
value_selector:
  - build_queries
  - industry_query
```

become:

```yaml
Name: industry_query
NodeCode: <mapped Code for build_queries>
Path: industry_query
RefType: node_field
```

Special cases:

- Dify LLM `text` output usually maps to HiAgent LLM `raw_output`.
- Dify Knowledge `result` usually maps to HiAgent Knowledge `outputList`.

## HiAgent LLM Shape

Known working shape from a HiAgent export:

```yaml
Configs:
  LLM:
    InputVariables:
    - Name: report_text
      NodeCode: upstream_code
      Path: content
      RefType: node_field
    ModelID: ...
    ModelName: ...
    OutputFormat: json
    OutputSchema:
    - Name: raw_output
      Type: 0
    Prompt: "user prompt with {{report_text}}"
    PromptConfig: null
    SystemPrompt: "system prompt"
    SystemPromptConfig: null
```

## Static Checks

Use these checks before handing off a converted file:

```python
import yaml
from pathlib import Path

d = yaml.safe_load(Path("output.hiagent.yaml").read_text())
codes = {n["Code"] for n in d["Nodes"]}
missing = []
for n in d["Nodes"]:
    for dep in n.get("Depends") or []:
        if dep.get("NodeCode") not in codes:
            missing.append((n["Name"], dep))
    if n["Type"] == "Code" and n["Configs"]["Code"].get("Language") == 1:
        compile(n["Configs"]["Code"]["Code"], f"<node {n['Name']}>", "exec")
assert not missing
assert [(n["Type"], n["Name"]) for n in d["Nodes"] if n["Type"] in ("Start", "End")]
```

