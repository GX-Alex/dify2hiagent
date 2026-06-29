#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import struct
import re
import time
import zipfile
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

CONDITION_OPERATOR_MAP = {
    "=": "EQ",
    "==": "EQ",
    "eq": "EQ",
    "equal": "EQ",
    "equals": "EQ",
    "is": "EQ",
    "!=": "NE",
    "≠": "NE",
    "ne": "NE",
    "not equal": "NE",
    "not equals": "NE",
    "is not": "NE",
    "contains": "CONTAINS",
    "contain": "CONTAINS",
    "not contains": "NOT_CONTAINS",
    "does not contain": "NOT_CONTAINS",
    "empty": "EMPTY",
    "is empty": "EMPTY",
    "not empty": "NOT_EMPTY",
    "is not empty": "NOT_EMPTY",
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


def parse_template_default_expr(expr: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+or\s+(.+)", expr.strip())
    if not match:
        return None
    name, raw_default = match.group(1), match.group(2).strip()
    try:
        default = ast.literal_eval(raw_default)
    except Exception:
        default = raw_default.strip("'\"")
    return name, "" if default is None else str(default)


def template_transform_defaults(text: str) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for match in re.finditer(r"\{\{\s*(.*?)\s*\}\}", text or ""):
        parsed = parse_template_default_expr(match.group(1))
        if parsed and parsed[0] not in defaults:
            defaults[parsed[0]] = parsed[1]
    return defaults


def convert_template_transform_text(text: str) -> str:
    """Normalize Dify/Jinja variables for HiAgent TextProcessing concat templates."""
    def replace(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            return "{{" + expr + "}}"
        parsed = parse_template_default_expr(expr)
        if parsed:
            return "{{" + parsed[0] + "}}"
        return match.group(0)

    return re.sub(r"\{\{\s*(.*?)\s*\}\}", replace, text or "")


def has_complex_template_logic(text: str) -> bool:
    if "{%" in (text or "") or "{#" in (text or ""):
        return True
    for match in re.finditer(r"\{\{\s*(.*?)\s*\}\}", text or ""):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", match.group(1).strip()):
            return True
    return False


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


def load_hiagent_template(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    if path.suffix.lower() != ".zip":
        return load_yaml(path)
    yaml = import_yaml()
    with zipfile.ZipFile(path) as zf:
        index = yaml.safe_load(zf.read("index.yaml").decode("utf-8"))
        main_name = index.get("MainMetaName")
        agent_names = [name for name in zf.namelist() if name.startswith("agent/") and name.endswith((".yaml", ".yml"))]
        preferred = f"agent/{main_name}.yaml" if main_name else None
        selected = preferred if preferred in agent_names else (agent_names[0] if agent_names else None)
        if not selected:
            raise SystemExit(f"No agent YAML found in HiAgent zip template: {path}")
        return yaml.safe_load(zf.read(selected).decode("utf-8"))


def is_dify_chatflow(dify: dict[str, Any]) -> bool:
    mode = str((dify.get("app") or {}).get("mode") or "").lower()
    return mode in {"advanced-chat", "chatflow", "chat-flow"}


def chat_start_schema(dify_start_schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    schema = [
        {
            "Desc": "用户输入的原始内容",
            "DescEn": "query",
            "DescJa": "ユーザーが入力したオリジナルコンテンツ",
            "DescZhHans": "用户输入的原始内容",
            "DescZhHant": "用戶輸入的原始內容",
            "Name": "query",
            "Required": True,
            "Type": 0,
        },
        {
            "Desc": "对话附件",
            "DescEn": "files",
            "DescJa": "対話添付ファイル",
            "DescZhHans": "对话附件",
            "DescZhHant": "對話附件",
            "Name": "files",
            "SubParameters": [
                {"Desc": "文件名", "DescEn": "name", "DescZhHans": "文件名", "DescZhHant": "文件名", "Name": "name", "Required": True, "Type": 0},
                {"Desc": "文件链接", "DescEn": "url", "DescZhHans": "文件链接", "DescZhHant": "文件鏈接", "Name": "url", "Required": True, "Type": 0},
            ],
            "Type": 11,
        },
        {
            "Desc": "用户与应用的对话历史",
            "DescEn": "chat history",
            "DescJa": "ユーザーとアプリケーションの対話履歴",
            "DescZhHans": "用户与应用的对话历史",
            "DescZhHant": "用戶與應用的對話歷史",
            "Name": "chat_histories",
            "SubParameters": [
                {"Desc": "历史对话问题", "DescEn": "query", "DescZhHans": "历史对话问题", "DescZhHant": "歷史對話問題", "Name": "query", "Type": 0},
                {"Desc": "历史对话回答", "DescEn": "answer", "DescZhHans": "历史对话回答", "DescZhHant": "歷史對話回答", "Name": "answer", "Type": 0},
                {
                    "Desc": "对话附件",
                    "DescEn": "files",
                    "DescJa": "対話添付ファイル",
                    "DescZhHans": "对话附件",
                    "DescZhHant": "對話附件",
                    "Name": "files",
                    "SubParameters": [
                        {"Desc": "文件名", "DescEn": "name", "DescZhHans": "文件名", "DescZhHant": "文件名", "Name": "name", "Required": True, "Type": 0},
                        {"Desc": "文件链接", "DescEn": "url", "DescZhHans": "文件链接", "DescZhHant": "文件鏈接", "Name": "url", "Required": True, "Type": 0},
                    ],
                    "Type": 11,
                },
            ],
            "Type": 9,
        },
    ]
    existing = {item["Name"] for item in schema}
    for item in dify_start_schema:
        if item.get("Name") not in existing:
            schema.append(item)
    return schema


def default_chat_advanced_config() -> dict[str, Any]:
    return {
        "AdvancedReviewType": "unused",
        "FeedbackTagConfig": {
            "DislikeTags": ["没有帮助", "知识过时", "问题理解错误", "事实错误", "回答不准确", "内容有害/不健康", "前后回复不一致"],
            "Enabled": True,
            "LikeTags": None,
        },
        "OpeningConfig": {"OpeningEnabled": False},
        "ReferenceEnabled": False,
        "ReviewEnabled": False,
        "SpeechInteractionConfig": {},
        "SuggestEnabled": True,
        "SuggestPromptConfig": {"Enabled": False, "Prompt": ""},
        "ThoughtLanguageConfig": {"Language": "zh"},
        "UploadConfig": {
            "Enabled": True,
            "UploadAudioAllowed": True,
            "UploadCompressedAllowed": False,
            "UploadDocumentAllowed": True,
            "UploadImageAllowed": True,
            "UploadOtherAllowed": False,
            "UploadVideoAllowed": True,
        },
    }


def agent_template_parts(agent_template: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    if not agent_template:
        return default_chat_advanced_config(), ""
    single = ((agent_template.get("AppConfig") or {}).get("SingleAgentConfig") or {})
    chatflow_cfg = single.get("ChatFlowConfig") or {}
    chat_advanced = chatflow_cfg.get("ChatAdvancedConfig") or single.get("ChatAdvancedConfig") or default_chat_advanced_config()
    workspace_id = (agent_template.get("AppInfo") or {}).get("WorkspaceID") or (agent_template.get("AppConfig") or {}).get("WorkspaceID") or ""
    return copy.deepcopy(chat_advanced), workspace_id


def wrap_chatflow_agent(hiagent: dict[str, Any], dify: dict[str, Any], agent_template: dict[str, Any] | None = None) -> dict[str, Any]:
    app = dify.get("app") or {}
    app_id = stable_code((app.get("name") or "dify_chatflow") + ":agent")
    workflow_id = hiagent["ID"]
    now_ms = int(time.time() * 1000)
    chat_advanced, workspace_id = agent_template_parts(agent_template)
    app_depends = copy.deepcopy(hiagent.get("Depends") or {})
    for value in app_depends.values():
        if isinstance(value, dict):
            for item in value.values():
                if isinstance(item, dict):
                    item.setdefault("SourceTypes", ["Workflow"])
    single_config = {
        "A2aAgentIDs": [],
        "AgentIDs": [],
        "ChatAdvancedConfig": copy.deepcopy(chat_advanced),
        "ChatFlowConfig": {
            "ChatAdvancedConfig": copy.deepcopy(chat_advanced),
            "RoundsReserved": 3,
            "UserVariableConfigs": [{"Description": "对话输出", "Name": "output", "Scope": "Agent"}],
            "Version": "v1",
            "VersionDescription": app.get("description") or "Converted from Dify chatflow",
            "WorkflowID": workflow_id,
            "WorkflowPublishID": workflow_id,
        },
        "DatabaseIDs": [],
        "GraphIDs": [],
        "KnowledgeIDs": [],
        "ModelID": "",
        "ModelName": "",
        "PrePrompt": "",
        "PromptConfig": {"PromptMode": "regex"},
        "QADatasetIDs": [],
        "SummaryModelID": "",
        "SummaryModelName": "",
        "TerminologyIDs": [],
        "ToolIDs": [],
        "UpdateTime": "",
        "VariableConfigs": [],
        "Version": "v1",
        "VersionDescription": app.get("description") or "Converted from Dify chatflow",
        "WorkflowIDs": [],
    }
    return {
        "AppConfig": {
            "AgentMode": "",
            "AppID": app_id,
            "ChatFlowDetail": hiagent,
            "MultiAgentConfig": None,
            "SingleAgentConfig": single_config,
            "WorkspaceID": workspace_id,
        },
        "AppDepends": app_depends,
        "AppInfo": {"AgentMode": "", "AppID": app_id, "AppType": "ChatFlow", "WorkspaceID": workspace_id},
        "DLVersion": "0.0.1",
        "Desc": app.get("description", ""),
        "DisplayName": app.get("name", "Dify 转换对话型工作流"),
        "LogoPath": "",
        "MetaType": "Agent",
        "UniqueName": app_id,
        "UpdatedAt": now_ms,
        "VersionCode": stable_code((app.get("name") or "dify_chatflow") + ":version"),
        "VersionName": "v1",
    }


def zip_trailing_signature(path: Path | None) -> bytes:
    if not path or path.suffix.lower() != ".zip" or not path.exists():
        return b""
    data = path.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return b""
    comment_len = struct.unpack_from("<H", data, eocd + 20)[0]
    start = eocd + 22 + comment_len
    return data[start:]


def default_zip_trailing_signature(agent: dict[str, Any]) -> bytes:
    seed = (agent.get("UniqueName") or agent.get("DisplayName") or "dify2hiagent").encode("utf-8")
    return hashlib.md5(seed).hexdigest().encode("ascii")


def write_chatflow_zip(path: Path, agent: dict[str, Any], trailing_signature: bytes = b"") -> None:
    app_name = agent.get("DisplayName") or "Dify 转换对话型工作流"
    index = {
        "DLVersion": "0.0.1",
        "FromWorkspaceID": (agent.get("AppInfo") or {}).get("WorkspaceID") or "",
        "MainMeta": "Agent",
        "MainMetaName": app_name,
        "MainUniqueName": agent.get("UniqueName"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yaml", dump_yaml(index))
        zf.writestr(f"agent/{app_name}.yaml", dump_yaml(agent))
    signature = trailing_signature or default_zip_trailing_signature(agent)
    if signature:
        with path.open("ab") as f:
            f.write(signature)


def report_workflow(artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact.get("MetaType") == "Agent":
        return ((artifact.get("AppConfig") or {}).get("ChatFlowDetail") or {})
    return artifact


def node_lookup(dify: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in dify["workflow"]["graph"]["nodes"]}


def upstream_map(dify: dict[str, Any]) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for edge in dify["workflow"]["graph"].get("edges", []):
        parents[edge["target"]].append(edge["source"])
    return parents


def incoming_edge_map(dify: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in dify["workflow"]["graph"].get("edges", []):
        incoming[str(edge.get("target"))].append(edge)
    return incoming


def condition_port_map(nodes: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for node in nodes:
        data = node.get("data") or {}
        if data.get("type") != "if-else":
            continue
        mapping = {"false": "else", "else": "else"}
        for index, case in enumerate(data.get("cases") or [], 1):
            port_id = f"if{index:02d}"
            for key in (case.get("case_id"), case.get("id")):
                if key is not None:
                    mapping[str(key)] = port_id
            if index == 1:
                mapping.setdefault("true", port_id)
        result[str(node.get("id"))] = mapping
    return result


def node_depends_from_edges(
    node_id: str,
    incoming_edges: dict[str, list[dict[str, Any]]],
    code_map: dict[str, str],
    type_map: dict[str, str],
    port_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    depends = []
    seen: set[tuple[str, str | None]] = set()
    for edge in incoming_edges.get(str(node_id), []):
        source = str(edge.get("source"))
        code = code_map.get(source)
        if not code:
            continue
        dep: dict[str, Any] = {"NodeCode": code}
        if type_map.get(source) == "if-else":
            handle = str(edge.get("sourceHandle") or "")
            port = port_map.get(source, {}).get(handle)
            if not port and handle in {"false", "else"}:
                port = "else"
            elif not port and handle and handle not in {"source", "target"}:
                port = handle
            if port:
                dep["PortID"] = port
        key = (code, dep.get("PortID"))
        if key not in seen:
            depends.append(dep)
            seen.add(key)
    return depends


def map_selector(
    selector: list[str] | None,
    code_map: dict[str, str],
    type_map: dict[str, str],
    start_var_types: dict[str, str] | None = None,
    conversation_refs: dict[tuple[str, str], dict[str, Any]] | None = None,
    current_node_id: str | None = None,
) -> dict[str, Any]:
    selector = selector or []
    if not selector:
        return {"RefType": "node_field"}

    source = str(selector[0])
    path = ".".join(str(x) for x in selector[1:]) if len(selector) > 1 else ""
    if source == "conversation":
        resolved = (conversation_refs or {}).get((str(current_node_id), path))
        if resolved:
            return copy.deepcopy(resolved)
        return {"Path": path, "RefType": "node_field"}
    if source == "sys" and "sys" in code_map:
        return {"NodeCode": code_map["sys"], "Path": path, "RefType": "node_field"}
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


def dify_template_refs(text: str) -> list[tuple[str, list[str]]]:
    refs = []
    for match in re.finditer(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.*\[\]-]+)#\}\}", text or ""):
        refs.append((match.group(0), [match.group(1), match.group(2)]))
    return refs


def build_answer_code(template: str, replacements: list[tuple[str, str]]) -> str:
    lines = [
        "# Dify answer 节点转换：渲染回答模板并输出给 HiAgent 对话型 End。",
        f"ANSWER_TEMPLATE = {template!r}",
        "",
        "def _stringify(value):",
        "    if value is None:",
        "        return ''",
        "    if isinstance(value, list):",
        "        return '\\n'.join(_stringify(item) for item in value)",
        "    if isinstance(value, dict):",
        "        if 'url' in value:",
        "            return str(value.get('url') or '')",
        "        if 'path' in value:",
        "            return str(value.get('path') or '')",
        "        return str(value)",
        "    return str(value)",
        "",
        "def handler(params):",
        "    output = ANSWER_TEMPLATE",
    ]
    for token, name in replacements:
        lines.append(f"    output = output.replace({token!r}, _stringify(params.get({name!r})))")
    lines.extend(["    return {'output': output}", ""])
    return "\n".join(lines)


def assigner_output_names(node: dict[str, Any]) -> set[str]:
    names = set()
    data = node.get("data") or {}
    if data.get("type") != "assigner":
        return names
    for item in data.get("items") or []:
        target = item.get("variable_selector") or []
        if len(target) >= 2 and str(target[0]) == "conversation":
            names.add(str(target[-1]))
    return names


def build_conversation_refs(
    nodes: list[dict[str, Any]],
    parents: dict[str, list[str]],
    code_map: dict[str, str],
) -> dict[tuple[str, str], dict[str, Any]]:
    assigner_outputs = {str(node["id"]): assigner_output_names(node) for node in nodes}
    variables = {name for names in assigner_outputs.values() for name in names}
    refs: dict[tuple[str, str], dict[str, Any]] = {}
    for node in nodes:
        node_id = str(node["id"])
        queue = [(parent, 1) for parent in parents.get(node_id, [])]
        seen: set[str] = set()
        distances: dict[str, int] = {}
        while queue:
            parent_id, distance = queue.pop(0)
            parent_id = str(parent_id)
            if parent_id in seen:
                continue
            seen.add(parent_id)
            distances[parent_id] = distance
            for grandparent in parents.get(parent_id, []):
                queue.append((grandparent, distance + 1))
        for variable in variables:
            candidates = [
                (distance, parent_id)
                for parent_id, distance in distances.items()
                if variable in assigner_outputs.get(parent_id, set())
            ]
            if candidates:
                _, source_id = min(candidates, key=lambda item: item[0])
                refs[(node_id, variable)] = {
                    "NodeCode": code_map[source_id],
                    "Path": variable,
                    "RefType": "node_field",
                }
    return refs


def infer_hi_type_from_value(value: Any) -> int:
    if isinstance(value, bool):
        return 2
    if isinstance(value, int) and not isinstance(value, bool):
        return 1
    if isinstance(value, float):
        return 3
    if isinstance(value, dict):
        return 4
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return 5
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            return 8
        return 9
    return 0


def condition_json_value(value: Any, var_type: str | None = None) -> str:
    if isinstance(value, str):
        type_name = str(var_type or "").lower()
        text = value.strip()
        if type_name in {"integer", "number"}:
            try:
                value = int(text) if type_name == "integer" else float(text)
            except ValueError:
                value = text
        elif type_name == "boolean":
            value = text.lower() in {"true", "1", "yes", "y", "是"}
    return json.dumps(value, ensure_ascii=False)


def map_condition_operator(raw_operator: str | None) -> tuple[str, str | None]:
    key = str(raw_operator or "is").strip().lower().replace("_", " ").replace("-", " ")
    operator = CONDITION_OPERATOR_MAP.get(key)
    if operator:
        return operator, None
    unsupported = raw_operator or ""
    return "EQ", f"条件操作符 {unsupported!r} 暂未在 HiAgent 选择器六类操作中找到等价项，已按等于导出，请导入后复核。"


def build_condition_node_config(
    data: dict[str, Any],
    code_map: dict[str, str],
    type_map: dict[str, str],
    start_var_types: dict[str, str],
    conversation_refs: dict[tuple[str, str], dict[str, Any]],
    current_node_id: str,
) -> tuple[dict[str, Any], list[str]]:
    branches = []
    warnings = []
    for index, case in enumerate(data.get("cases") or [], 1):
        conditions = []
        for condition in case.get("conditions") or []:
            operator, warning = map_condition_operator(condition.get("comparison_operator"))
            if warning:
                warnings.append(warning)
            left = map_selector(condition.get("variable_selector"), code_map, type_map, start_var_types, conversation_refs, current_node_id)
            left["Name"] = "Left"
            item: dict[str, Any] = {"Left": left, "Operator": operator}
            if operator in {"EMPTY", "NOT_EMPTY"}:
                item["Right"] = None
            elif condition.get("value_selector") or condition.get("target_selector"):
                right = map_selector(condition.get("value_selector") or condition.get("target_selector"), code_map, type_map, start_var_types, conversation_refs, current_node_id)
                right["Name"] = "Right"
                item["Right"] = right
            else:
                item["Right"] = {
                    "JsonValue": condition_json_value(condition.get("value", ""), condition.get("varType")),
                    "Name": "Right",
                    "RefType": "value",
                }
            conditions.append(item)
        branches.append({
            "ConditionLogic": "OR" if str(case.get("logical_operator") or "and").lower() == "or" else "AND",
            "Conditions": conditions,
            "ID": f"if{index:02d}",
        })
    return {"ElseBranch": {"ID": "else"}, "IfBranches": branches}, warnings


def unique_input_name(base: str, used: set[str]) -> str:
    name = re.sub(r"[^0-9A-Za-z_]", "_", base).strip("_") or "value"
    candidate = name
    index = 2
    while candidate in used:
        candidate = f"{name}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def build_template_default_code(assignments: list[dict[str, str]]) -> str:
    lines = [
        "# Dify template-transform 变量默认值预处理。",
        "def _coalesce(value, default):",
        "    if value is None or value == '':",
        "        return default",
        "    return str(value)",
        "",
        "def handler(params):",
        "    return {",
    ]
    for item in assignments:
        lines.append(
            f"        {item['output_name']!r}: _coalesce(params.get({item['input_name']!r}), {item['default']!r}),"
        )
    lines.extend(["    }", ""])
    return "\n".join(lines)


def build_assigner_code(assignments: list[dict[str, Any]]) -> str:
    lines = [
        "# Dify assigner 节点转换：复现变量赋值逻辑，并将赋值结果作为本节点输出传递给下游。",
        "def _append_value(current, value):",
        "    if isinstance(current, list):",
        "        return current + value if isinstance(value, list) else current + [value]",
        "    if current is None:",
        "        return value",
        "    if isinstance(current, str):",
        "        return current + ('' if value is None else str(value))",
        "    return [current] + value if isinstance(value, list) else [current, value]",
        "",
        "def _extend_value(current, value):",
        "    if isinstance(current, list):",
        "        return current + value if isinstance(value, list) else current + [value]",
        "    if isinstance(current, str):",
        "        return current + ('' if value is None else str(value))",
        "    return value if current is None else _append_value(current, value)",
        "",
        "def _apply_assign(operation, current, value):",
        "    op = str(operation or 'over-write').lower().replace('_', '-')",
        "    if op in ('over-write', 'overwrite', 'set', 'assign'):",
        "        return value",
        "    if op in ('append', 'append-item'):",
        "        return _append_value(current, value)",
        "    if op in ('extend', 'concat'):",
        "        return _extend_value(current, value)",
        "    if op in ('clear', 'reset'):",
        "        return None",
        "    return value",
        "",
        "def handler(params):",
        "    result = {}",
    ]
    for item in assignments:
        output_name = item["output_name"]
        operation = item["operation"]
        value_expr = item["value_expr"]
        current_expr = item["current_expr"]
        lines.append(f"    result[{output_name!r}] = _apply_assign({operation!r}, {current_expr}, {value_expr})")
    lines.extend(["    return result", ""])
    return "\n".join(lines)


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
    start_var_types: dict[str, str] | None = None,
    conversation_refs: dict[tuple[str, str], dict[str, Any]] | None = None,
    current_node_id: str | None = None,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    prompts = data.get("prompt_template") or []
    system_prompt = "\n\n".join(convert_template(p.get("text", "")) for p in prompts if p.get("role") == "system")
    user_prompt = "\n\n".join(convert_template(p.get("text", "")) for p in prompts if p.get("role") == "user")

    prompt_variables = []
    original_prompt_text = "\n".join(p.get("text", "") for p in prompts)
    for match in re.finditer(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.*\[\]-]+)#\}\}", original_prompt_text):
        source, path = match.group(1), match.group(2)
        mapped = map_selector([source, path], code_map, type_map, start_var_types, conversation_refs, current_node_id)
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


def add_depends_from_node_refs(hi_node: dict[str, Any]) -> None:
    depends = []
    seen = set()
    for dep in hi_node.get("Depends") or []:
        code = dep.get("NodeCode")
        key = (code, dep.get("PortID"))
        if code and code != hi_node.get("Code") and key not in seen:
            new_dep = {"NodeCode": code}
            if dep.get("PortID"):
                new_dep["PortID"] = dep.get("PortID")
            depends.append(new_dep)
            seen.add(key)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            code = value.get("NodeCode")
            key = (code, None)
            if value.get("RefType") == "node_field" and code and code != hi_node.get("Code") and key not in seen:
                depends.append({"NodeCode": code})
                seen.add(key)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(hi_node.get("Configs"))
    if depends:
        hi_node["Depends"] = depends


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
        key = (code, dep.get("PortID"))
        if code and key not in seen:
            new_dep = {"NodeCode": code}
            if dep.get("PortID"):
                new_dep["PortID"] = dep.get("PortID")
            depends.append(new_dep)
            seen.add(key)
    for item in input_variables:
        code = item.get("NodeCode")
        key = (code, None)
        if code and key not in seen:
            depends.append({"NodeCode": code})
            seen.add(key)
    if depends:
        hi_node["Depends"] = depends
    add_tool_dependency(hiagent, template, cfg)
    return hi_node

def convert(
    dify: dict[str, Any],
    template: dict[str, Any] | None,
    fallback_model_id: str,
    fallback_model_name: str,
    chatflow: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    graph = dify["workflow"]["graph"]
    nodes = graph.get("nodes", [])
    parents = upstream_map(dify)
    incoming_edges = incoming_edge_map(dify)
    lookup = node_lookup(dify)
    code_map = {str(node["id"]): stable_code(str(node["id"])) for node in nodes}
    type_map = {str(node["id"]): node["data"]["type"] for node in nodes}
    branch_ports = condition_port_map(nodes)
    start_node = next((node for node in nodes if node["data"].get("type") == "start"), None)
    if chatflow and start_node:
        code_map["sys"] = code_map[str(start_node["id"])]
        type_map["sys"] = "start"
    start_var_types = {var.get("variable", ""): var.get("type", "") for var in ((start_node or {}).get("data") or {}).get("variables", [])}
    conversation_refs = build_conversation_refs(nodes, parents, code_map)
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
        "FlowType": "Agent" if chatflow else "Workflow",
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
        if incoming_edges.get(node_id):
            hi_node["Depends"] = node_depends_from_edges(node_id, incoming_edges, code_map, type_map, branch_ports)

        if dify_type == "start":
            schema = [start_schema_item(var) for var in data.get("variables", [])]
            if chatflow:
                schema = chat_start_schema(schema)
            hi_node["Type"] = "Start"
            hi_node["Name"] = "Start"
            hi_node["Configs"]["Start"] = {"InputSchema": schema, "OutputSchema": copy.deepcopy(schema)}

        elif dify_type == "end":
            inputs = []
            for output in data.get("outputs", []):
                selector = output.get("value_selector") or []
                mapped = map_selector(selector, code_map, type_map, start_var_types, conversation_refs, node_id)
                mapped["Name"] = output.get("variable", "")
                inputs.append(mapped)
            hi_node["Type"] = "End"
            hi_node["Name"] = "End"
            if chatflow:
                first = copy.deepcopy(inputs[0]) if inputs else {"Name": "output", "Path": "output", "RefType": "user_variable"}
                first["Name"] = "output"
                hi_node["Configs"]["End"] = {
                    "InputVariables": [first],
                    "OutputSchema": [{"Name": "content", "Required": True, "Type": 0}],
                    "OutputType": "Content",
                    "StreamOutput": True,
                    "Template": "{{output}}",
                }
            else:
                hi_node["Configs"]["End"] = {"InputVariables": inputs, "OutputType": "Variable"}

        elif dify_type == "llm":
            params = ((data.get("model") or {}).get("completion_params") or {})
            system_prompt, prompt, prompt_vars = llm_prompt_configs(data, code_map, type_map, start_var_types, conversation_refs, node_id)
            input_vars = []
            seen = set()
            for var in data.get("variables") or []:
                name = var.get("variable")
                if not name:
                    continue
                mapped = map_selector(var.get("value_selector"), code_map, type_map, start_var_types, conversation_refs, node_id)
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
                mapped = map_selector(var.get("value_selector"), code_map, type_map, start_var_types, conversation_refs, node_id)
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
            used_input_names: set[str] = set()
            for index, item in enumerate(data.get("items") or [], 1):
                target = item.get("variable_selector") or []
                output_name = str(target[-1]) if target else f"value_{index}"
                operation = item.get("operation") or item.get("write_mode") or "over-write"
                value_expr = "None"
                current_expr = "None"
                output_type = 0

                if str(operation).lower().replace("_", "-") not in {"over-write", "overwrite", "set", "assign", "clear", "reset"}:
                    current_name = unique_input_name(f"current_{output_name}", used_input_names)
                    current_selector = item.get("variable_selector") or []
                    current_mapped = map_selector(current_selector, code_map, type_map, start_var_types, conversation_refs, node_id)
                    current_mapped["Name"] = current_name
                    input_vars.append(current_mapped)
                    current_expr = f"params.get({current_name!r})"

                if item.get("input_type") == "variable":
                    value_selector = item.get("value") or []
                    value_name = unique_input_name(f"value_{output_name}", used_input_names)
                    mapped = map_selector(value_selector, code_map, type_map, start_var_types, conversation_refs, node_id) if value_selector else {"RefType": "node_field"}
                    mapped["Name"] = value_name
                    input_vars.append(mapped)
                    value_expr = f"params.get({value_name!r})"
                elif str(operation).lower().replace("_", "-") in {"clear", "reset"}:
                    value_expr = "None"
                else:
                    value = item.get("value")
                    output_type = infer_hi_type_from_value(value)
                    value_expr = repr(value)

                output_schema.append({"Name": output_name, "Type": output_type})
                assignments.append({
                    "output_name": output_name,
                    "operation": operation,
                    "current_expr": current_expr,
                    "value_expr": value_expr,
                })

            hi_node["Type"] = "Code"
            hi_node["Configs"]["Code"] = {
                "Code": build_assigner_code(assignments),
                "InputVariables": input_vars,
                "Language": 1,
                "OutputSchema": output_schema or [{"Name": "output", "Type": 0}],
                "Retries": 0,
                "TimeoutSeconds": 120,
            }
            report.append(f"变量赋值节点「{hi_node['Name']}」已转换为复现赋值逻辑的 Code 节点，并以节点输出传递给下游。")

        elif dify_type == "template-transform":
            defaults = template_transform_defaults(data.get("template", ""))
            normalizer_inputs = []
            normalizer_assignments = []
            text_input_vars = []
            used_input_names: set[str] = set()
            for var in data.get("variables") or []:
                var_name = var.get("variable", "")
                if not var_name:
                    continue
                input_name = unique_input_name(f"input_{var_name}", used_input_names)
                mapped = map_selector(var.get("value_selector"), code_map, type_map, start_var_types, conversation_refs, node_id)
                mapped["Name"] = input_name
                normalizer_inputs.append(mapped)
                normalizer_assignments.append({
                    "output_name": var_name,
                    "input_name": input_name,
                    "default": defaults.get(var_name, ""),
                })

            normalizer_code = stable_code(f"{node_id}:template-defaults")
            normalizer_node = {
                "Code": normalizer_code,
                "Configs": {
                    "Code": {
                        "Code": build_template_default_code(normalizer_assignments),
                        "InputVariables": normalizer_inputs,
                        "Language": 1,
                        "OutputSchema": [{"Name": item["output_name"], "Type": 0} for item in normalizer_assignments],
                        "Retries": 0,
                        "TimeoutSeconds": 120,
                    }
                },
                "ErrorConfig": {"ErrorConfigType": "None"},
                "ID": normalizer_code,
                "Layout": {"X": float(pos.get("x", 0)) - 260.0, "Y": float(pos.get("y", 0))},
                "Name": f"{hi_node['Name']}_变量默认值",
                "Type": "Code",
            }
            if incoming_edges.get(node_id):
                normalizer_node["Depends"] = node_depends_from_edges(node_id, incoming_edges, code_map, type_map, branch_ports)
            add_depends_from_node_refs(normalizer_node)
            hiagent["Nodes"].append(normalizer_node)

            for item in normalizer_assignments:
                text_input_vars.append({
                    "Name": item["output_name"],
                    "NodeCode": normalizer_code,
                    "Path": item["output_name"],
                    "RefType": "node_field",
                })
            concat_template = convert_template_transform_text(data.get("template", ""))
            hi_node["Type"] = "TextProcessing"
            hi_node["Depends"] = [{"NodeCode": normalizer_code}]
            hi_node["Configs"]["TextProcessing"] = {
                "ConcatTemplate": concat_template,
                "CustomDelimiters": None,
                "Delimiters": None,
                "InputVariables": text_input_vars,
                "OutputSchema": [{"Name": "output", "Type": 0}],
                "TextProcessingType": "Concat",
            }
            if has_complex_template_logic(data.get("template", "")):
                report.append(
                    f"模版转换节点「{hi_node['Name']}」已映射为 HiAgent 文本处理拼接节点，并新增变量默认值预处理 Code 节点；原 Dify 模板包含 Jinja 条件/表达式，HiAgent 文本处理可能只支持变量占位拼接，导入后请重点复核输出。"
                )
            else:
                report.append(f"模版转换节点「{hi_node['Name']}」已映射为 HiAgent 文本处理拼接节点，并新增变量默认值预处理 Code 节点。")

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
                        mapped = map_selector(selector, code_map, type_map, start_var_types, conversation_refs, node_id) if selector else {"RefType": "node_field"}
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

        elif dify_type == "if-else":
            condition_config, condition_warnings = build_condition_node_config(
                data, code_map, type_map, start_var_types, conversation_refs, node_id
            )
            hi_node["Type"] = "Condition"
            hi_node["Configs"]["Condition"] = condition_config
            for warning in condition_warnings:
                report.append(f"选择器节点「{hi_node['Name']}」：{warning}")
            report.append(f"条件分支节点「{hi_node['Name']}」已映射为 HiAgent 选择器节点，并按 sourceHandle 写入下游 Depends.PortID。")

        elif dify_type == "knowledge-retrieval":
            config = data.get("multiple_retrieval_config") or {}
            query_variable = map_selector(data.get("query_variable_selector"), code_map, type_map, start_var_types, conversation_refs, node_id)
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

        elif dify_type == "answer":
            answer_text = data.get("answer", "")
            input_vars = []
            replacements = []
            used_input_names: set[str] = set()
            for index, (token, selector) in enumerate(dify_template_refs(answer_text), 1):
                name = unique_input_name(f"answer_var_{index}", used_input_names)
                mapped = map_selector(selector, code_map, type_map, start_var_types, conversation_refs, node_id)
                mapped["Name"] = name
                input_vars.append(mapped)
                replacements.append((token, name))
            hi_node["Type"] = "Code"
            hi_node["Configs"]["Code"] = {
                "Code": build_answer_code(answer_text, replacements),
                "InputVariables": input_vars,
                "Language": 1,
                "OutputSchema": [{"Name": "output", "Type": 0}],
                "Retries": 0,
                "TimeoutSeconds": 120,
            }
            report.append(f"回答节点「{hi_node['Name']}」已转为可渲染变量的输出组装 Code 节点。")

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

        add_depends_from_node_refs(hi_node)
        hiagent["Nodes"].append(hi_node)

    if not any(node.get("Type") == "End" for node in hiagent["Nodes"]):
        source_ids = {str(edge.get("source")) for edge in graph.get("edges", [])}
        terminal_ids = [str(node.get("id")) for node in nodes if str(node.get("id")) not in source_ids]
        terminal_codes = [code_map[node_id] for node_id in terminal_ids if node_id in code_map]
        if terminal_codes:
            end_code = stable_code("synthetic_end")
            if chatflow:
                assign_codes = []
                for index, code in enumerate(terminal_codes):
                    assign_code = stable_code(f"synthetic_end_assign:{index}:{code}")
                    assign_codes.append(assign_code)
                    hiagent["Nodes"].append({
                        "Code": assign_code,
                        "Configs": {
                            "VariablesAssign": {
                                "Variables": [{"Name": "output", "NodeCode": code, "Path": "output", "RefType": "node_field"}]
                            }
                        },
                        "Depends": [{"NodeCode": code}],
                        "ErrorConfig": {"ErrorConfigType": "None"},
                        "ID": assign_code,
                        "Layout": {"X": 2400.0, "Y": 320.0 + index * 180.0},
                        "Name": f"变量赋值{index + 1:02d}",
                        "Type": "VariablesAssign",
                    })
                end_config = {
                    "InputVariables": [{"Name": "output", "Path": "output", "RefType": "user_variable"}],
                    "OutputSchema": [{"Name": "content", "Required": True, "Type": 0}],
                    "OutputType": "Content",
                    "StreamOutput": True,
                    "Template": "{{output}}",
                }
                end_depends = [{"NodeCode": code} for code in assign_codes]
            else:
                end_config = {
                    "InputVariables": [
                        {"Name": f"output_{index + 1}", "NodeCode": code, "Path": "output", "RefType": "node_field"}
                        for index, code in enumerate(terminal_codes)
                    ],
                    "OutputType": "Variable",
                }
                end_depends = [{"NodeCode": code} for code in terminal_codes]
            hiagent["Nodes"].append({
                "Code": end_code,
                "Configs": {"End": end_config},
                "Depends": end_depends,
                "ErrorConfig": {"ErrorConfigType": "None"},
                "ID": end_code,
                "Layout": {"X": 2600.0, "Y": 420.0},
                "Name": "End",
                "Type": "End",
            })
            report.append("原 Dify 应用未包含标准 end 节点；已追加合成 End 节点用于 HiAgent 工作流导入。")

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
    workflow = report_workflow(hiagent)
    node_lines = []
    for node in workflow["Nodes"]:
        depends = ", ".join(dep["NodeCode"] for dep in node.get("Depends", [])) or "-"
        node_lines.append(f"- {node['Name']}：{node['Type']}，Code={node['Code']}，Depends={depends}")
    text = "\n".join(
        [
            "# Dify 到 HiAgent 转换报告",
            "",
            f"- 工作流：{hiagent.get('DisplayName') or workflow.get('DisplayName')}",
            f"- 类型：{'对话型工作流 Agent zip' if hiagent.get('MetaType') == 'Agent' else '工作流 YAML'}",
            f"- 节点数：{len(workflow.get('Nodes', []))}",
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
    parser.add_argument("--template", type=Path, help="Optional HiAgent export YAML/zip used to copy ModelMap/model IDs and resources.")
    parser.add_argument("--agent-template", type=Path, help="Optional HiAgent ChatFlow agent export zip/YAML used to copy Agent wrapper settings.")
    parser.add_argument("--model-id", default="REPLACE_WITH_HIAGENT_MODEL_ID")
    parser.add_argument("--model-name", default="REPLACE_WITH_HIAGENT_MODEL_NAME")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    dify = load_yaml(args.input)
    template = load_hiagent_template(args.template)
    agent_template = load_hiagent_template(args.agent_template)
    chatflow = is_dify_chatflow(dify)
    hiagent, report = convert(dify, template, args.model_id, args.model_name, chatflow=chatflow)
    if chatflow:
        hiagent = wrap_chatflow_agent(hiagent, dify, agent_template)
        report.append("检测到 Dify advanced-chat/chatflow，已输出 HiAgent 对话型工作流 Agent zip 包。")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if chatflow:
        if args.output.suffix.lower() != ".zip":
            args.output = args.output.with_suffix(".zip")
        write_chatflow_zip(args.output, hiagent, zip_trailing_signature(args.agent_template))
    else:
        args.output.write_text(dump_yaml(hiagent), encoding="utf-8")
    write_report(args.report, hiagent, report)
    workflow = report_workflow(hiagent)
    print(json.dumps({"output": str(args.output), "report": str(args.report), "nodes": len(workflow["Nodes"]), "chatflow": chatflow}, ensure_ascii=False))


if __name__ == "__main__":
    main()
