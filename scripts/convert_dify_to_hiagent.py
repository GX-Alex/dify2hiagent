#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

TYPE_MAP = {
    "string": 0,
    "paragraph": 0,
    "text-input": 0,
    "integer": 1,
    "number": 3,
    "boolean": 2,
    "object": 4,
    "array[string]": 5,
    "array[number]": 8,
    "array[object]": 9,
}

DIFY_TOOL_NAME_MAP = {
    "markdown_to_docx_converter": {
        "hiagent_tool": "md_to_docx",
        "params": {"markdown_content": "md_text", "title": "output_filename"},
    },
    "md_to_docx": {
        "hiagent_tool": "md_to_docx",
        "params": {"md_text": "md_text", "output_filename": "output_filename"},
    },
    "convert_to_markdown": {
        "hiagent_tool": "convert_to_markdown",
        "params": {"uri": "uri"},
    },
    "browser_basic": {
        "hiagent_tool": "browser_basic",
        "params": {"url": "url", "full_page_ocr": "full_page_ocr"},
    },
    "QuerySQLDatabase": {
        "hiagent_tool": "QuerySQLDatabase",
        "params": {"dsn": "dsn", "query": "query"},
    },
}


def stable_code(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"dify{digest}"


def hi_type(dify_type: str | None) -> int:
    return TYPE_MAP.get(str(dify_type or "string").lower(), 0)


def file_category(var: dict[str, Any]) -> str:
    allowed = set(var.get("allowed_file_types") or [])
    if "image" in allowed:
        return "Image"
    if "audio" in allowed:
        return "Voice"
    if "video" in allowed:
        return "Video"
    if "document" in allowed:
        return "Doc"
    return "Default"


def start_schema_item(var: dict[str, Any]) -> dict[str, Any]:
    var_type = var.get("type")
    item = {
        "Desc": var.get("label") or var.get("variable", ""),
        "Name": var.get("variable", ""),
        "Required": bool(var.get("required")),
    }
    if var_type == "file":
        item.update({
            "FileCategory": file_category(var),
            "SubParameters": [
                {"Name": "url", "Required": True, "Type": 0},
                {"Name": "name", "Type": 0},
            ],
            "Type": 10,
        })
    elif var_type == "file-list":
        item.update({
            "FileCategory": file_category(var),
            "SubParameters": [
                {"Name": "url", "Required": True, "Type": 0},
                {"Name": "name", "Type": 0},
            ],
            # HiAgent exports observed in this project only expose single-file Type 10.
            # Represent file-list as Array<Object> and use the first file for plugin URI mapping.
            "Type": 9,
        })
    else:
        item["Type"] = hi_type(var_type)
    return item


def convert_template(text: str) -> str:
    """Convert Dify {{#node.field#}} placeholders to HiAgent-style {{field}} placeholders.

    HiAgent prompt rendering for imported YAML is not documented in the local export,
    so this keeps variable names readable inside the prompt editor.
    """
    return re.sub(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.*\[\]-]+)#\}\}", r"{{\1_\2}}", text or "")


def import_yaml():
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: PyYAML. Install with `python3 -m pip install -r requirements.txt` "
            "or `python3 -m pip install PyYAML`."
        ) from exc
    return yaml


def load_yaml(path: Path) -> dict[str, Any]:
    yaml = import_yaml()
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def node_lookup(dify: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in dify["workflow"]["graph"]["nodes"]}


def upstream_map(dify: dict[str, Any]) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for edge in dify["workflow"]["graph"].get("edges", []):
        parents[edge["target"]].append(edge["source"])
    return parents


def map_selector(
    selector: list[str] | None,
    code_map: dict[str, str],
    type_map: dict[str, str],
    start_var_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    selector = selector or []
    if not selector:
        return {"RefType": "node_field"}

    source = str(selector[0])
    path = ".".join(str(x) for x in selector[1:]) if len(selector) > 1 else ""
    if source in code_map:
        source_type = type_map.get(source)
        if source_type == "llm" and path == "text":
            path = "raw_output"
        elif source_type == "knowledge-retrieval" and path == "result":
            path = "outputList"
        elif source_type == "document-extractor" and path == "text":
            path = "content[0].text"
        elif source_type == "tool" and path == "files":
            path = "content[0].files"
        return {
            "NodeCode": code_map[source],
            "Path": path,
            "RefType": "node_field",
        }
    return {"Path": path, "RefType": "node_field"}


def map_file_uri_selector(
    selector: list[str] | None,
    code_map: dict[str, str],
    type_map: dict[str, str],
    start_var_types: dict[str, str],
) -> dict[str, Any]:
    mapped = map_selector(selector, code_map, type_map, start_var_types)
    selector = selector or []
    if len(selector) >= 2 and str(selector[0]) in code_map and type_map.get(str(selector[0])) == "start":
        var_name = str(selector[1])
        var_type = start_var_types.get(var_name)
        if var_type == "file":
            mapped["Path"] = f"{var_name}.url"
        elif var_type == "file-list":
            mapped["Path"] = f"{var_name}[0].url"
    return mapped


def parse_dify_template_ref(value: Any) -> list[str] | None:
    text = str(value or "")
    match = re.fullmatch(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.*\[\]-]+)#\}\}", text.strip())
    if not match:
        return None
    return [match.group(1), match.group(2)]


def schema_from_outputs(outputs: dict[str, Any] | None) -> list[dict[str, Any]]:
    schema = []
    for name, spec in (outputs or {}).items():
        schema.append({"Name": name, "Type": hi_type((spec or {}).get("type"))})
    return schema


def wrap_code_for_hiagent(code: str, variables: list[dict[str, Any]]) -> str:
    names = [item.get("variable") for item in variables or [] if item.get("variable")]
    if "def handler(" in code:
        return code
    if "def main(" not in code:
        return (
            "# 原始 Dify 代码未声明 main(...)，这里保留原文供导入后人工调整。\n"
            + code
            + "\n\n"
            + "def handler(params):\n"
            + "    return {}\n"
        )
    code = re.sub(r"(^\s*)def\s+main\s*\(", r"\1def dify_main(", code, count=1, flags=re.M)
    args = ", ".join(f"{name}=params.get({name!r})" for name in names)
    return (
        "# 方法定义不能修改\n"
        "# 以下代码由 Dify Code 节点转换而来，并自动桥接 HiAgent 的 handler(params) 入口。\n"
        + code.rstrip()
        + "\n\n"
        + "def handler(params):\n"
        + f"    return dify_main({args})\n"
    )


def llm_prompt_configs(
    data: dict[str, Any],
    code_map: dict[str, str],
    type_map: dict[str, str],
) -> tuple[Any, Any, list[dict[str, Any]]]:
    prompts = data.get("prompt_template") or []
    system_prompt = "\n\n".join(convert_template(p.get("text", "")) for p in prompts if p.get("role") == "system")
    user_prompt = "\n\n".join(convert_template(p.get("text", "")) for p in prompts if p.get("role") == "user")

    prompt_variables = []
    original_prompt_text = "\n".join(p.get("text", "") for p in prompts)
    for match in re.finditer(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.*\[\]-]+)#\}\}", original_prompt_text):
        source, path = match.group(1), match.group(2)
        mapped = map_selector([source, path], code_map, type_map, {})
        mapped["Name"] = f"{source}_{path}"
        prompt_variables.append(mapped)

    return system_prompt, user_prompt, prompt_variables


def default_knowledge_output_schema() -> list[dict[str, Any]]:
    return [
        {
            "Name": "outputList",
            "Required": True,
            "SubParameters": [
                {"Name": "output", "Required": True, "Type": 0},
                {
                    "Name": "metadata",
                    "Required": True,
                    "SubParameters": [
                        {"Name": "score", "Required": True, "Type": 3},
                        {"Name": "dataset_id", "Required": True, "Type": 0},
                        {"Name": "segment_id", "Required": True, "Type": 0},
                        {"Name": "document_id", "Required": True, "Type": 0},
                        {"Name": "dataset_name", "Required": True, "Type": 0},
                        {"Name": "document_name", "Required": True, "Type": 0},
                    ],
                    "Type": 4,
                },
            ],
            "Type": 9,
        },
        {"Name": "filter", "Type": 0},
    ]


def template_tool_catalog(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog = {}
    for node in template.get("Nodes") or []:
        if node.get("Type") != "Tool":
            continue
        cfg = (node.get("Configs") or {}).get("Tool") or {}
        tool_name = cfg.get("ToolName")
        if tool_name:
            catalog[tool_name] = copy.deepcopy(cfg)
    return catalog


def add_tool_dependency(hiagent: dict[str, Any], template: dict[str, Any], tool_cfg: dict[str, Any]) -> None:
    tool_id = tool_cfg.get("ToolID")
    plugin_name = tool_cfg.get("ToolPluginName")
    plugin_type = tool_cfg.get("ToolPluginType")
    tool_map = (template.get("Depends") or {}).get("ToolMap") or {}
    plugin_map = (template.get("Depends") or {}).get("PluginMap") or {}
    for key, value in tool_map.items():
        if key == tool_id or value.get("Name") == tool_cfg.get("ToolName"):
            hiagent["Depends"].setdefault("ToolMap", {})[key] = copy.deepcopy(value)
            plugin_id = value.get("PluginID")
            if plugin_id and plugin_id in plugin_map:
                hiagent["Depends"].setdefault("PluginMap", {})[plugin_id] = copy.deepcopy(plugin_map[plugin_id])
    for key, value in plugin_map.items():
        if value.get("Name") == plugin_name or value.get("PluginUniqCode", "").endswith(f":{plugin_name}"):
            hiagent["Depends"].setdefault("PluginMap", {})[key] = copy.deepcopy(value)


def parent_ref_for_path(hi_node: dict[str, Any], path: str) -> dict[str, Any] | None:
    for dep in hi_node.get("Depends") or []:
        code = dep.get("NodeCode")
        if code:
            return {"NodeCode": code, "Path": path, "RefType": "node_field"}
    return None


def build_tool_node(
    hi_node: dict[str, Any],
    tool_cfg: dict[str, Any],
    input_variables: list[dict[str, Any]],
    template: dict[str, Any],
    hiagent: dict[str, Any],
) -> dict[str, Any]:
    cfg = copy.deepcopy(tool_cfg)
    cfg["InputVariables"] = input_variables
    hi_node["Type"] = "Tool"
    hi_node["Configs"]["Tool"] = cfg
    depends = []
    seen = set()
    for dep in hi_node.get("Depends") or []:
        code = dep.get("NodeCode")
        if code and code not in seen:
            depends.append({"NodeCode": code})
            seen.add(code)
    for item in input_variables:
        code = item.get("NodeCode")
        if code and code not in seen:
            depends.append({"NodeCode": code})
            seen.add(code)
    if depends:
        hi_node["Depends"] = depends
    add_tool_dependency(hiagent, template, cfg)
    return hi_node

def convert(
    dify: dict[str, Any],
    template: dict[str, Any] | None,
    fallback_model_id: str,
    fallback_model_name: str,
) -> tuple[dict[str, Any], list[str]]:
    graph = dify["workflow"]["graph"]
    nodes = graph.get("nodes", [])
    parents = upstream_map(dify)
    lookup = node_lookup(dify)
    code_map = {str(node["id"]): stable_code(str(node["id"])) for node in nodes}
    type_map = {str(node["id"]): node["data"]["type"] for node in nodes}
    start_node = next((node for node in nodes if node["data"].get("type") == "start"), None)
    start_var_types = {var.get("variable", ""): var.get("type", "") for var in ((start_node or {}).get("data") or {}).get("variables", [])}
    report: list[str] = []

    template = template or {}
    tool_catalog = template_tool_catalog(template)
    model_map = copy.deepcopy((template.get("Depends") or {}).get("ModelMap") or {})
    if model_map:
        model_id, model_info = next(iter(model_map.items()))
        model_name = model_info.get("Name", "")
        model_features = ["streaming", "tool-call", "vision", "reasoning", "reasoning-effort", "reasoning-switch"]
    else:
        model_id = fallback_model_id
        model_name = fallback_model_name
        model_map = {model_id: {"Desc": "", "ID": model_id, "LogoPath": "", "Name": model_name}}
        model_features = ["streaming"]

    app = dify.get("app") or {}
    flow_id = stable_code(app.get("name") or "dify_workflow")
    hiagent = {
        "DLVersion": "v2",
        "Depends": {
            "AppMap": {},
            "DataSourceMap": {},
            "DatabaseMap": {},
            "KnowledgeMap": {},
            "ModelMap": model_map,
            "PluginMap": {},
            "QADataSetMap": {},
            "TermDatasetMap": {},
            "ToolMap": {},
            "WorkflowMap": {},
        },
        "Desc": app.get("description", ""),
        "DisplayName": app.get("name", "Dify 转换工作流"),
        "FlowType": "Workflow",
        "ID": flow_id,
        "LogoPath": "",
        "MetaType": "Workflow",
        "Nodes": [],
        "UniqueName": flow_id,
        "UpdatedAt": 0,
        "VersionCode": "-",
        "VersionName": "-",
    }

    for node in nodes:
        node_id = str(node["id"])
        data = node["data"]
        dify_type = data["type"]
        hi_code = code_map[node_id]
        pos = node.get("position") or {}
        hi_node: dict[str, Any] = {
            "Code": hi_code,
            "Configs": {},
            "ErrorConfig": {"ErrorConfigType": "None"},
            "ID": hi_code,
            "Layout": {"X": float(pos.get("x", 0)), "Y": float(pos.get("y", 0))},
            "Name": data.get("title") or node_id,
        }
        if parents.get(node_id):
            hi_node["Depends"] = [{"NodeCode": code_map[str(parent)]} for parent in parents[node_id]]

        if dify_type == "start":
            schema = [start_schema_item(var) for var in data.get("variables", [])]
            hi_node["Type"] = "Start"
            hi_node["Name"] = "Start"
            hi_node["Configs"]["Start"] = {"InputSchema": schema, "OutputSchema": copy.deepcopy(schema)}

        elif dify_type == "end":
            inputs = []
            for output in data.get("outputs", []):
                selector = output.get("value_selector") or []
                mapped = map_selector(selector, code_map, type_map, start_var_types)
                mapped["Name"] = output.get("variable", "")
                inputs.append(mapped)
            hi_node["Type"] = "End"
            hi_node["Name"] = "End"
            hi_node["Configs"]["End"] = {"InputVariables": inputs, "OutputType": "Variable"}

        elif dify_type == "llm":
            params = ((data.get("model") or {}).get("completion_params") or {})
            system_prompt, prompt, prompt_vars = llm_prompt_configs(data, code_map, type_map)
            input_vars = []
            seen = set()
            for var in data.get("variables") or []:
                name = var.get("variable")
                if not name:
                    continue
                mapped = map_selector(var.get("value_selector"), code_map, type_map, start_var_types)
                mapped["Name"] = name
                input_vars.append(mapped)
                seen.add(name)
            for item in prompt_vars:
                if item["Name"] not in seen:
                    input_vars.append(item)
                    seen.add(item["Name"])
            hi_node["Type"] = "LLM"
            hi_node["Configs"]["LLM"] = {
                "CurrentTimeEnabled": False,
                "EnableChatHistories": False,
                "InputVariables": input_vars,
                "MaxTokens": int(params.get("max_tokens") or 8192),
                "ModelFeatureList": model_features,
                "ModelID": model_id,
                "ModelInteractiveMode": "direct",
                "ModelName": model_name,
                "OutputFormat": "json",
                "OutputSchema": [{"Name": "raw_output", "Type": 0}],
                "Prompt": prompt,
                "PromptConfig": None,
                "ReasoningEffortType": "medium",
                "ReasoningMode": True,
                "ReasoningSwitch": None,
                "ReasoningSwitchType": "enabled",
                "Retries": 0,
                "SystemPrompt": system_prompt,
                "SystemPromptConfig": None,
                "Temperature": float(params.get("temperature", 0.7)),
                "TimeoutSeconds": 120,
                "TopP": float(params.get("top_p", 0.9)),
            }
            source_model = (data.get("model") or {}).get("name")
            if source_model and source_model != model_name:
                report.append(f"LLM 节点「{hi_node['Name']}」原模型 {source_model} 已映射为 HiAgent 占位模型 {model_name}。")
            report.append(f"LLM 节点「{hi_node['Name']}」已按 HiAgent 样例写入 Prompt/SystemPrompt，导入后请检查提示词变量是否正确绑定。")

        elif dify_type == "code":
            input_vars = []
            for var in data.get("variables") or []:
                mapped = map_selector(var.get("value_selector"), code_map, type_map, start_var_types)
                mapped["Name"] = var.get("variable", "")
                input_vars.append(mapped)
            hi_node["Type"] = "Code"
            hi_node["Configs"]["Code"] = {
                "Code": wrap_code_for_hiagent(data.get("code", ""), data.get("variables") or []),
                "InputVariables": input_vars,
                "Language": 1 if str(data.get("code_language", "")).startswith("python") else 0,
                "OutputSchema": schema_from_outputs(data.get("outputs")),
                "Retries": 0,
                "TimeoutSeconds": 120,
            }

        elif dify_type == "assigner":
            input_vars = []
            output_schema = []
            assignments = []
            for index, item in enumerate(data.get("items") or [], 1):
                target = item.get("variable_selector") or []
                output_name = str(target[-1]) if target else f"value_{index}"
                value_selector = item.get("value") if item.get("input_type") == "variable" else None
                input_name = f"input_{output_name}"
                mapped = map_selector(value_selector, code_map, type_map, start_var_types) if value_selector else {"RefType": "node_field"}
                mapped["Name"] = input_name
                input_vars.append(mapped)
                output_schema.append({"Name": output_name, "Type": 0})
                assignments.append((output_name, input_name))
            lines = [
                "# Dify assigner 节点转换为透传 Code 节点。",
                "def handler(params):",
                "    return {",
            ]
            for output_name, input_name in assignments:
                lines.append(f"        {output_name!r}: params.get({input_name!r}),")
            lines.extend(["    }", ""])
            hi_node["Type"] = "Code"
            hi_node["Configs"]["Code"] = {
                "Code": "\n".join(lines),
                "InputVariables": input_vars,
                "Language": 1,
                "OutputSchema": output_schema or [{"Name": "output", "Type": 0}],
                "Retries": 0,
                "TimeoutSeconds": 120,
            }
            report.append(f"变量赋值节点「{hi_node['Name']}」已转换为透传 Code 节点；如需 HiAgent 会话变量语义，请导入后复核。")

        elif dify_type == "document-extractor":
            tool_cfg = tool_catalog.get("convert_to_markdown")
            if tool_cfg:
                uri = map_file_uri_selector(data.get("variable_selector"), code_map, type_map, start_var_types)
                uri["Name"] = "uri"
                hi_node = build_tool_node(hi_node, tool_cfg, [uri], template, hiagent)
                if data.get("is_array_file"):
                    report.append(f"文档提取节点「{hi_node['Name']}」已映射为 convert_to_markdown；原 Dify 为 file-list，当前默认取首个文件 URL。")
                else:
                    report.append(f"文档提取节点「{hi_node['Name']}」已映射为 HiAgent convert_to_markdown 插件。")
            else:
                hi_node["Type"] = "Code"
                hi_node["Configs"]["Code"] = {
                    "Code": (
                        "# 模板中未找到 convert_to_markdown 工具，导入后请人工替换。\n"
                        "def handler(params):\n"
                        "    return {'unsupported_type': 'document-extractor'}\n"
                    ),
                    "InputVariables": [],
                    "Language": 1,
                    "OutputSchema": [{"Name": "unsupported_type", "Type": 0}],
                    "Retries": 0,
                    "TimeoutSeconds": 120,
                }
                report.append(f"节点「{hi_node['Name']}」类型 document-extractor 未找到 convert_to_markdown 模板，已转为占位 Code 节点。")

        elif dify_type == "tool" and data.get("tool_name") in DIFY_TOOL_NAME_MAP:
            mapping = DIFY_TOOL_NAME_MAP[data.get("tool_name")]
            hi_tool_name = mapping["hiagent_tool"]
            tool_cfg = tool_catalog.get(hi_tool_name)
            if tool_cfg:
                input_variables = []
                tool_parameters = data.get("tool_parameters") or {}
                for dify_param, hi_param in mapping["params"].items():
                    raw = (tool_parameters.get(dify_param) or {}).get("value")
                    selector = parse_dify_template_ref(raw)
                    if selector and selector[0] == "conversation":
                        mapped = parent_ref_for_path(hi_node, selector[1]) or {"Path": selector[1], "RefType": "node_field"}
                    else:
                        mapped = map_selector(selector, code_map, type_map, start_var_types) if selector else {"RefType": "node_field"}
                    mapped["Name"] = hi_param
                    input_variables.append(mapped)
                hi_node = build_tool_node(hi_node, tool_cfg, input_variables, template, hiagent)
                report.append(f"工具节点「{hi_node['Name']}」已由 Dify {data.get('tool_name')} 映射为 HiAgent {hi_tool_name} 插件。")
            else:
                hi_node["Type"] = "Code"
                hi_node["Configs"]["Code"] = {
                    "Code": (
                        f"# 模板中未找到 {hi_tool_name} 工具，导入后请人工替换。\n"
                        "def handler(params):\n"
                        f"    return {{'unsupported_type': 'tool:{data.get('tool_name')}'}}\n"
                    ),
                    "InputVariables": [],
                    "Language": 1,
                    "OutputSchema": [{"Name": "unsupported_type", "Type": 0}],
                    "Retries": 0,
                    "TimeoutSeconds": 120,
                }
                report.append(f"工具节点「{hi_node['Name']}」识别为 {data.get('tool_name')}，但模板中未找到 {hi_tool_name}，已转为占位 Code 节点。")

        elif dify_type == "knowledge-retrieval":
            config = data.get("multiple_retrieval_config") or {}
            query_variable = map_selector(data.get("query_variable_selector"), code_map, type_map, start_var_types)
            query_variable["Name"] = "query"
            score = config.get("score_threshold")
            hi_node["Type"] = "Knowledge"
            hi_node["Configs"]["Knowledge"] = {
                "ConfigVersion": 2,
                "ContextComponents": ["id", "content"],
                "Expand": False,
                "ExpandNum": None,
                "KnowledgeRange": [],
                "OutputSchema": default_knowledge_output_schema(),
                "QueryVariable": query_variable,
                "RerankID": None,
                "RetrievalSearchMethod": 0,
                "ScoreThreshold": 0.5 if score is None else float(score),
                "TopK": int(config.get("top_k") or 3),
            }
            report.append(f"知识库节点「{hi_node['Name']}」需要在 HiAgent 导入后绑定 KnowledgeRange；Dify dataset_ids={data.get('dataset_ids') or []}。")

        else:
            hi_node["Type"] = "Code"
            hi_node["Configs"]["Code"] = {
                "Code": (
                    "# 未自动支持的 Dify 节点类型，导入后请人工替换。\n"
                    "def handler(params):\n"
                    f"    return {{'unsupported_type': {dify_type!r}}}\n"
                ),
                "InputVariables": [],
                "Language": 1,
                "OutputSchema": [{"Name": "unsupported_type", "Type": 0}],
                "Retries": 0,
                "TimeoutSeconds": 120,
            }
            report.append(f"节点「{hi_node['Name']}」类型 {dify_type} 未自动支持，已转为占位 Code 节点。")

        hiagent["Nodes"].append(hi_node)

    if not any(node.get("Type") == "End" for node in hiagent["Nodes"]):
        source_ids = {str(edge.get("source")) for edge in graph.get("edges", [])}
        terminal_ids = [str(node.get("id")) for node in nodes if str(node.get("id")) not in source_ids]
        terminal_codes = [code_map[node_id] for node_id in terminal_ids if node_id in code_map]
        if terminal_codes:
            end_code = stable_code("synthetic_end")
            hiagent["Nodes"].append({
                "Code": end_code,
                "Configs": {
                    "End": {
                        "InputVariables": [
                            {"Name": f"output_{index + 1}", "NodeCode": code, "Path": "output", "RefType": "node_field"}
                            for index, code in enumerate(terminal_codes)
                        ],
                        "OutputType": "Variable",
                    }
                },
                "Depends": [{"NodeCode": code} for code in terminal_codes],
                "ErrorConfig": {"ErrorConfigType": "None"},
                "ID": end_code,
                "Layout": {"X": 2600.0, "Y": 420.0},
                "Name": "End",
                "Type": "End",
            })
            report.append("原 Dify 应用未包含标准 end 节点；已追加一个合成 End 节点用于 HiAgent 工作流导入。")

    return hiagent, report


def dump_yaml(data: dict[str, Any]) -> str:
    yaml = import_yaml()

    class LiteralDumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    def str_representer(dumper, value: str):
        if "\n" in value:
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", value)

    LiteralDumper.add_representer(str, str_representer)
    return yaml.dump(data, Dumper=LiteralDumper, allow_unicode=True, sort_keys=False, width=120)


def write_report(path: Path, hiagent: dict[str, Any], report: list[str]) -> None:
    node_lines = []
    for node in hiagent["Nodes"]:
        depends = ", ".join(dep["NodeCode"] for dep in node.get("Depends", [])) or "-"
        node_lines.append(f"- {node['Name']}：{node['Type']}，Code={node['Code']}，Depends={depends}")
    text = "\n".join(
        [
            "# Dify 到 HiAgent 转换报告",
            "",
            f"- 工作流：{hiagent.get('DisplayName')}",
            f"- 节点数：{len(hiagent.get('Nodes', []))}",
            "",
            "## 需要导入后检查",
            "",
            *[f"- {item}" for item in report],
            "",
            "## 节点清单",
            "",
            *node_lines,
            "",
            "## 说明",
            "",
            "- Dify 的知识库 dataset_id 无法直接转换为 HiAgent 的 KnowledgeRange，需要在目标工作空间重新绑定。",
            "- Dify 模型名无法直接转换为 HiAgent 模型 ID，本次使用示例 HiAgent 文件中的模型作为占位。",
            "- LLM 提示词已按 HiAgent LLM 样例写入 Prompt/SystemPrompt；导入后仍建议打开节点检查变量绑定。",
            "- Dify 代码节点已包装为 HiAgent `handler(params)` 入口，导入后建议逐个单节点调试。",
        ]
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--template", type=Path, help="Optional HiAgent export YAML used to copy ModelMap/model IDs.")
    parser.add_argument("--model-id", default="REPLACE_WITH_HIAGENT_MODEL_ID")
    parser.add_argument("--model-name", default="REPLACE_WITH_HIAGENT_MODEL_NAME")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    dify = load_yaml(args.input)
    template = load_yaml(args.template) if args.template else None
    hiagent, report = convert(dify, template, args.model_id, args.model_name)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dump_yaml(hiagent), encoding="utf-8")
    write_report(args.report, hiagent, report)
    print(json.dumps({"output": str(args.output), "report": str(args.report), "nodes": len(hiagent["Nodes"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
