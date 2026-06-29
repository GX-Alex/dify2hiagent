# dify2hiagent

[中文](#中文) | [English](#english)

## 中文

`dify2hiagent` 是一个把 Dify 工作流导出文件转换为 HiAgent（火山引擎）工作流 YAML 导入文件的工具项目，同时保留 Codex Skill 结构，方便 Codex、Claude Code、OpenCode、Hermes、OpenClaw 等 AI 编码工具协作维护。

### 功能

- 读取 Dify `*.workflow.yml` 导出文件。
- 生成 HiAgent `DLVersion: v2` 工作流 YAML；Dify `advanced-chat` / chatflow 会生成 HiAgent Agent zip 包。
- 映射常见节点：`Start`、`End`、`LLM`、`Code`、`Knowledge`、条件分支/选择器、变量赋值、模版转换/文本处理、文档提取和部分插件工具。
- 保留 LLM `Prompt` / `SystemPrompt`。
- 将 Dify Code 节点包装为 HiAgent 可运行的 `handler(params)`。
- 自动规避 HiAgent 沙箱注入 `main()` 导致的函数名冲突。
- 生成转换报告，标记需要导入后人工绑定的模型、知识库、工具等资源。
- 从 HiAgent 插件模版复制 `ToolMap` / `PluginMap`，支持 `convert_to_markdown`、`md_to_docx`、`browser_basic`、`QuerySQLDatabase` 等已知工具映射。
- 将 Dify 变量赋值节点转换为 Code 节点，复现 overwrite/append/extend/clear 等赋值操作，并把 `conversation.*` 传递给下游。
- 将 Dify `if-else` 条件分支转换为 HiAgent `Condition` 选择器节点，并保留下游 `PortID` 分支路由。
- 将 Dify 模版转换节点转换为 HiAgent 文本处理拼接节点，并插入默认值预处理 Code 节点，避免非必填上游字段不注入变量。
- 将 Dify 对话型应用转换为 HiAgent 对话型工作流 zip，包含 `index.yaml` 和 `agent/<name>.yaml`。

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

Dify `advanced-chat` / chatflow 会输出 HiAgent 对话型工作流 zip。建议同时提供普通资源模版和对话型 Agent zip 模版，以复用外层配置和 zip 尾部签名：

```bash
python3 scripts/convert_dify_to_hiagent.py input.chatflow.yml \
  --template hiagent_resource_sample.yaml \
  --agent-template hiagent_chatflow_agent_export.zip \
  -o output.hiagent.zip \
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
- `if-else` 基础分支已映射为 HiAgent 选择器；Dify 数值大小比较、复杂分支和 HTTP 复杂鉴权等仍需结合真实 HiAgent 导出样例继续补映射。
- Dify `file-list` 文档提取当前按 HiAgent 插件输入约束默认取首个文件 URL。
- Knowledge 返回结构不要过早过度适配；应先看运行时输出和下游 Code 节点实际读取字段。

## English

`dify2hiagent` converts Dify workflow export YAML files into HiAgent workflow YAML imports. It also keeps a Codex Skill layout so AI coding tools such as Codex, Claude Code, OpenCode, Hermes, and OpenClaw can use and maintain it consistently.

### Features

- Reads Dify `*.workflow.yml` exports.
- Generates HiAgent `DLVersion: v2` workflow YAML; Dify `advanced-chat` / chatflow apps are emitted as HiAgent Agent zip packages.
- Maps common nodes: `Start`, `End`, `LLM`, `Code`, `Knowledge`, conditional branch/selectors, variable assignment, template transform/text processing, document extraction, and selected plugin tools.
- Preserves LLM `Prompt` and `SystemPrompt`.
- Wraps Dify Code nodes with HiAgent-compatible `handler(params)`.
- Avoids HiAgent sandbox `main()` name collisions by renaming Dify business functions to `dify_main(...)`.
- Writes a conversion report with resources that must be rebound after import.
- Converts Dify `if-else` branches into HiAgent `Condition` selector nodes and preserves downstream `PortID` branch routing.
- Converts Dify variable assignment nodes into Code nodes that reproduce overwrite/append/extend/clear operations and pass `conversation.*` values downstream.
- Converts Dify template transform nodes into HiAgent text processing concat nodes and inserts a default-value Code node so optional upstream fields still populate variables.
- Converts Dify chatflow apps into HiAgent ChatFlow Agent zip packages containing `index.yaml` and `agent/<name>.yaml`.
- Copies `ToolMap` / `PluginMap` entries from a HiAgent plugin template for known tools such as `convert_to_markdown`, `md_to_docx`, `browser_basic`, and `QuerySQLDatabase`.

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

Dify `advanced-chat` / chatflow apps are exported as HiAgent Agent zip packages. Prefer passing both a resource sample and a ChatFlow Agent zip template:

```bash
python3 scripts/convert_dify_to_hiagent.py input.chatflow.yml \
  --template hiagent_resource_sample.yaml \
  --agent-template hiagent_chatflow_agent_export.zip \
  -o output.hiagent.zip \
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
- Basic `if-else` branches are mapped to HiAgent selectors; numeric comparisons, complex branch logic, and advanced HTTP auth need additional mapping based on real HiAgent exports.
- Dify `file-list` document extraction currently uses the first file URL to fit the observed HiAgent plugin `uri` input.
- Do not overfit Knowledge output schemas until runtime output and downstream Code reads prove a mismatch.
