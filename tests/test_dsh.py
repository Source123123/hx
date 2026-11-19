"""DeepSeek Harness adapter 的测试。

运行：python -m unittest tests.test_dsh -v
（或 python -m unittest discover -s tests）
"""
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from hx import core
from hx.adapters.deepseek_harness import (DeepSeekHarnessAdapter, _node_executable, digest_session, dsh_to_neutral,
                                          merge_mcp_yaml, parse_entry_config, parse_mcp_servers,
                                          project_key, read_session_texts)
from hx.portable import Rebaser
from hx.util import HARNESS_DIR

SECRET = "sk-dsh-" + "a1b2c3d4e5" * 3


def new_dir(*parts):
    """不经过 tempfile.mkdtemp 的临时目录：某些受限环境给 0o700 目录建子目录会失败。"""
    root = Path(tempfile.gettempdir()) / ("hx-dsh-" + uuid.uuid4().hex)
    path = root.joinpath(*parts)
    path.mkdir(parents=True)
    return root, path


class DshTest(unittest.TestCase):
    def setUp(self):
        self.root, self.project = new_dir("project")
        self.home_a = self.root / "home-a"
        self.home_b = self.root / "home-b"
        self.dsh_a = self.home_a / ".dsh"
        self.profile_a = self.dsh_a / "profiles/desktop"
        self.profile_a.mkdir(parents=True)
        self.shared_a = self.home_a / ".agents"
        (self.shared_a / "skills").mkdir(parents=True)
        (self.project / ".dsh/skills/project-skill").mkdir(parents=True)
        (self.project / "AGENTS.md").write_text("# project\n\nFollow these.\n", encoding="utf-8")
        (self.project / ".dsh/skills/project-skill/SKILL.md").write_text(
            "---\nname: project-skill\ndescription: project skill\n---\n\nProject skill.\n", encoding="utf-8")

        (self.dsh_a / "AGENTS.md").write_text(f"# user rules\n\nkey {SECRET}\n", encoding="utf-8")
        (self.dsh_a / "skills/user-skill").mkdir(parents=True)
        (self.dsh_a / "skills/user-skill/SKILL.md").write_text(
            "---\nname: user-skill\ndescription: user skill\n---\n\nUser skill.\n", encoding="utf-8")
        (self.dsh_a / "skills/.system/builtin").mkdir(parents=True)
        (self.dsh_a / "skills/.system/builtin/SKILL.md").write_text("builtin\n", encoding="utf-8")
        (self.shared_a / "AGENTS.md").write_text("# shared rules\n", encoding="utf-8")
        (self.shared_a / "skills/shared-skill").mkdir(parents=True)
        (self.shared_a / "skills/shared-skill/SKILL.md").write_text(
            "---\nname: shared-skill\ndescription: shared\n---\n\nShared.\n", encoding="utf-8")

        (self.profile_a / "cordis.patch.yml").write_text(
            "# profile\n"
            "- id: agent-default-model\n"
            "  name: '@deepseek-ai/dsh-agent-default-model'\n"
            "  config:\n"
            "    provider: deepseek-official\n"
            "    model: deepseek-flash\n\n"
            "- id: mcp-manager\n"
            "  name: '@deepseek-ai/dsh-mcp-manager'\n"
            "  inject: [credentials]\n"
            "  config:\n"
            "    servers:\n"
            "      github:\n"
            "        transport: stdio\n"
            "        command: npx\n"
            "        args:\n"
            "          - -y\n"
            "          - '@modelcontextprotocol/server-github'\n"
            "        env:\n"
            "          GITHUB_TOKEN: '${credential:GITHUB_TOKEN}'\n"
            "      api:\n"
            "        transport: streamable-http\n"
            "        url: https://example.com/mcp\n"
            "        headers:\n"
            "          Authorization: 'Bearer ${credential:MCP_TOKEN}'\n",
            encoding="utf-8")
        (self.profile_a / "package.json").write_text(json.dumps({
            "name": "dsh-profile-desktop",
            "dependencies": {"@deepseek-ai/dsh-subagent-codex": "0.2.1-rc.3"},
            "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]}},
        }, indent=2), encoding="utf-8")
        installed = self.profile_a / "node_modules/@deepseek-ai/dsh-subagent-codex"
        installed.mkdir(parents=True)
        (installed / "package.json").write_text(json.dumps(
            {"name": "@deepseek-ai/dsh-subagent-codex", "version": "0.2.1-rc.3"}), encoding="utf-8")
        (self.dsh_a / ".credentials.yaml").write_text(
            "version: 1\nrecords: {}\nrefs:\n  DEEPSEEK_API_KEY: deepseek-account-platform/default\n"
            "  GITHUB_TOKEN: github/default\n", encoding="utf-8")
        (self.dsh_a / ".env").write_text("DEEPSEEK_API_KEY=" + SECRET + "\nDEBUG=1\n", encoding="utf-8")

        session = self._session_dir(self.project)
        session.mkdir(parents=True)
        (session / "session.v4.jsonl").write_text("\n".join(json.dumps(x) for x in self._events()) + "\n",
                                                  encoding="utf-8")

        self.old_home = os.environ.get("HX_HOME")
        os.environ["HX_HOME"] = str(self.home_a)
        os.environ.pop("DSH_HOME", None)
        os.environ.pop("DSH_AGENTS_HOME", None)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HX_HOME", None)
        else:
            os.environ["HX_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def _session_dir(self, project):
        return self.dsh_a / "sessions" / project_key(str(project.resolve())) / "sess-1"

    def _events(self):
        project = str(self.project.resolve())
        return [
            {"type": "session", "version": 4, "id": "sess-1", "createdAt": 1795000000000,
             "cwd": project, "isSeeded": False, "delegationDepth": 0, "agentPreset": "standard"},
            {"type": "request/context", "seq": 1, "time": 1795000001000,
             "data": {"provider": "deepseek-official", "model": "deepseek-flash"}},
            {"type": "user/message", "seq": 2, "time": 1795000002000,
             "data": {"content": [{"type": "text", "text": "add a login page"}],
                      "source": {"kind": "user"}, "role": "user"}},
            {"type": "user/message", "seq": 3, "time": 1795000002500,
             "data": {"content": [{"type": "text", "text": "runtime noise"}],
                      "source": {"kind": "runtime-context"}, "role": "user"}},
            {"type": "assistant/message", "seq": 4, "time": 1795000003000,
             "data": {"turn": 1, "step": 1, "message": {"role": "assistant", "content": [
                 {"type": "reasoning", "text": "thinking"}, {"type": "text", "text": "Done."}]}}},
            {"type": "tool/call", "seq": 5, "time": 1795000004000,
             "data": {"name": "write", "arguments": json.dumps({"file_path": project + "\\login.py"})}},
            {"type": "tool/call", "seq": 6, "time": 1795000005000,
             "data": {"name": "apply_patch",
                      "arguments": json.dumps({"input": "*** Begin Patch\n*** Add File: notes.md\n+x\n*** End Patch"})}},
            {"type": "turn/end", "seq": 7, "time": 1795000006000,
             "data": {"turn": 1, "reason": {"kind": "completed"}}},
        ]

    def test_capture_config_skills_mcp_and_sessions(self):
        manifest = core.init(self.project, "deepseek-harness")
        self.assertEqual(manifest["agents"], ["deepseek-harness"])
        result = core.capture(self.project)
        harness = self.project / HARNESS_DIR
        entries = {entry["path"]: entry for entry in result["lock"]["entries"]}

        self.assertIn("AGENTS.md", entries)
        self.assertEqual(entries["AGENTS.md"]["scope"], "project")
        self.assertIn(".dsh/skills/project-skill/SKILL.md", entries)
        self.assertIn(f"{HARNESS_DIR}/agent/deepseek-harness/user/skills/user-skill/SKILL.md", entries)
        self.assertIn(f"{HARNESS_DIR}/agent/deepseek-harness/shared-skills/shared-skill/SKILL.md", entries)
        self.assertNotIn(".system", "\n".join(entries))
        self.assertEqual(entries[f"{HARNESS_DIR}/agent/deepseek-harness/user/AGENTS.md"]["target"],
                         "user:AGENTS.md")
        self.assertEqual(entries[f"{HARNESS_DIR}/agent/deepseek-harness/profile/desktop/cordis.patch.yml"]["target"],
                         "user:profiles/desktop/cordis.patch.yml")

        packed = (harness / "agent/deepseek-harness/mcp.json").read_text(encoding="utf-8")
        self.assertNotIn(SECRET, packed)
        servers = json.loads(packed)["mcpServers"]
        self.assertEqual(servers["github"]["env"], {"GITHUB_TOKEN": "${GITHUB_TOKEN}"})
        self.assertEqual(servers["api"]["type"], "http")
        self.assertEqual(servers["api"]["headers"]["Authorization"], "Bearer ${MCP_TOKEN}")

        digest = json.loads((harness / "context/sessions/sess-1.json").read_text(encoding="utf-8"))
        self.assertEqual(digest["prompts"], ["add a login page"])
        self.assertEqual(digest["files_changed"], ["login.py", "notes.md"])
        self.assertEqual(digest["tool_counts"], {"apply_patch": 1, "write": 1})
        self.assertEqual(digest["models"], ["deepseek-flash"])
        self.assertEqual(digest["last_reply"], "Done.")

        manifest = json.loads((harness / "harness.json").read_text(encoding="utf-8"))
        self.assertIn("GITHUB_TOKEN", manifest["secrets"])
        self.assertIn("MCP_TOKEN", manifest["secrets"])
        self.assertIn("DEEPSEEK_API_KEY", manifest["secrets"])
        self.assertNotIn("DEBUG", manifest["secrets"])     # 非密钥值只留在参考副本里
        credentials = json.loads((harness / "agent/deepseek-harness/credentials.json").read_text(encoding="utf-8"))
        self.assertEqual(credentials["refs"], ["DEEPSEEK_API_KEY", "GITHUB_TOKEN"])
        env_reference = (harness / "agent/deepseek-harness/user/env.reference").read_text(encoding="utf-8")
        self.assertNotIn(SECRET, env_reference)
        self.assertIn("DEBUG=1", env_reference)
        plugins = json.loads((harness / "agent/deepseek-harness/plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(plugins["bundles"], ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"])
        self.assertEqual(plugins["installed"]["@deepseek-ai/dsh-subagent-codex"], "0.2.1-rc.3")
        self.assertEqual(result["lock"]["model"], "deepseek-flash")
        self.assertEqual(result["lock"]["agents"]["deepseek-harness"]["version"], None)

    def test_materialize_restores_user_files_and_mcp(self):
        core.init(self.project, "deepseek-harness")
        core.capture(self.project)
        (self.home_b / ".dsh").mkdir(parents=True)

        os.environ["HX_HOME"] = str(self.home_b)
        actions = core.materialize(self.project)
        written = [action for action in actions if action.status == "written"]
        self.assertTrue(written)

        dsh_b = self.home_b / ".dsh"
        self.assertEqual((dsh_b / "AGENTS.md").read_text(encoding="utf-8").splitlines()[0], "# user rules")
        self.assertIn("User skill.", (dsh_b / "skills/user-skill/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("Shared.", (self.home_b / ".agents/skills/shared-skill/SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual((self.home_b / ".agents/AGENTS.md").read_text(encoding="utf-8"), "# shared rules\n")
        patch = (dsh_b / "profiles/desktop/cordis.patch.yml").read_text(encoding="utf-8")
        servers = parse_mcp_servers(patch)
        self.assertEqual(servers["github"]["command"], "npx")
        self.assertEqual(servers["github"]["env"], {"GITHUB_TOKEN": "${credential:GITHUB_TOKEN}"})
        self.assertIn("Bearer ${credential:MCP_TOKEN}", patch)

        # 再跑一次幂等
        again = core.materialize(self.project)
        self.assertFalse([action for action in again if action.status == "written"])
        self.assertEqual(parse_entry_config(patch, "agent-default-model")["model"], "deepseek-flash")

    def test_merge_mcp_adds_without_replacing_and_reports_conflict(self):
        path = self.root / "cordis.patch.yml"
        path.write_text(
            "- id: mcp-manager\n"
            "  name: '@deepseek-ai/dsh-mcp-manager'\n"
            "  config:\n"
            "    servers:\n"
            "      old:\n"
            "        transport: stdio\n"
            "        command: old-cmd\n",
            encoding="utf-8")
        actions = merge_mcp_yaml(path, {"new": {"command": "new-cmd"}})
        self.assertEqual([action.status for action in actions], ["written"])
        servers = parse_mcp_servers(path.read_text(encoding="utf-8"))
        self.assertEqual(set(servers), {"old", "new"})
        self.assertEqual(servers["new"]["command"], "new-cmd")
        actions = merge_mcp_yaml(path, {"new": {"command": "new-cmd"}})
        self.assertEqual([action.status for action in actions], ["same"])
        actions = merge_mcp_yaml(path, {"new": {"command": "different"}})
        self.assertEqual([action.status for action in actions], ["conflict"])

    def test_merge_mcp_creates_entry(self):
        path = self.root / "empty.patch.yml"
        path.write_text("- id: ui-theme\n  name: x\n  config:\n    fontSize: 20\n", encoding="utf-8")
        merge_mcp_yaml(path, {"db": {"command": "dbmcp"}})
        text = path.read_text(encoding="utf-8")
        self.assertIn("- id: mcp-manager", text)
        self.assertEqual(parse_mcp_servers(text)["db"]["command"], "dbmcp")

    def test_digest_ignores_injected_messages_and_filters_credentials(self):
        text = "\n".join(json.dumps(x) for x in self._events())
        digest = digest_session(text, self.project, "sess-1")
        self.assertEqual(digest["prompts"], ["add a login page"])
        self.assertNotIn("runtime noise", digest["prompts"])
        self.assertNotIn("thinking", digest["last_reply"])
        self.assertTrue(digest["started"].startswith("2026-"))
        self.assertTrue(digest["ended"].startswith("2026-"))

    def test_export_neutral_then_adopt(self):
        core.init(self.project, "deepseek-harness")
        core.capture(self.project)
        adapter = DeepSeekHarnessAdapter()
        neutral = adapter.export_neutral(self.project)
        self.assertIn("shared-skill/SKILL.md", neutral.skills)
        self.assertIn("github", neutral.mcp)
        self.assertEqual(neutral.instructions, ["AGENTS.md"])

        empty = self.root / "empty-project"
        empty.mkdir()
        actions = adapter.adopt(empty, self.home_b, neutral, force=False)
        self.assertTrue([action for action in actions if action.status == "written"])
        text = (empty / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", text)
        self.assertIn("HANDOFF.md", text)

    def test_project_key_matches_dsh_layout(self):
        self.assertEqual(project_key(r"C:\Users\alice\work\demo"),
                         "--C-Users-alice-work-demo--")
        self.assertEqual(project_key("C:/Users/alice/work/demo"),
                         "--C-Users-alice-work-demo--")
        self.assertEqual(project_key("/home/alice/demo"), "--home-alice-demo--")

    def test_zstd_session_is_read_with_node_if_available(self):
        node = shutil.which("node") or _node_executable(Path.home())
        if not node:
            self.skipTest("node 不可用，跳过 zstd 往返")
        source = self._session_dir(self.project) / "session.v4.jsonl"
        text = source.read_text(encoding="utf-8")
        source.unlink()
        script = (
            "const fs=require('node:fs'),z=require('node:zlib');"
            "const b=Buffer.from(fs.readFileSync(0,'utf8'));"
            "const f=z.zstdCompressSync(b,{params:{[z.constants.ZSTD_c_checksumFlag]:1}});"
            "fs.writeFileSync(process.argv[1],f);"
        )
        import subprocess
        subprocess.run([node, "-e", script, str(source.with_suffix(".jsonl.zstd"))],
                       input=text.encode("utf-8"), check=True)
        old_node = os.environ.get("HX_NODE")
        os.environ["HX_NODE"] = node
        try:
            texts, error = read_session_texts([source.with_suffix(".jsonl.zstd")], self.home_a)
        finally:
            if old_node is None:
                os.environ.pop("HX_NODE", None)
            else:
                os.environ["HX_NODE"] = old_node
        self.assertIsNone(error)
        self.assertIn("add a login page", list(texts.values())[0])


if __name__ == "__main__":
    unittest.main()
