# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest>=8,<9", "tomlkit==0.13.3", "pyyaml>=6,<7"]
# ///
"""Offline installer regressions: uv run tests/test_setup_installer.py -q."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tomllib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVER = "ShucleRiderRAG"
START = "<!-- SHUCLIENTBOT_START -->"
END = "<!-- SHUCLIENTBOT_END -->"
TOOLS = {
    "agent_handshake", "lookup_issues", "lookup_pull_requests", "filter_issues",
    "expand_epic", "search_docs", "report_outcome",
}


@dataclass
class Installer:
    project: Path
    global_config: Path
    script: Path
    claude_state: Path
    env: dict[str, str]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(self.script), *args], cwd=self.project, env=self.env,
            text=True, capture_output=True, timeout=30,
        )


@pytest.fixture
def installer(tmp_path: Path) -> Installer:
    project = tmp_path / "consumer project"
    project.mkdir()
    global_config = tmp_path / "user config/config.toml"
    global_config.parent.mkdir()
    global_config.write_text('# Preserve comment\nmodel = "keep"\n')
    source = (ROOT / "setup-skills-and-mcp.sh").read_text()
    # Redirect only the global path, without changing HOME or CODEX_HOME.
    original = 'CODEX_CONFIG="${CODEX_HOME:-$HOME/.codex}/config.toml"'
    assert source.count(original) == 1
    script = tmp_path / "installer.sh"
    script.write_text(source.replace(original, f"CODEX_CONFIG={shlex.quote(str(global_config))}"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "assert sys.argv[1:6] == ['run', '--no-project', '--with', 'tomlkit==0.13.3', 'python']\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[6:]])\n"
    )
    uv.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/bash\n'
        '[ "$2" = "--max-time" ] && [ "$3" = "3" ] || exit 2\n'
        'printf "initialize -> HTTP 200 (mock)\\n"\n'
    )
    curl.chmod(0o755)
    claude_state = tmp_path / "claude-state.json"
    claude_state.write_text(json.dumps({
        "user": {SERVER: "http://user/mcp", "agentrag": "user-legacy"},
        "project": {SERVER: "http://shared/mcp"},
        "local": {SERVER: "http://old/mcp", "agentrag": "local-legacy"},
        "calls": [],
    }))
    claude = bin_dir / "claude"
    claude.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "path = Path(os.environ['SETUP_TEST_CLAUDE_STATE'])\n"
        "state = json.loads(path.read_text())\n"
        "args = sys.argv[1:]\n"
        "assert args[:4] == ['mcp', 'remove', '--scope', 'local'], args\n"
        "state['calls'].append(args)\n"
        "if os.environ.get('SETUP_TEST_REMOVE_FAILURE'):\n"
        "    print('Permission denied', file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "name = args[4]\n"
        "missing = name not in state['local']\n"
        "state['local'].pop(name, None)\n"
        "path.write_text(json.dumps(state))\n"
        "if missing:\n"
        "    print(f'No MCP server named \"{name}\" in local scope', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["SETUP_TEST_CLAUDE_STATE"] = str(claude_state)
    env.pop("SHUCLIENTBOT_MCP_URL", None)
    return Installer(project, global_config, script, claude_state, env)


@pytest.mark.parametrize("header", [
    "[mcp_servers.ShucleRiderRAG]",
    '[mcp_servers."ShucleRiderRAG"]',
    "  [ mcp_servers . 'ShucleRiderRAG' ]",
])
def test_main_mcp_cleanup_preserves_other_settings(installer: Installer, header: str):
    original = (
        '# Keep this comment\nmodel = "keep"\n'
        'developer_instructions = """\nExample:\n'
        '[mcp_servers.ShucleRiderRAG]\nurl = "http://example/mcp"\n"""\n'
        f'{header}\nurl = "http://old/mcp"\n'
        '[mcp_servers.ShucleRiderRAG.http_headers]\nX-Old = "old"\n'
        '[mcp_servers.agentrag]\ncommand = "old"\n'
        '  [mcp_servers.other]\n  url = "http://other/mcp"\n'
        '  [mcp_servers.other.http_headers]\n  X-Keep = "yes"\n'
    )
    expected = tomllib.loads(original)
    installer.global_config.write_text(original)
    installer.global_config.chmod(0o640)
    local = installer.project / ".codex/config.toml"
    local.parent.mkdir()
    local.write_text(original)
    result = installer.run("--local")
    assert result.returncode == 0, result.stderr
    for path in (installer.global_config, local):
        config = tomllib.loads(path.read_text())
        assert config["model"] == expected["model"]
        assert config["developer_instructions"] == expected["developer_instructions"]
        assert config["mcp_servers"] == {"other": expected["mcp_servers"]["other"]}
        assert "# Keep this comment" in path.read_text()
        assert any(p.read_text() == original for p in path.parent.glob("config.toml.bak.*"))
    assert installer.global_config.stat().st_mode & 0o777 == 0o640


def test_reinstall_preserves_rules_permissions_and_claude_scopes(installer: Installer):
    agents = installer.project / "AGENTS.md"
    agents.write_text(f"# Existing\nKeep before.\n{START}\nOld\n{END}\n## Rules\nKeep after.\n")
    settings = installer.project / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "permissions": {"allow": ["Read"], "deny": ["Bash(rm:*)"]}, "other": 42,
    }))
    original_state = json.loads(installer.claude_state.read_text())
    first = installer.run("--local", "--update-agents")
    assert first.returncode == 0, first.stderr
    paths = [agents, settings, installer.global_config]
    before = {path: path.read_bytes() for path in paths}
    second = installer.run("--local", "--update-agents")
    assert second.returncode == 0, second.stderr
    assert before == {path: path.read_bytes() for path in paths}
    assert "Keep before." in agents.read_text() and "Keep after." in agents.read_text()
    assert "Old" not in agents.read_text()
    assert agents.read_text().count(START) == agents.read_text().count(END) == 1
    permissions = json.loads(settings.read_text())
    assert permissions == {
        "permissions": {"allow": ["Read", "mcp__ShucleRiderRAG__*"], "deny": ["Bash(rm:*)"]},
        "other": 42,
    }
    state = json.loads(installer.claude_state.read_text())
    assert state["user"] == original_state["user"]
    assert state["project"] == original_state["project"]
    assert state["local"] == {}
    assert not (installer.project / ".codex/config.toml").exists()


@pytest.mark.parametrize("invalid_file, content", [
    ("AGENTS.md", f"# Rules\n{START}\nOld\n## Important\nKeep after.\n"),
    ("AGENTS.md", f"{END}\nKeep after.\n"),
    ("AGENTS.md", f"{START}\n{START}\n{END}\nKeep after.\n"),
    (".codex/config.toml", '[mcp_servers.ShucleRiderRAG]\nurl = "unterminated'),
    (".codex/config.toml", 'mcp_servers = "wrong type"\n'),
    ("global", '[mcp_servers.ShucleRiderRAG]\nurl = "unterminated'),
    (".claude/settings.local.json", '{"permissions":'),
])
def test_preflight_failure_preserves_files(installer: Installer, invalid_file: str, content: str):
    target = installer.global_config if invalid_file == "global" else installer.project / invalid_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    before = {p: p.read_bytes() for p in installer.project.rglob("*") if p.is_file()}
    before[installer.global_config] = installer.global_config.read_bytes()
    state = installer.claude_state.read_bytes()
    extra = ("--update-agents",) if invalid_file == "AGENTS.md" else ()
    result = installer.run("--local", *extra)
    assert result.returncode != 0 and "오류:" in result.stderr
    after = {p: p.read_bytes() for p in installer.project.rglob("*") if p.is_file()}
    after[installer.global_config] = installer.global_config.read_bytes()
    assert before == after
    assert state == installer.claude_state.read_bytes()
    assert not list(installer.global_config.parent.glob("*.bak.*"))


def test_agents_keep_scoped_mcp_and_current_tools(installer: Installer):
    url = 'http://localhost:8848/mcp?value="quoted"'
    result = installer.run("--url", url)
    assert result.returncode == 0, result.stderr
    markdown = (installer.project / ".claude/agents/shuclientbot.md").read_text()
    _, header, body = markdown.split("---", 2)
    agent = yaml.safe_load(header)
    assert agent["name"] == "shuclientbot" and agent["model"] == "inherit"
    assert {t.strip() for t in agent["tools"].split(",")} == {
        f"mcp__{SERVER}__{name}" for name in TOOLS
    }
    assert agent["mcpServers"] == [{SERVER: {"type": "http", "url": url}}]
    assert "repository" in body and "lookup_pull_requests" in body
    codex = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    assert codex["name"] == "shuclientbot"
    assert "lookup_pull_requests" in codex["developer_instructions"]
    assert "repository" in codex["developer_instructions"]
    assert codex["mcp_servers"][SERVER] == {
        "url": url, "enabled": True, "startup_timeout_sec": 3,
        "default_tools_approval_mode": "approve",
    }
    skill = (installer.project / ".claude/skills/shuclientbot/SKILL.md").read_text()
    assert "lookup_pull_requests" in skill and "서브에이전트(Agent)" in skill
    assert not (installer.project / "AGENTS.md").exists()


def test_missing_global_config_is_not_created(installer: Installer):
    installer.global_config.unlink()
    result = installer.run("--local")
    assert result.returncode == 0, result.stderr
    assert not installer.global_config.exists()
    assert not (installer.project / ".codex/config.toml").exists()
    assert (installer.project / ".codex/agents/shuclientbot.toml").exists()
    assert not (installer.project / "AGENTS.md").exists()


def test_claude_removal_errors_are_not_ignored(installer: Installer):
    installer.env["SETUP_TEST_REMOVE_FAILURE"] = "1"
    before = installer.global_config.read_bytes()
    result = installer.run("--local")
    assert result.returncode != 0
    assert "Permission denied" in result.stderr and "Claude local MCP 제거 실패" in result.stderr
    assert installer.global_config.read_bytes() == before
    assert not list(installer.project.iterdir())


def test_crlf_markers_are_replaced(installer: Installer):
    agents = installer.project / "AGENTS.md"
    agents.write_bytes(f"# Existing\r\n{START}\r\nOld\r\n{END}\r\nKeep after.\r\n".encode())
    result = installer.run("--local", "--update-agents")
    assert result.returncode == 0, result.stderr
    assert "Old" not in agents.read_text() and "Keep after." in agents.read_text()
    assert agents.read_text().count(START) == agents.read_text().count(END) == 1


def test_public_script_contains_no_tailscale_endpoint(installer: Installer):
    source = installer.script.read_text()
    assert "TAILSCALE_URL" not in source and ".ts.net" not in source
    result = installer.run("--tailscale")
    assert result.returncode == 2
    assert not json.loads(installer.claude_state.read_text())["calls"]


@pytest.mark.parametrize("args", [("--url",), ("--unknown",)])
def test_invalid_arguments_do_not_change_settings(installer: Installer, args: tuple[str, ...]):
    before = installer.global_config.read_bytes()
    result = installer.run(*args)
    assert result.returncode == 2
    assert installer.global_config.read_bytes() == before
    assert not list(installer.project.iterdir())
    assert not json.loads(installer.claude_state.read_text())["calls"]


@pytest.mark.parametrize("args, expected", [
    ((), "http://configured.example/mcp"),
    (("--local",), "http://127.0.0.1:8848/mcp"),
])
def test_environment_url_and_cli_precedence(installer: Installer, args: tuple[str, ...], expected: str):
    installer.env["SHUCLIENTBOT_MCP_URL"] = "http://configured.example/mcp"
    result = installer.run(*args)
    assert result.returncode == 0, result.stderr
    agent = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    assert agent["mcp_servers"][SERVER]["url"] == expected


def test_lan_default_is_preserved(installer: Installer):
    result = installer.run()
    assert result.returncode == 0, result.stderr
    agent = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    assert agent["mcp_servers"][SERVER]["url"] == "http://HMC123ui-Mac-Studio.local:8848/mcp"


@pytest.mark.parametrize("update_agents", [False, True])
def test_script_runs_from_stdin(installer: Installer, update_agents: bool):
    extra = ["--update-agents"] if update_agents else []
    result = subprocess.run(
        ["/bin/bash", "-s", "--", "--local", *extra], input=installer.script.read_text(),
        cwd=installer.project, env=installer.env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "✅ 완료" in result.stdout
    assert (installer.project / ".codex/agents/shuclientbot.toml").exists()
    assert (installer.project / "AGENTS.md").exists() == update_agents


@pytest.mark.parametrize("original", [
    b"# Existing rules\nKeep this file.\n",
    f"{START}\nExisting ShuClientBot instructions\n{END}\n".encode(),
    f"{START}\nUnclosed block\n## Important\nKeep after.\n".encode(),
    f"{END}\nOrphan end marker\n".encode(),
    f"# Existing\r\n{START}\r\nOld\r\n{END}\r\nKeep after.\r\n".encode(),
])
def test_default_install_preserves_agents_exactly(installer: Installer, original: bytes):
    agents = installer.project / "AGENTS.md"
    agents.write_bytes(original)
    agents.chmod(0o444)
    before = agents.stat()

    result = installer.run("--local")

    assert result.returncode == 0, result.stderr
    assert agents.read_bytes() == original
    after = agents.stat()
    assert (after.st_ino, after.st_mode, after.st_mtime_ns) == (
        before.st_ino, before.st_mode, before.st_mtime_ns,
    )
    assert (installer.project / ".codex/agents/shuclientbot.toml").exists()


def test_update_agents_creates_missing_file(installer: Installer):
    result = installer.run("--local", "--update-agents")
    assert result.returncode == 0, result.stderr
    content = (installer.project / "AGENTS.md").read_text()
    assert content.count(START) == content.count(END) == 1
    assert "lookup_pull_requests" in content


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(__file__), *sys.argv[1:]]))
