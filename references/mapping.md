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
| `tool` | `Tool` | Requires a matching HiAgent tool/plugin ID in the target workspace. |
| `http-request` | `Http` | Map method, URL, headers/body/auth when present. |

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

