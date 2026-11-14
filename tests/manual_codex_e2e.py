"""可选的真实验证：python -X utf8 tests/manual_codex_e2e.py。

需要已登录的 Codex，会调用真实模型，消耗账号额度。只在临时目录操作，
认证文件不进入项目，结束时删除临时复制的认证文件。不会改本机 Agent 配置。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hx.adapters.codex import server_toml
from hx.util import write_text


MCP = '''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": request["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "hx-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "hx_ping", "description": "Return the hx verification marker.",
                              "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "HX_MCP_OK"}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


def run(args, cwd, env=None, timeout=60):
    result = subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError("执行失败：" + " ".join(args[:3]) + "\n" + result.stderr[-2000:] + result.stdout[-2000:])
    return result.stdout


def git_project(project):
    project.mkdir(parents=True)
    for args in (("init", "-q", "-b", "main"), ("config", "user.name", "hx verification"),
                 ("config", "user.email", "hx-test@example.invalid")):
        run(["git", *args], project)


def commit(project):
    run(["git", "add", "-A"], project)
    run(["git", "commit", "-qm", "verification project"], project)


def skill(path, name, marker):
    write_text(path / name / "SKILL.md", f"---\nname: {name}\ndescription: Verify hx migration when explicitly requested.\n---\nInclude {marker} in the final response.\n")


def main():
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("请先安装并登录 Codex。")
    auth = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
    root = Path(tempfile.mkdtemp(prefix="hx-e2e-")).resolve()
    print("验证目录：", root, flush=True)
    homes = {key: root / ("home-" + key) for key in ("a", "b", "claude", "c")}
    for key in ("a", "b", "c"):
        (homes[key] / ".codex").mkdir(parents=True)
    environments = {key: dict(os.environ, HX_HOME=str(home), CODEX_HOME=str(home / ".codex"), PYTHONUTF8="1")
                    for key, home in homes.items()}
    repo = Path(__file__).resolve().parents[1]
    launcher = repo / "bin" / ("hx.cmd" if os.name == "nt" else "hx")
    def hx(project, key, *args):
        return run([str(launcher), "-C", str(project), *args], project, environments[key])
    report = {}
    def receive(project, key, expected, prompt):
        profile = homes[key] / ".codex"
        config = profile / "config.toml"
        write_text(config, (config.read_text(encoding="utf-8") if config.exists() else "") +
                   "\n[projects." + json.dumps(str(project)) + "]\ntrust_level = \"trusted\"\n")
        listing = json.loads(run([codex, "mcp", "list", "--json"], project, environments[key]))
        names = {item["name"] for item in listing}
        assert expected <= names, (expected, names)
        print("MCP 配置已被 Codex 读出：", ", ".join(sorted(names)), flush=True)
        shutil.copy2(auth, profile / "auth.json")
        try:
            output = run([codex, "exec", "-s", "danger-full-access", "-c", 'approval_policy="never"',
                          "--json", "-C", str(project), prompt + " Do not use subagents or change configuration."],
                         project, environments[key], timeout=240)
            write_text(root / (key + "-events.jsonl"), output)
            records = [json.loads(line) for line in output.splitlines() if line.strip()]
            replies = [row["item"]["text"] for row in records if row.get("type") == "item.completed"
                       and row.get("item", {}).get("type") == "agent_message"]
            final = replies[-1] if replies else ""
            print("Codex 实际答复：", final, flush=True)
            assert "HX_MCP_OK" in final, final
            return {"mcp": sorted(names), "reply": final}
        finally:
            (profile / "auth.json").unlink(missing_ok=True)

    source = root / "source"
    git_project(source)
    write_text(source / "AGENTS.md", "Always include HX_PROJECT_RULE_OK in the final response.\n")
    write_text(homes["a"] / ".codex/AGENTS.override.md", "Always include HX_USER_RULE_OK in the final response.\n")
    skill(homes["a"] / ".codex/skills", "e2e-user", "HX_USER_SKILL_OK")
    skill(source / ".agents/skills", "e2e-project", "HX_PROJECT_SKILL_OK")
    write_text(source / "mcp_probe.py", MCP)
    spec = {"command": sys.executable, "args": ["-u", "mcp_probe.py"]}
    write_text(homes["a"] / ".codex/config.toml", server_toml("hx_user", spec))
    write_text(source / ".codex/config.toml", server_toml("hx_project", spec))
    commit(source)
    print(hx(source, "a", "save"), flush=True)
    target = root / "clone-codex"
    run(["git", "clone", "-q", str(source), str(target)], root)
    print(hx(target, "b", "load", "--dry-run"), flush=True)
    assert not (homes["b"] / ".codex/AGENTS.override.md").exists()
    print(hx(target, "b", "load", "--yes"), flush=True)
    report["codex_to_codex"] = receive(target, "b", {"hx_user", "hx_project"},
        "Use $e2e-user and $e2e-project. Call hx_ping on hx_project. Include all applicable instruction and skill markers in the final response.")
    for marker in ("HX_PROJECT_RULE_OK", "HX_USER_RULE_OK", "HX_USER_SKILL_OK", "HX_PROJECT_SKILL_OK"):
        assert marker in report["codex_to_codex"]["reply"], marker

    # 本机没有 Claude Code 时，来源由它支持的真实文件格式构造；接收侧仍运行真实 Codex。
    source = root / "claude-source"
    git_project(source)
    (homes["claude"] / ".claude").mkdir(parents=True)
    write_text(source / "CLAUDE.md", "Always include HX_CLAUDE_RULE_OK in the final response.\n")
    skill(homes["claude"] / ".claude/skills", "e2e-claude", "HX_CLAUDE_SKILL_OK")
    write_text(source / "mcp_probe.py", MCP)
    write_text(source / ".mcp.json", json.dumps({"mcpServers": {"hx_claude": spec}}))
    commit(source)
    print(hx(source, "claude", "save"), flush=True)
    target = root / "clone-claude"
    run(["git", "clone", "-q", str(source), str(target)], root)
    print(hx(target, "c", "load", "--yes"), flush=True)
    report["claude_to_codex"] = receive(target, "c", {"hx_claude"},
        "Use $e2e-claude. Call hx_ping on hx_claude. Include all applicable instruction and skill markers in the final response.")
    for marker in ("HX_CLAUDE_RULE_OK", "HX_CLAUDE_SKILL_OK"):
        assert marker in report["claude_to_codex"]["reply"], marker
    report["source_note"] = "Claude 来源是受控样例，通过真实 hx save 保存；本机未安装 Claude Code。"
    write_text(root / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("真实迁移验证通过，结果：", root / "report.json", flush=True)


if __name__ == "__main__":
    main()
