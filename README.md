# dify2hiagent

[中文](#中文) | [English](#english)

## 中文

`dify2hiagent` 是一个把 Dify 工作流导出文件转换为 HiAgent（火山引擎）工作流 YAML 导入文件的工具项目，同时保留 Codex Skill 结构，方便 Codex、Claude Code、OpenCode、Hermes、OpenClaw 等 AI 编码工具协作维护。

### 功能

- 读取 Dify `*.workflow.yml` 导出文件。
- 生成 HiAgent `DLVersion: v2` 工作流 YAML。
- 映射常见节点：`Start`、`End`、`LLM`、`Code`、`Knowledge`。
- 保留 LLM `Prompt` / `SystemPrompt`。
- 将 Dify Code 节点包装为 HiAgent 可运行的 `handler(params)`。
- 自动规避 HiAgent 沙箱注入 `main()` 导致的函数名冲突。
- 生成转换报告，标记需要导入后人工绑定的模型、知识库、工具等资源。

### 目录结构

```text
.
├── SKILL.md                         # Codex skill 入口
├── README.md                        # 中英文项目说明
├── AGENTS.md                        # 通用 AI 编码工具指南
├── CLAUDE.md                        # Claude Code 指南
├── agents/openai.yaml               # Codex UI metadata
├── references/mapping.md            # 映射规则参考
├── requirements.txt                 # Python 依赖
└── scripts/convert_dify_to_hiagent.py
```

### 安装

```bash
python3 -m pip install -r requirements.txt
```

依赖很轻，目前只需要 `PyYAML`。

### 使用

推荐提供一个 HiAgent 导出样例，尤其是包含 LLM 节点的样例，这样脚本可以复用目标工作空间的 `ModelMap` 和模型 ID：

```bash
python3 scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --template hiagent_sample_with_llm.yaml \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

如果没有 HiAgent 样例，也可以传占位模型信息，导入后再在 HiAgent 页面改模型：

```bash
python3 scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --model-id REPLACE_WITH_HIAGENT_MODEL_ID \
  --model-name REPLACE_WITH_HIAGENT_MODEL_NAME \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

### 转换后检查

导入 HiAgent 前建议检查：

- `Type: Start` 的节点 `Name` 必须是 `Start`。
- `Type: End` 的节点 `Name` 必须是 `End`。
- 所有 `NodeCode` 都指向存在的节点 `Code`。
- Python Code 节点能编译。
- Code 节点应使用 `dify_main(...)`，不要保留 `main(...)` 作为业务入口。
- LLM 节点应使用 `Prompt` / `SystemPrompt`，`PromptConfig` / `SystemPromptConfig` 可为 `null`。

### 通用 AI 工具适配

- Codex：可直接作为 skill 使用，入口见 `SKILL.md`。
- Claude Code：读取 `CLAUDE.md`。
- OpenCode、Hermes、OpenClaw 及其他 agent：读取 `AGENTS.md`。
- 所有工具都应优先调用 `scripts/convert_dify_to_hiagent.py`，不要手工重写转换逻辑。

### 已知边界

- Dify 知识库 ID、插件 ID、工具 ID 不能直接转换为 HiAgent 工作空间资源，需要导入后重新绑定。
- `if-else`、复杂工具节点、HTTP 复杂鉴权等需要结合真实 HiAgent 导出样例继续补映射。
- Knowledge 返回结构不要过早过度适配；应先看运行时输出和下游 Code 节点实际读取字段。

## English

`dify2hiagent` converts Dify workflow export YAML files into HiAgent workflow YAML imports. It also keeps a Codex Skill layout so AI coding tools such as Codex, Claude Code, OpenCode, Hermes, and OpenClaw can use and maintain it consistently.

### Features

- Reads Dify `*.workflow.yml` exports.
- Generates HiAgent `DLVersion: v2` workflow YAML.
- Maps common nodes: `Start`, `End`, `LLM`, `Code`, and `Knowledge`.
- Preserves LLM `Prompt` and `SystemPrompt`.
- Wraps Dify Code nodes with HiAgent-compatible `handler(params)`.
- Avoids HiAgent sandbox `main()` name collisions by renaming Dify business functions to `dify_main(...)`.
- Writes a conversion report with resources that must be rebound after import.

### Install

```bash
python3 -m pip install -r requirements.txt
```

Only `PyYAML` is required.

### Usage

Prefer passing a HiAgent export sample with LLM nodes so the converter can reuse the target workspace `ModelMap` and model IDs:

```bash
python3 scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --template hiagent_sample_with_llm.yaml \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

Without a HiAgent sample, pass placeholder model information and update the model in HiAgent after import:

```bash
python3 scripts/convert_dify_to_hiagent.py input.workflow.yml \
  --model-id REPLACE_WITH_HIAGENT_MODEL_ID \
  --model-name REPLACE_WITH_HIAGENT_MODEL_NAME \
  -o output.hiagent.yaml \
  --report output.hiagent.report.md
```

### AI Tool Compatibility

- Codex: use `SKILL.md` as the skill entrypoint.
- Claude Code: read `CLAUDE.md`.
- OpenCode, Hermes, OpenClaw, and other agents: read `AGENTS.md`.
- All agents should invoke `scripts/convert_dify_to_hiagent.py` instead of rewriting conversion logic by hand.

### Validation Checklist

Before handing off a converted workflow:

- `Type: Start` node has `Name: Start`.
- `Type: End` node has `Name: End`.
- Every `NodeCode` points to an existing node `Code`.
- Python Code nodes compile.
- Code nodes use `dify_main(...)` and `handler(params)`, not a business function named `main(...)`.
- LLM nodes use `Prompt` / `SystemPrompt`; `PromptConfig` / `SystemPromptConfig` may remain `null`.

### Known Limits

- Dify knowledge base IDs, plugin IDs, and tool IDs cannot be directly migrated into HiAgent workspace resources. Rebind them after import.
- `if-else`, complex tool nodes, and advanced HTTP auth need additional mapping based on real HiAgent exports.
- Do not overfit Knowledge output schemas until runtime output and downstream Code reads prove a mismatch.
