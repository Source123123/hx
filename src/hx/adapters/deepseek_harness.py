"""DeepSeek Harness（DSH）的 adapter。

抓取范围：
  项目内（引用）：AGENTS.md、CLAUDE.md、AGENTS.local.md、CLAUDE.local.md、
                 .dsh/skills、.agents/skills
  项目外（复制）：$DSH_HOME/AGENTS.md、$DSH_HOME/skills、$DSH_HOME/.env 的参考、
                 $DSH_HOME/profiles/<profile>/cordis.patch.yml、$DSH_HOME/cordis.patch.yml、
                 ~/.agents/AGENTS.md、~/.agents/skills

DSH 的关键位置（对照 0.2.2-rc.3 的 dsh-home-paths、dsh-agent-instructions、
dsh-skill-filesystem、dsh-mcp-manager、dsh-session-persistence-jsonl）：
  - 用户指令：$DSH_HOME/AGENTS.md、<agentsHome>/AGENTS.md
  - 项目指令：AGENTS.md、CLAUDE.md、AGENTS.local.md、CLAUDE.local.md
  - 技能：<project>/.dsh/skills、<project>/.agents/skills、$DSH_HOME/skills、
          <agentsHome>/skills；项目根目录是最接近的 .git 祖先
  - 配置：profile 的 cordis.patch.yml（用户设置）与 $DSH_HOME 下的同名 patch
  - MCP：mcp-manager 行的 config.servers，密钥写成 ${credential:NAME}
  - 会话：$DSH_HOME/sessions/<projectKey>/<encoded-id>/session.v4.jsonl[.zstd]

会话默认用 zstd 压缩。Python 3.14 有 compression.zstd，3.9–3.13 没有；
本 adapter 依次尝试 compression.zstd、第三方 zstandard、node:zlib。都没有时
跳过会话摘要并记录原因，不影响配置抓取。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import struct
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..portable import localize, read_packed_json
from ..util import HARNESS_DIR, env_refs, read_json, relative_to_project, write_text
from .base import Action, Adapter, CaptureContext, Check, Neutral, place_file

A = "agent/deepseek-harness"
SCAN_LIMIT = 500
SECRET_NAME = re.compile(r"(?i)secret|token|password|passwd|key|credential|auth")
CREDENTIAL_REF = re.compile(r"^\$\{credential:([A-Za-z_][A-Za-z0-9_]*)\}$")
BEARER_CREDENTIAL = re.compile(r"^Bearer \$\{credential:([A-Za-z_][A-Za-z0-9_]*)\}$")
ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
PATCH_FILE = re.compile(r"^\*\*\* (?:(?:Update|Add|Delete) File|Move to): (.+)$", re.M)
EDIT_TOOLS = ("write", "edit", "multi_edit", "str_replace_editor", "notebook_edit")
SESSION_FILE = re.compile(r"^session(?:\.v(\d+))?\.jsonl(\.zstd)?$")


def project_key(cwd: str) -> str:
    """DSH 存放项目会话的目录名（对照 dsh-session-persistence-jsonl 的 projectKey）。"""
    if not cwd:
        raise ValueError("empty project path")
    readable = ""
    separator_run = False
    units = cwd.encode("utf-16-le")
    for index in range(0, len(units), 2):
        code = units[index] | (units[index + 1] << 8)
        ch = chr(code)
        if ch in ("/", "\\", ":"):
            if not separator_run:
                readable += "-"
            separator_run = True
        elif ch != "~" and re.match(r"^[A-Za-z0-9._-]$", ch):
            readable += ch
            separator_run = False
        else:
            readable += "~" + format(code, "04X")
            separator_run = False
    slug = re.sub(r"^-+", "", readable) or "root"
    return "--" + slug[:251] + "--"


# ---------- YAML 的窄化读取 ----------
#
# DSH 的 cordis.patch.yml 是注释密集的 YAML，没有第三方依赖时不能完整解析。
# adapter 只需要认两类东西：mcp-manager 行的 servers 表，和 agent-default-model
# 行的 model/provider。下面按缩进读取这些局部结构，写回时只插入/替换局部行，
# 不重写整份文件，因此注释、!!js 表达式和其他行都原样保留。

_ENTRY_ID = re.compile(r"^(\s*)-\s+id:\s*(['\"]?)([^'\"\s#]+)\2\s*(?:#.*)?$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_comment(text: str) -> str:
    quote = None
    escaped = False
    for index, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote == '"':
            escaped = True
            continue
        if ch in ("'", '"'):
            if quote is None:
                quote = ch
            elif quote == ch:
                quote = None
            continue
        if ch == "#" and quote is None and (index == 0 or text[index - 1].isspace()):
            return text[:index]
    return text


def _content(line: str) -> str:
    return _strip_comment(line).strip()


def _scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("!!js"):
        raw = raw[4:].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            return json.loads(raw)
        except ValueError:
            return raw[1:-1]
    lowered = raw.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if raw == "{}":
        return {}
    if raw == "[]":
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            return json.loads(raw)
        except ValueError:
            return [x.strip() for x in raw[1:-1].split(",") if x.strip()]
    if raw.startswith("{") and raw.endswith("}"):
        try:
            return json.loads(raw)
        except ValueError:
            return _inline_map(raw[1:-1])
    try:
        return int(raw) if re.fullmatch(r"-?\d+", raw) else float(raw)
    except ValueError:
        return raw


def _inline_map(text: str) -> dict:
    out = {}
    for part in re.split(r",(?![^{}\[\]]*\])", text):
        key, sep, value = part.partition(":")
        if sep:
            out[_unquote_key(key.strip())] = _scalar(value.strip())
    return out


def _unquote_key(key: str) -> str:
    key = key.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        return _scalar(key)
    return key


def _find_entry(text: str, ids: tuple) -> tuple[int, int, int] | None:
    """找一条 `- id: xxx` 的行范围：[start, end)，以及 entry 的缩进。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _ENTRY_ID.match(line)
        if not match or match.group(3) not in ids:
            continue
        indent = len(match.group(1))
        end = len(lines)
        for probe in range(index + 1, len(lines)):
            content = _content(lines[probe])
            if not content:
                continue
            current = _indent(lines[probe])
            if current <= indent:
                if content.startswith("- "):
                    end = probe
                    break
                if current < indent:
                    end = probe
                    break
        return index, end, indent
    return None


def _child_key(lines: list[str], start: int, end: int, key: str) -> int | None:
    for index in range(start, end):
        content = _content(lines[index])
        if content == key + ":" or content.startswith(key + ":"):
            return index
    return None


def _parse_servers(lines: list[str], start: int, end: int) -> dict:
    """解析 servers 表。start 是 `servers:` 行，end 是 entry 结束行。"""
    if start >= end:
        return {}
    base = _indent(lines[start])
    inline = _content(lines[start]).partition(":")[2].strip()
    if inline == "{}" or not inline:
        servers: dict = {}
    else:
        value = _scalar(inline)
        return value if isinstance(value, dict) else {}
    index = start + 1
    while index < end:
        content = _content(lines[index])
        if not content:
            index += 1
            continue
        current = _indent(lines[index])
        if current <= base:
            break
        if not content.endswith(":"):
            index += 1
            continue
        name = _unquote_key(content[:-1].strip())
        server_indent = current
        next_index = index + 1
        while next_index < end and (not _content(lines[next_index]) or _indent(lines[next_index]) > server_indent):
            next_index += 1
        servers[name] = _parse_server(lines[index + 1:next_index], server_indent)
        index = next_index
    return servers


def _parse_server(lines: list[str], parent_indent: int) -> dict:
    spec: dict = {}
    index = 0
    while index < len(lines):
        content = _content(lines[index])
        if not content:
            index += 1
            continue
        current = _indent(lines[index])
        if current <= parent_indent:
            break
        key, sep, raw = content.partition(":")
        key = _unquote_key(key.strip())
        raw = raw.strip()
        if not sep:
            index += 1
            continue
        if raw in ("", "{}"):
            block_end = index + 1
            child_indent = None
            while block_end < len(lines):
                if not _content(lines[block_end]):
                    block_end += 1
                    continue
                if _indent(lines[block_end]) <= current:
                    break
                if child_indent is None:
                    child_indent = _indent(lines[block_end])
                block_end += 1
            if key == "args":
                spec[key] = [_scalar(_content(x)[2:]) for x in lines[index + 1:block_end]
                             if _content(x).startswith("- ")]
                spec[key] = [x for x in spec[key] if x is not None]
            elif key in ("env", "headers"):
                spec[key] = _parse_mapping(lines[index + 1:block_end], current)
            elif raw == "{}":
                spec[key] = {}
            else:
                spec[key] = _parse_nested(lines[index + 1:block_end], current)
            index = block_end
        else:
            spec[key] = _scalar(raw)
            index += 1
    return spec


def _parse_mapping(lines: list[str], parent_indent: int) -> dict:
    out = {}
    for line in lines:
        content = _content(line)
        if not content or _indent(line) <= parent_indent:
            continue
        key, sep, value = content.partition(":")
        if sep:
            out[_unquote_key(key.strip())] = _scalar(value.strip())
    return out


def _parse_nested(lines: list[str], parent_indent: int) -> dict:
    out = {}
    for line in lines:
        content = _content(line)
        if not content or _indent(line) <= parent_indent:
            continue
        key, sep, value = content.partition(":")
        if sep:
            out[_unquote_key(key.strip())] = _scalar(value.strip())
    return out


def parse_mcp_servers(text: str) -> dict:
    lines = text.splitlines()
    block = _find_entry(text, ("mcp-manager", "mcp"))
    if block is None:
        return {}
    start, end, _ = block
    key = _child_key(lines, start, end, "servers")
    if key is None:
        return {}
    return _parse_servers(lines, key, end)


def parse_entry_config(text: str, entry_id: str) -> dict:
    """读某条 entry 的 config 子树，只用于取 model/provider。"""
    block = _find_entry(text, (entry_id,))
    if block is None:
        return {}
    lines = text.splitlines()
    start, end, _ = block
    config = _child_key(lines, start, end, "config")
    if config is None:
        return {}
    base = _indent(lines[config])
    out = {}
    for index in range(config + 1, end):
        content = _content(lines[index])
        if not content:
            continue
        if _indent(lines[index]) <= base:
            break
        key, sep, value = content.partition(":")
        if sep:
            out[_unquote_key(key.strip())] = _scalar(value.strip())
    return out


def _yaml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_.-]+", key) else json.dumps(key, ensure_ascii=False)


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_value(x) for x in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _server_lines(name: str, spec: dict, indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}{_yaml_key(name)}:"]
    child = " " * (indent + 2)
    for key in ("transport", "command", "url", "cwd"):
        if spec.get(key) is not None:
            lines.append(f"{child}{key}: {_yaml_value(spec[key])}")
    if spec.get("args"):
        lines.append(f"{child}args:")
        for value in spec["args"]:
            lines.append(f"{child}  - {_yaml_value(value)}")
    for key in ("env", "headers"):
        if isinstance(spec.get(key), dict) and spec[key]:
            lines.append(f"{child}{key}:")
            for k, v in spec[key].items():
                lines.append(f"{child}  {_yaml_key(k)}: {_yaml_value(v)}")
    if spec.get("enabled") is False:
        lines.append(f"{child}enabled: false")
    return lines


def merge_mcp_yaml(config: Path, servers: dict, dry_run: bool = False) -> list:
    """把中立写法的 MCP 服务器并入 DSH 的 cordis.patch.yml：只添加没有的。"""
    if not servers:
        return []
    text = config.read_text(encoding="utf-8", errors="replace") if config.is_file() else ""
    existing = parse_mcp_servers(text)
    actions, blocks = [], []
    for name, neutral in sorted(servers.items()):
        spec = dsh_from_neutral(neutral)
        if name in existing:
            same = _normalize_server(existing[name]) == _normalize_server(spec)
            actions.append(Action("same", f"{config}#servers.{name}") if same else
                           Action("conflict", f"{config}#servers.{name}", "DSH 配置里已有同名但不同的 MCP 服务器，未改动"))
            continue
        blocks.append(_server_lines(name, spec, 0))
        actions.append(Action("written", f"{config}#servers.{name}", "MCP 服务器加入 DSH 配置"))
    if not blocks or dry_run:
        return actions

    lines = text.splitlines()
    if text and not text.endswith("\n"):
        lines.append("")
    block = _find_entry(text, ("mcp-manager", "mcp"))
    if block is None:
        entry = ["- id: mcp-manager",
                 "  name: '@deepseek-ai/dsh-mcp-manager'",
                 "  config:",
                 "    servers:"]
        for block_lines in blocks:
            entry += [" " * 6 + line if line else line for line in block_lines]
        if lines and lines[-1].strip():
            entry = [""] + entry
        out = "\n".join(lines + entry).rstrip() + "\n"
    else:
        start, end, indent = block
        servers_key = _child_key(lines, start, end, "servers")
        if servers_key is not None:
            base = _indent(lines[servers_key])
            child_indent = base + 2
            inline = _content(lines[servers_key]).partition(":")[2].strip()
            if inline == "{}":
                lines[servers_key] = " " * base + "servers:"
                insert_at = servers_key + 1
            else:
                insert_at = servers_key + 1
                while insert_at < len(lines):
                    content = _content(lines[insert_at])
                    if content and _indent(lines[insert_at]) <= base:
                        break
                    insert_at += 1
            new_lines = []
            for block_lines in blocks:
                new_lines += [(" " * child_indent + line) if line else line for line in block_lines]
            lines[insert_at:insert_at] = new_lines
        else:
            config_key = _child_key(lines, start, end, "config")
            if config_key is None:
                insert_at = start + 1
                while insert_at < len(lines) and _content(lines[insert_at]).startswith(("name:", "inject:", "disabled:")):
                    insert_at += 1
                new_lines = [" " * (indent + 2) + "config:",
                             " " * (indent + 4) + "servers:"]
                for block_lines in blocks:
                    new_lines += [" " * (indent + 6) + line if line else line for line in block_lines]
            else:
                base = _indent(lines[config_key])
                insert_at = config_key + 1
                while insert_at < len(lines) and (not _content(lines[insert_at]) or _indent(lines[insert_at]) > base):
                    insert_at += 1
                new_lines = [" " * (base + 2) + "servers:"]
                for block_lines in blocks:
                    new_lines += [" " * (base + 4) + line if line else line for line in block_lines]
            lines[insert_at:insert_at] = new_lines
        out = "\n".join(lines).rstrip() + "\n"
    write_text(config, out)
    return actions


def _normalize_server(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if not k.startswith("x-hx-")}


def dsh_to_neutral(spec: dict, ctx: CaptureContext, name: str) -> dict:
    out: dict = {}
    if isinstance(spec.get("url"), str) or spec.get("transport") == "streamable-http":
        out["type"] = "http"
        out["url"] = ctx.redactor.text(str(spec.get("url", "")))
        headers = {}
        for header, value in (spec.get("headers") or {}).items():
            value = str(value)
            match = BEARER_CREDENTIAL.match(value)
            if match:
                headers[header] = "Bearer ${%s}" % match.group(1)
                ctx.secrets.add(match.group(1))
            elif CREDENTIAL_REF.match(value):
                var = CREDENTIAL_REF.match(value).group(1)
                headers[header] = "${%s}" % var
                ctx.secrets.add(var)
            else:
                refs = env_refs(value)
                if refs:
                    headers[header] = value
                    ctx.secrets.update(refs)
                else:
                    var = "MCP_" + re.sub(r"[^A-Za-z0-9]", "_", name + "_" + header).upper()
                    headers[header] = "${%s}" % var
                    ctx.secrets.add(var)
        if headers:
            out["headers"] = headers
    else:
        out["command"] = ctx.redactor.text(str(spec.get("command", "")))
        if isinstance(spec.get("args"), list):
            out["args"] = [ctx.redactor.text(a) if isinstance(a, str) else a for a in spec["args"]]
        env = {}
        for key, value in (spec.get("env") or {}).items():
            value = str(value)
            match = CREDENTIAL_REF.match(value)
            if match:
                env[key] = "${%s}" % match.group(1)
                ctx.secrets.add(match.group(1))
            elif env_refs(value):
                env[key] = value
                ctx.secrets.update(env_refs(value))
            elif SECRET_NAME.search(key) or ctx.redactor.text(value) != value:
                env[key] = "${%s}" % key
                ctx.secrets.add(key)
            else:
                env[key] = value
        if env:
            out["env"] = env
    if spec.get("enabled") is False:
        out["x-hx-enabled"] = False
    if isinstance(spec.get("cwd"), str):
        out["cwd"] = ctx.redactor.text(spec["cwd"])
    return out


def dsh_from_neutral(spec: dict) -> dict:
    out: dict = {}
    if isinstance(spec.get("url"), str) or spec.get("type") == "http":
        out["transport"] = "streamable-http"
        out["url"] = spec.get("url", "")
        headers = {}
        for header, value in (spec.get("headers") or {}).items():
            value = str(value)
            match = BEARER_CREDENTIAL.match(value) or re.match(r"^Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}$", value)
            if match:
                headers[header] = "Bearer ${credential:%s}" % match.group(1)
            elif ENV_REF.match(value):
                headers[header] = "${credential:%s}" % ENV_REF.match(value).group(1)
            else:
                headers[header] = value
        if headers:
            out["headers"] = headers
    else:
        out["transport"] = "stdio"
        out["command"] = spec.get("command", "")
        if spec.get("args"):
            out["args"] = list(spec["args"])
        env = {}
        for key, value in (spec.get("env") or {}).items():
            value = str(value)
            match = ENV_REF.match(value)
            env[key] = "${credential:%s}" % match.group(1) if match else value
        if env:
            out["env"] = env
    for key in ("cwd",):
        if spec.get(key):
            out[key] = spec[key]
    if spec.get("x-hx-enabled") is False:
        out["enabled"] = False
    return out


# ---------- 会话摘要 ----------

def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " …(截断)"


def _iso(ms) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _texts(content) -> list:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"]
    return []


def _tool_files(name: str, arguments) -> list:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return []
    if not isinstance(arguments, dict):
        return []
    files = []
    if name == "apply_patch":
        for match in PATCH_FILE.findall(str(arguments.get("input") or arguments.get("patch") or "")):
            files.append(match.strip().strip("\"'"))
    elif name in EDIT_TOOLS:
        value = arguments.get("file_path") or arguments.get("path") or arguments.get("notebook_path")
        if isinstance(value, str):
            files.append(value)
    return files


def digest_session(text: str, project: Path, fallback_id: str = "", agent_version: str | None = None) -> dict:
    """把一个 DSH 会话（解压后的 JSONL 文本）压缩成与其他 adapter 一致的摘要。"""
    prompts, files, tools, models = [], [], Counter(), []
    started = ended = None
    session_id = fallback_id
    last_reply = ""
    root = str(project.resolve())
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        if kind == "session":
            session_id = record.get("id") or session_id
            timestamp = record.get("createdAt")
            if isinstance(timestamp, (int, float)):
                started = timestamp if started is None else min(started, timestamp)
                ended = timestamp if ended is None else max(ended, timestamp)
            continue
        timestamp = record.get("time")
        if isinstance(timestamp, (int, float)):
            started = timestamp if started is None else min(started, timestamp)
            ended = timestamp if ended is None else max(ended, timestamp)
        if kind == "request/context":
            model = data.get("model")
            if isinstance(model, str) and (not models or models[-1] != model):
                models.append(model)
        elif kind == "user/message":
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            if source.get("kind") not in (None, "user", "client"):
                continue
            prompt = "\n".join(t for t in _texts(data.get("content")) if t.strip()).strip()
            if prompt:
                prompts.append(_clip(prompt, 2000))
        elif kind == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            reply = "\n".join(t for t in _texts(message.get("content")) if t.strip()).strip()
            if reply:
                last_reply = reply
        elif kind == "tool/call":
            name = data.get("name") or "?"
            tools[name] += 1
            for path in _tool_files(name, data.get("arguments")):
                rel = relative_to_project(path, root)
                if rel not in files:
                    files.append(rel)
    return {
        "id": session_id,
        "started": _iso(started),
        "ended": _iso(ended),
        "git_branch": _git_branch(project),
        "agent_version": agent_version,
        "models": models,
        "prompts": prompts,
        "files_changed": files,
        "tool_counts": dict(sorted(tools.items())),
        "last_reply": _clip(last_reply, 1500),
    }


def _git_branch(project: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(project), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
        branch = result.stdout.strip()
        return branch if result.returncode == 0 and branch and branch != "HEAD" else None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------- zstd 解码 ----------

_ZSTD_NODE = r"""
const fs = require('node:fs'), z = require('node:zlib')
const MAGIC = 0xFD2FB528
function scan(b) {
  const frames = []
  let offset = 0
  while (offset < b.length) {
    const start = offset
    if (b.length - offset < 4) return { frames, tornStart: start }
    if (b.readUInt32LE(offset) !== MAGIC) throw new Error('invalid zstd magic at ' + offset)
    offset += 4
    if (offset === b.length) return { frames, tornStart: start }
    const descriptor = b.readUInt8(offset); offset += 1
    if ((descriptor & 0x18) !== 0) throw new Error('reserved zstd frame-header bit')
    const contentSizeFlag = descriptor >>> 6
    const singleSegment = (descriptor & 0x20) !== 0
    const checksum = (descriptor & 0x04) !== 0
    const dictFlag = descriptor & 0x03
    const dictBytes = dictFlag === 3 ? 4 : dictFlag
    const sizeBytes = contentSizeFlag === 0 ? (singleSegment ? 1 : 0) : 1 << contentSizeFlag
    const headerBytes = (singleSegment ? 0 : 1) + dictBytes + sizeBytes
    if (b.length - offset < headerBytes) return { frames, tornStart: start }
    offset += headerBytes
    for (;;) {
      if (b.length - offset < 3) return { frames, tornStart: start }
      const block = b.readUIntLE(offset, 3); offset += 3
      const last = (block & 1) !== 0
      const type = (block >>> 1) & 0x03
      const size = block >>> 3
      if (type === 0x03) throw new Error('reserved zstd block type')
      const payload = type === 0x01 ? 1 : size
      if (b.length - offset < payload) return { frames, tornStart: start }
      offset += payload
      if (last) break
    }
    if (checksum) {
      if (b.length - offset < 4) return { frames, tornStart: start }
      offset += 4
    }
    frames.push({ start, end: offset })
  }
  return { frames }
}
const paths = JSON.parse(fs.readFileSync(0, 'utf8'))
const out = {}
for (const path of paths) {
  try {
    const data = fs.readFileSync(path)
    if (!data.length) { out[path] = ''; continue }
    if (data.readUInt32LE(0) !== MAGIC) { out[path] = data.toString('utf8'); continue }
    const { frames } = scan(data)
    const parts = frames.map(frame => z.zstdDecompressSync(data.subarray(frame.start, frame.end)))
    out[path] = Buffer.concat(parts).toString('utf8')
  } catch (error) {
    out[path] = { error: String((error && error.message) || error) }
  }
}
process.stdout.write(JSON.stringify(out))
"""


def _python_zstd_text(data: bytes) -> str | None:
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+
    except ImportError:
        zstd = None
    if zstd is not None:
        try:
            with zstd.ZstdFile(io.BytesIO(data)) as handle:
                return handle.read().decode("utf-8", errors="replace")
        except Exception:
            return None
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _node_executable(home: Path, dsh_root: Path | None = None) -> str | None:
    override = os.environ.get("HX_NODE")
    if override and Path(override).is_file():
        return override
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    for pattern in ("dsh-runtimes/*/dependencies/node/bin/node.exe", "dsh-runtimes/*/dependencies/node/bin/node"):
        for candidate in sorted((dsh_root or (home / ".dsh")).glob(pattern)):
            if candidate.is_file():
                return str(candidate)
    return None


def read_session_texts(paths: list[Path], home: Path, dsh_root: Path | None = None) -> tuple[dict, str | None]:
    """读取会话文本。返回 ({路径: 文本}, 失败原因)。zstd 优先用 Python，最后用 node。"""
    texts: dict = {}
    compressed = []
    for path in paths:
        if path.suffix == ".zstd":
            compressed.append(path)
            continue
        try:
            texts[str(path)] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not compressed:
        return texts, None
    for path in compressed:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        text = _python_zstd_text(data)
        if text is not None:
            texts[str(path)] = text
    remaining = [path for path in compressed if str(path) not in texts]
    if not remaining:
        return texts, None
    node = _node_executable(home, dsh_root)
    if not node:
        return texts, "无法解压 zstd 会话：需要 Python 3.14、zstandard 模块或 node"
    try:
        result = subprocess.run([node, "-e", _ZSTD_NODE], input=json.dumps([str(p) for p in remaining]),
                                capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return texts, f"node 解压 zstd 失败：{(result.stderr or '').strip()[:200]}"
        payload = json.loads(result.stdout)
        for path in remaining:
            value = payload.get(str(path))
            if isinstance(value, str):
                texts[str(path)] = value
            elif isinstance(value, dict):
                return texts, f"解压 {path.name} 失败：{value.get('error')}"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return texts, f"node 解压 zstd 失败：{error}"
    return texts, None


# ---------- DSH 版本 ----------

def _asar_member(asar: Path, parts: tuple) -> dict | None:
    try:
        with asar.open("rb") as handle:
            head = handle.read(16)
            if len(head) < 16:
                return None
            size = struct.unpack_from("<I", head, 12)[0]
            header = json.loads(handle.read(size).decode("utf-8"))
            base = 8 + struct.unpack_from("<I", head, 4)[0]
            node: dict = header
            for part in parts:
                node = node["files"][part]
            handle.seek(base + int(node["offset"]))
            data = handle.read(int(node["size"]))
            return json.loads(data.decode("utf-8"))
    except (OSError, ValueError, KeyError, struct.error):
        return None


def dsh_version(home: Path) -> str | None:
    configured = os.environ.get("DSH_INSTALL_DIR")
    if configured and not os.environ.get("HX_HOME"):
        text = str(Path(configured))
        index = text.lower().find(".asar")
        if index >= 0:
            asar = Path(text[:index + 5])
            for parts in (("dsh", "desktop-runtime.json"), ("dsh", "package.json"), ("package.json",)):
                data = _asar_member(asar, parts)
                if isinstance(data, dict):
                    version = (data.get("release") or {}).get("version") if parts[-1] == "desktop-runtime.json" else data.get("version")
                    if isinstance(version, str) and version:
                        return version
    if not os.environ.get("HX_HOME"):
        for candidate in (Path(configured) / "package.json" if configured else None,
                          Path(configured).parent / "package.json" if configured else None,
                          Path(configured) / "dsh" / "package.json" if configured else None):
            if candidate and candidate.is_file():
                data = read_json(candidate, {})
                if isinstance(data, dict) and isinstance(data.get("version"), str):
                    return data["version"]
    for path in sorted((home / ".dsh" / "profiles").glob("*/node_modules/@deepseek-ai/dsh/package.json")):
        data = read_json(path, {})
        if isinstance(data, dict) and isinstance(data.get("version"), str):
            return data["version"]
    return None


# ---------- adapter ----------

class DeepSeekHarnessAdapter(Adapter):
    name = "deepseek-harness"
    title = "DeepSeek Harness"
    generated_dirs = (A,)

    # ---------- 路径 ----------

    def user_root(self, home: Path) -> Path:
        custom = os.environ.get("DSH_HOME")
        if custom and not os.environ.get("HX_HOME"):
            return Path(custom)
        return home / ".dsh"

    def agents_root(self, home: Path) -> Path:
        custom = os.environ.get("DSH_AGENTS_HOME")
        if custom and not os.environ.get("HX_HOME"):
            return Path(custom)
        return home / ".agents"

    def profiles_root(self, home: Path) -> Path:
        return self.user_root(home) / "profiles"

    def active_profile(self, home: Path) -> str:
        root = self.profiles_root(home)
        name = os.environ.get("DSH_PROFILE") if not os.environ.get("HX_HOME") else None
        if name:
            name = Path(name).name
        if name and (root / name).is_dir():
            return name
        for candidate in ("default", "desktop", "web"):
            if (root / candidate).is_dir():
                return candidate
        names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
        return names[0] if names else "default"

    def profile_patch(self, project: Path, home: Path) -> Path:
        return self.profiles_root(home) / self.active_profile(home) / "cordis.patch.yml"

    def home_patch(self, home: Path) -> Path:
        return self.user_root(home) / "cordis.patch.yml"

    def session_root(self, home: Path) -> Path:
        """会话根目录。默认 $DSH_HOME/sessions，也会读 profile 里的 session-persistence-jsonl。"""
        for path in (self.home_patch(home),
                     self.profiles_root(home) / self.active_profile(home) / "cordis.patch.yml"):
            if not path.is_file():
                continue
            config = parse_entry_config(path.read_text(encoding="utf-8", errors="replace"),
                                        "session-persistence-jsonl")
            value = config.get("root")
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            match = re.search(r"dshHomePath\(\s*['\"]([^'\"]+)['\"]\s*\)", value)
            if match:
                return self.user_root(home) / match.group(1)
            if value.startswith("!!js"):
                value = value[4:].strip()
            if os.path.isabs(value):
                return Path(value)
            return self.user_root(home) / value
        return self.user_root(home) / "sessions"

    def _project_sessions(self, project: Path, home: Path) -> Path:
        return self.session_root(home) / project_key(str(project.resolve()))

    # ---------- 识别 ----------

    def detect(self, project: Path, home: Path) -> bool:
        return ((project / ".dsh").exists() or (project / ".agents/skills").exists()
                or self.used_here(project, home))

    def used_here(self, project: Path, home: Path) -> bool:
        return bool(self._session_files(project, home, first_only=True))

    def _session_files(self, project: Path, home: Path, first_only: bool = False) -> list:
        directory = self._project_sessions(project, home)
        if not directory.is_dir():
            return []
        files = []
        for session_dir in directory.iterdir():
            if not session_dir.is_dir():
                continue
            best = None
            for path in session_dir.iterdir():
                match = SESSION_FILE.match(path.name)
                if not match:
                    continue
                version = int(match.group(1) or 0)
                key = (version, 1 if match.group(2) else 0)
                if best is None or key > best[0]:
                    best = (key, path)
            if best is not None:
                files.append(best[1])
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        files = files[:SCAN_LIMIT]
        found = files[::-1]
        return found[-1:] if first_only else found

    # ---------- capture ----------

    def capture(self, ctx: CaptureContext) -> None:
        self._capture_project(ctx)
        self._capture_user(ctx)
        self._capture_sessions(ctx)

    def _capture_project(self, ctx: CaptureContext) -> None:
        for name in ("AGENTS.md", "CLAUDE.md", "AGENTS.local.md", "CLAUDE.local.md"):
            ctx.ref(name, "instructions")
        for directory in (".dsh/skills", ".agents/skills"):
            ctx.ref_tree(directory, "skills")
        # 项目里已有的 .dsh 配置目前没有官方入口；保留目录存在这一事实即可。

    def _capture_user(self, ctx: CaptureContext) -> None:
        root = self.user_root(ctx.home)
        shared = self.agents_root(ctx.home)
        if (root / "AGENTS.md").is_file():
            ctx.put(f"{A}/user/AGENTS.md", (root / "AGENTS.md").read_bytes(), "instructions", "user:AGENTS.md")
        if (root / "cordis.patch.yml").is_file():
            ctx.put(f"{A}/user/cordis.patch.yml", (root / "cordis.patch.yml").read_bytes(),
                    "settings", "user:cordis.patch.yml")
        profile = self.active_profile(ctx.home)
        patch = self.profiles_root(ctx.home) / profile / "cordis.patch.yml"
        if patch.is_file():
            ctx.put(f"{A}/profile/{profile}/cordis.patch.yml", patch.read_bytes(), "settings",
                    f"user:profiles/{profile}/cordis.patch.yml")
        ctx.put_tree(root / "skills", f"{A}/user/skills", "skills", "user:skills", exclude=(".system",))
        if (shared / "AGENTS.md").is_file():
            ctx.put(f"{A}/shared/AGENTS.md", (shared / "AGENTS.md").read_bytes(), "instructions")
        ctx.put_tree(shared / "skills", f"{A}/shared-skills", "skills")

        self._capture_mcp(ctx, profile)
        self._capture_plugins(ctx, profile)
        self._capture_credentials(ctx)
        self._capture_env(ctx)

    def _capture_mcp(self, ctx: CaptureContext, profile: str) -> None:
        servers: dict = {}
        for path in (self.profiles_root(ctx.home) / profile / "cordis.patch.yml",
                     self.home_patch(ctx.home)):
            if path.is_file():
                servers.update(parse_mcp_servers(path.read_text(encoding="utf-8", errors="replace")))
        if servers:
            neutral = {name: dsh_to_neutral(spec, ctx, name) for name, spec in sorted(servers.items())
                       if isinstance(spec, dict)}
            ctx.put(f"{A}/mcp.json", json.dumps({"mcpServers": neutral}, ensure_ascii=False, indent=2,
                                                sort_keys=True) + "\n", "mcp")

    def _capture_plugins(self, ctx: CaptureContext, profile: str) -> None:
        manifest_path = self.profiles_root(ctx.home) / profile / "package.json"
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            manifest = {}
        dependencies = manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {}
        installed = {}
        for name in sorted(dependencies):
            package = self.profiles_root(ctx.home) / profile / "node_modules" / name / "package.json"
            data = read_json(package, {})
            if isinstance(data, dict) and isinstance(data.get("version"), str):
                installed[name] = data["version"]
        payload = {
            "profile": profile,
            "bundles": list(((manifest.get("dsh") or {}).get("profile") or {}).get("bundles") or []),
            "dependencies": dependencies,
            "installed": installed,
            "dshVersion": dsh_version(ctx.home),
        }
        if payload["bundles"] or dependencies:
            ctx.put(f"{A}/plugins.json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    "plugins")

    def _capture_credentials(self, ctx: CaptureContext) -> None:
        path = self.user_root(ctx.home) / ".credentials.yaml"
        if not path.is_file():
            return
        names = _credential_names(path.read_text(encoding="utf-8", errors="replace"))
        if names:
            ctx.put(f"{A}/credentials.json", json.dumps({"refs": names}, ensure_ascii=False,
                                                        indent=2, sort_keys=True) + "\n", "settings")
        ctx.skipped.append((".credentials.yaml", "凭据值不打包，只记录名字"))

    def _capture_env(self, ctx: CaptureContext) -> None:
        path = self.user_root(ctx.home) / ".env"
        if not path.is_file():
            return
        values = _parse_dotenv(path.read_text(encoding="utf-8", errors="replace"))
        names, kept = [], []
        for key, value in values.items():
            if SECRET_NAME.search(key) or ctx.redactor.text(value) != value:
                ctx.secrets.add(key)
                names.append(key)
            else:
                kept.append(f"{key}={value}")
        text = "# DSH .env 的参考副本；只记录变量名和非密钥值，不自动还原。\n"
        text += "\n".join(kept) + ("\n" if kept else "")
        if names:
            text += "# 需要在本机设置的变量名：" + "、".join(sorted(names)) + "\n"
        ctx.put(f"{A}/user/env.reference", text, "settings")
        ctx.skipped.append((".env", "密钥值不打包；需要的变量名已登记，载入时由本机提供"))

    def _capture_sessions(self, ctx: CaptureContext) -> None:
        limit = int(ctx.options.get("max_sessions", 30))
        files = self._session_files(ctx.project, ctx.home)[-max(limit, 1):]
        version = dsh_version(ctx.home)
        project_patch = self.profile_patch(ctx.project, ctx.home)
        config = parse_entry_config(project_patch.read_text(encoding="utf-8", errors="replace"), "agent-default-model") \
            if project_patch.is_file() else {}
        home_config = parse_entry_config(
            (self.home_patch(ctx.home)).read_text(encoding="utf-8", errors="replace"), "agent-default-model") \
            if self.home_patch(ctx.home).is_file() else {}
        model = home_config.get("model") or config.get("model")
        ctx.info["agent_version"] = version
        ctx.info["model"] = model if isinstance(model, str) else None
        if not files:
            return
        texts, error = read_session_texts(files, ctx.home, self.user_root(ctx.home))
        if error:
            ctx.skipped.append(("DeepSeek Harness 会话", error))
        latest = {}
        for path in files:
            text = texts.get(str(path))
            if text is None:
                continue
            digest = digest_session(text, ctx.project, path.parent.name, version)
            if not digest["prompts"] and not digest["files_changed"]:
                continue
            if limit > 0:
                ctx.session(digest)
            latest = digest
        if not isinstance(ctx.info.get("model"), str) and latest.get("models"):
            ctx.info["model"] = latest["models"][-1]

    # ---------- materialize ----------

    def materialize(self, project: Path, home: Path, lock: dict, force: bool, dry_run: bool = False) -> list:
        actions = []
        base = project / HARNESS_DIR / A
        shared = self.agents_root(home)
        for rel in ("shared/AGENTS.md",):
            source = base / rel
            if source.is_file():
                actions.append(place_file(localize(source.read_bytes(), project, home),
                                          shared / "AGENTS.md", force, dry_run))
        skills = base / "shared-skills"
        if skills.is_dir():
            for path in sorted(x for x in skills.rglob("*") if x.is_file()):
                actions.append(place_file(localize(path.read_bytes(), project, home),
                                          shared / "skills" / path.relative_to(skills), force, dry_run))
        packed = read_packed_json(base / "mcp.json", project, home, {}) or {}
        servers = packed.get("mcpServers") or {}
        if servers:
            actions += merge_mcp_yaml(self._locked_profile_patch(project, home, lock), servers, dry_run)
        return actions

    def _locked_profile_patch(self, project: Path, home: Path, lock: dict) -> Path:
        """优先把 MCP 合进快照里那个 profile，避免目标机器 profile 名不同导致两处分裂。"""
        for entry in lock.get("entries", []):
            target = entry.get("target") or ""
            match = re.match(r"^user:profiles/([^/]+)/cordis\.patch\.yml$", target)
            if match:
                return self.profiles_root(home) / match.group(1) / "cordis.patch.yml"
        return self.profile_patch(project, home)

    # ---------- 跨 Agent ----------

    def export_neutral(self, project: Path) -> Neutral:
        neutral = Neutral()
        base = project / HARNESS_DIR / A
        for root in (project / ".dsh/skills", project / ".agents/skills", base / "user/skills", base / "shared-skills"):
            if root.is_dir():
                for path in sorted(x for x in root.rglob("*") if x.is_file()):
                    neutral.skills[path.relative_to(root).as_posix()] = path.read_bytes()
        for name, spec in ((read_json(base / "mcp.json", {}) or {}).get("mcpServers") or {}).items():
            if isinstance(spec, dict) and spec.get("x-hx-enabled") is not False:
                neutral.mcp[name] = {key: value for key, value in spec.items() if not key.startswith("x-hx-")}
        neutral.instructions = [name for name in ("AGENTS.md", "CLAUDE.md") if (project / name).is_file()]
        return neutral

    def adopt(self, project: Path, home: Path, neutral: Neutral, force: bool, dry_run: bool = False) -> list:
        actions = []
        if neutral.instructions and not any((project / name).is_file() for name in ("AGENTS.md", "CLAUDE.md")):
            lines = ["# 项目指令", "", "本项目的指令写在下面这些文件里。开始工作前请完整阅读并遵守："]
            lines += [f"- `{name}`" for name in neutral.instructions]
            lines += ["", "项目当前的进展和待办见交接简报：`.harness/context/HANDOFF.md`。", ""]
            actions.append(place_file("\n".join(lines).encode(), project / "AGENTS.md", force, dry_run))
        root = self.agents_root(home) / "skills"
        for rel, data in sorted(neutral.skills.items()):
            actions.append(place_file(data, root / rel, force, dry_run))
        if neutral.mcp:
            actions += merge_mcp_yaml(self.profile_patch(project, home), neutral.mcp, dry_run)
        return actions

    # ---------- doctor ----------

    def doctor(self, project: Path, home: Path, manifest: dict, lock: dict) -> list:
        checks = []
        root = self.user_root(home)
        if not root.is_dir():
            checks.append(Check("fail", "没有找到 DeepSeek Harness 的用户目录", f"先启动一次 DSH，或设置 DSH_HOME（当前按 {root} 查找）。"))
            return checks
        version = dsh_version(home)
        wanted = ((lock.get("agents") or {}).get(self.name) or {}).get("version")
        if version and wanted and version != wanted:
            checks.append(Check("warn", f"DeepSeek Harness 版本不同：本机 {version}，快照 {wanted}", "行为可能有差异"))
        else:
            checks.append(Check("ok", f"DeepSeek Harness 已安装（{version or '版本未知'}）"))
        profile = self.active_profile(home)
        if not (self.profiles_root(home) / profile).is_dir():
            checks.append(Check("warn", f"没有找到 DSH profile：{profile}", "启动一次 DSH 会在 ~/.dsh/profiles 下创建。"))
        packed = read_packed_json(project / HARNESS_DIR / A / "mcp.json", project, home, {}) or {}
        for name, spec in sorted((packed.get("mcpServers") or {}).items()):
            command = spec.get("command") if isinstance(spec, dict) else None
            if not command:
                continue
            if shutil.which(command) or Path(command).is_file():
                checks.append(Check("ok", f"MCP 服务器 {name}：命令 {command} 可用"))
            else:
                checks.append(Check("fail", f"MCP 服务器 {name}：找不到命令 {command}", f"请安装 {command}"))
        credentials = (read_json(project / HARNESS_DIR / A / "credentials.json", {}) or {}).get("refs") or []
        local_credentials = set()
        credential_file = root / ".credentials.yaml"
        if credential_file.is_file():
            local_credentials = set(_credential_names(credential_file.read_text(encoding="utf-8", errors="replace")))
        for name in credentials:
            if isinstance(name, str) and (name in local_credentials or os.environ.get(name)):
                checks.append(Check("ok", f"凭据 {name} 已存在"))
            else:
                checks.append(Check("warn", f"凭据 {name} 未找到", "在 DSH 设置里登录或配置该凭据；也可以设置同名环境变量后重试。"))
        plugins = read_json(project / HARNESS_DIR / A / "plugins.json", {}) or {}
        for name, wanted_version in sorted((plugins.get("dependencies") or {}).items()):
            package = self.profiles_root(home) / profile / "node_modules" / name / "package.json"
            data = read_json(package, {})
            if not isinstance(data, dict):
                checks.append(Check("warn", f"DSH 插件依赖未安装：{name}", f"在 profile 目录运行 dsh plugin add {name} 或 pnpm install。"))
            elif isinstance(data.get("version"), str) and data["version"] != wanted_version:
                checks.append(Check("warn", f"DSH 插件版本不同：{name}", f"本机 {data['version']}，快照 {wanted_version}。"))
        if self._session_files(project, home, first_only=True):
            probe = self._session_files(project, home, first_only=True)[0]
            captured, error = read_session_texts([probe], home, root)
            if error and str(probe) not in captured:
                checks.append(Check("warn", "DSH 会话是 zstd 压缩的，本机没有可用的解码器",
                                    "安装 Python 3.14、zstandard 模块，或把 node 放进 PATH；否则只能保存配置，不能提炼会话摘要。"))
        return checks

    # ---------- 团队一键接入 / 原始对话 ----------

    def team_setup(self, project: Path, enable: bool) -> list | None:
        return None

    def transcripts(self, project: Path, home: Path) -> list:
        return [path for path in self._session_files(project, home) if path.suffix == ".jsonl"]

    def transcript_dir(self, project: Path, home: Path) -> Path:
        return self._project_sessions(project, home)


# ---------- 小工具 ----------

def _parse_dotenv(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _credential_names(text: str) -> list:
    names = []
    in_refs = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) == 0:
            in_refs = stripped == "refs:"
            continue
        if in_refs and _indent(line) >= 2:
            key, sep, _ = stripped.partition(":")
            if sep:
                names.append(_unquote_key(key.strip()))
    return sorted(set(names))


# 兼容测试或其他模块直接引用
def parse_dsh_mcp(text: str) -> dict:
    return parse_mcp_servers(text)
