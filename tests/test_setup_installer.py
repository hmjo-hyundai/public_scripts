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
        "if sys.argv[1:6] == ['run', '--no-project', '--with', 'mcp-proxy==0.13.0', 'python']:\n"
        "    assert sys.argv[6:] == ['-c', 'import mcp_proxy']\n"
        "    sys.exit(1 if os.environ.get('SETUP_TEST_BRIDGE_FAILURE') else 0)\n"
        "assert sys.argv[1:6] == ['run', '--no-project', '--with', 'tomlkit==0.13.3', 'python']\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[6:]])\n"
    )
    uv.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        f'#!{sys.executable}\n'
        'import json,os,sys\n'
        'from pathlib import Path\n'
        'assert sys.argv[1:4] == ["-sS", "--max-time", "3"]\n'
        'Path(os.environ["SETUP_TEST_CURL_ARGS"]).write_text(json.dumps(sys.argv[1:]))\n'
        'print(os.environ.get("SETUP_TEST_HTTP_STATUS", "200"), end="")\n'
        'sys.exit(int(os.environ.get("SETUP_TEST_CURL_EXIT", "0")))\n'
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
        "state.setdefault('cwds', []).append(os.getcwd())\n"
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
    env["SETUP_TEST_CURL_ARGS"] = str(tmp_path / "curl-args.json")
    env.pop("SHUCLIENTBOT_MCP_URL", None)
    return Installer(project, global_config, script, claude_state, env)


def mcp_target(server: dict) -> str:
    return server["url"] if "url" in server else server["args"][-1]


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
    claude_server = agent["mcpServers"][0][SERVER]
    assert claude_server["type"] == "stdio"
    assert mcp_target(claude_server) == url
    assert "repository" in body and "lookup_pull_requests" in body
    codex = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    assert codex["name"] == "shuclientbot"
    assert "lookup_pull_requests" in codex["developer_instructions"]
    assert "repository" in codex["developer_instructions"]
    codex_server = codex["mcp_servers"][SERVER]
    assert codex_server["command"] == claude_server["command"]
    assert codex_server["args"] == claude_server["args"]
    assert codex_server["enabled"] is True
    assert codex_server["startup_timeout_sec"] == 20
    assert codex_server["default_tools_approval_mode"] == "approve"
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
    assert mcp_target(agent["mcp_servers"][SERVER]) == expected


def test_lan_default_is_preserved(installer: Installer):
    result = installer.run()
    assert result.returncode == 0, result.stderr
    agent = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    assert mcp_target(agent["mcp_servers"][SERVER]) == "http://HMC123ui-Mac-Studio.local:8848/mcp"


@pytest.mark.parametrize("url", [
    "http://Mac-Studio.LOCAL.:8848/mcp", "http://localhost:8848/mcp",
    "http://127.0.0.1:8848/mcp", "http://10.20.30.40:8848/mcp",
    "http://[::1]:8848/mcp", "http://[fe80::1234%25wlan0]:8848/mcp",
])
def test_lan_connections_bypass_proxy_at_check_and_runtime(installer: Installer, url: str):
    installer.env["HTTP_PROXY"] = "http://proxy.example:8080"
    installer.env["NO_PROXY"] = "keep.example"
    result = installer.run("--url", url)
    assert result.returncode == 0, result.stderr
    codex = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    server = codex["mcp_servers"][SERVER]
    args = server["args"]
    assert args[:5] == ["run", "--offline", "--no-project", "--with", "mcp-proxy==0.13.0"]
    assert Path(server["command"]).is_absolute()
    assert mcp_target(server) == url
    curl = json.loads(Path(installer.env["SETUP_TEST_CURL_ARGS"]).read_text())
    assert curl[curl.index("--noproxy") + 1] == "*"
    # Execute the generated child code with runpy replaced: no real server access.
    code = args[args.index("-c") + 1]
    probe = (
        "import os,json,runpy; "
        "runpy.run_module=lambda *a,**k: print(json.dumps({n:os.environ.get(n) "
        "for n in ('HTTP_PROXY','NO_PROXY','no_proxy')})); " + code
    )
    child = subprocess.run([sys.executable, "-c", probe], env=installer.env,
                           capture_output=True, text=True, check=True)
    assert json.loads(child.stdout) == {
        "HTTP_PROXY": "http://proxy.example:8080", "NO_PROXY": "*", "no_proxy": "*",
    }
    assert installer.env["NO_PROXY"] == "keep.example"


def test_public_https_keeps_native_http_transport_and_proxy(installer: Installer):
    url = "https://mcp.example.com/mcp"
    installer.env["SETUP_TEST_BRIDGE_FAILURE"] = "1"
    result = installer.run("--url", url)
    assert result.returncode == 0, result.stderr
    agent = tomllib.loads((installer.project / ".codex/agents/shuclientbot.toml").read_text())
    assert agent["mcp_servers"][SERVER] == {
        "url": url, "enabled": True, "startup_timeout_sec": 3,
        "default_tools_approval_mode": "approve",
    }
    curl = json.loads(Path(installer.env["SETUP_TEST_CURL_ARGS"]).read_text())
    assert "--noproxy" not in curl


def test_bridge_dependency_failure_does_not_change_configuration(installer: Installer):
    installer.env["SETUP_TEST_BRIDGE_FAILURE"] = "1"
    before = installer.global_config.read_bytes()
    state = installer.claude_state.read_bytes()
    result = installer.run()
    assert result.returncode != 0
    assert "MCP 연결 도구 준비 실패" in result.stderr
    assert installer.global_config.read_bytes() == before
    assert installer.claude_state.read_bytes() == state
    assert not list(installer.project.iterdir())


@pytest.mark.parametrize("status, exit_code", [("000", "28"), ("403", "0"), ("500", "0")])
def test_unreachable_mcp_is_not_reported_as_connected(installer: Installer, status, exit_code):
    installer.env["SETUP_TEST_HTTP_STATUS"] = status
    installer.env["SETUP_TEST_CURL_EXIT"] = exit_code
    result = installer.run()
    assert result.returncode == 0, result.stderr
    assert "설정 설치 완료, MCP 연결 미확인" in result.stdout
    assert "✅ 완료" not in result.stdout
    assert (installer.project / ".codex/agents/shuclientbot.toml").exists()


@pytest.mark.parametrize("url", ["file:///tmp/mcp", "http://", "http://localhost:wrong/mcp"])
def test_invalid_url_does_not_change_settings(installer: Installer, url: str):
    before = installer.global_config.read_bytes()
    state = installer.claude_state.read_bytes()
    result = installer.run("--url", url)
    assert result.returncode != 0
    assert installer.global_config.read_bytes() == before
    assert installer.claude_state.read_bytes() == state
    assert not list(installer.project.iterdir())


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



def init_reference_repo(path: Path, remote: str | None = None) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    if remote:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote],
            check=True, capture_output=True,
        )


def installed_reference(target: Path) -> dict:
    agent = tomllib.loads((target / ".codex/agents/shuclientbot.toml").read_text())
    instructions = agent["developer_instructions"]
    context = next(line for line in instructions.splitlines() if line.startswith('{"local_path":'))
    for relative in (".claude/agents/shuclientbot.md", ".claude/skills/shuclientbot/SKILL.md"):
        assert context in (target / relative).read_text()
    assert "기획·정책·Slack 질문은 로컬 레포 없이" in instructions
    return json.loads(context)


@pytest.mark.parametrize("remote", [
    "git@github.com:hkmc-airlab/shucle-rider-ios.git",
    "https://fake-user:fake-token@github.com/hkmc-airlab/shucle-rider-ios.git",
    "ssh://git@github.com/hkmc-airlab/shucle-rider-ios.git",
])
def test_target_and_reference_are_relative_to_invocation(installer: Installer, remote: str):
    target = installer.project.parent / "별도 작업 폴더"
    target.mkdir()
    repo = installer.project.parent / '참조 """ 레포\n두번째 줄'
    init_reference_repo(repo, remote)
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    args = ("--target", "../" + target.name, "--repo", "../" + repo.name, "--local")

    result = installer.run(*args)

    assert result.returncode == 0, result.stderr
    assert installed_reference(target) == {
        "local_path": str(repo.resolve()), "repository": "hkmc-airlab/shucle-rider-ios",
    }
    assert not list(installer.project.iterdir())
    assert before == {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert str(target.resolve()) in result.stdout
    state = json.loads(installer.claude_state.read_text())
    assert set(state["cwds"]) == {str(target.resolve())}
    for file in target.rglob("*"):
        if file.is_file():
            assert "fake-token" not in file.read_text()
    assert "fake-token" not in result.stdout + result.stderr

    files = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    result = installer.run(*args)
    assert result.returncode == 0, result.stderr
    assert files == {p: p.read_bytes() for p in files}


@pytest.mark.parametrize("remote, expected", [
    (None, None),
    ("https://git.example.com/team/project.git", None),
    ("https://github.com/hkmc-airlab/shucle-rider-android.git", "hkmc-airlab/shucle-rider-android"),
])
def test_target_git_repo_is_default_reference(installer: Installer, remote, expected):
    target = installer.project.parent / "reference"
    init_reference_repo(target, remote)

    result = installer.run("--target", str(target), "--local")

    assert result.returncode == 0, result.stderr
    assert installed_reference(target) == {"local_path": str(target.resolve()), "repository": expected}
    assert not list(installer.project.iterdir())


def test_reference_worktree(installer: Installer):
    repo = installer.project.parent / "repo"
    init_reference_repo(repo, "git@github.com:hkmc-airlab/shucle-rider-ios.git")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Installer Test", "-c",
         "user.email=installer@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "--allow-empty", "-m", "fixture"],
        check=True, capture_output=True,
    )
    worktree = installer.project.parent / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree)],
        check=True, capture_output=True,
    )

    result = installer.run("--repo", str(worktree), "--local")

    assert result.returncode == 0, result.stderr
    assert installed_reference(installer.project) == {
        "local_path": str(worktree.resolve()), "repository": "hkmc-airlab/shucle-rider-ios",
    }
    assert not (worktree / ".claude").exists()
    assert not (worktree / ".codex").exists()


@pytest.mark.parametrize("args", [
    ("--target",), ("--repo",), ("--target", "--local"), ("--repo", "--local"),
    ("--target", "missing"), ("--repo", "missing"), ("--repo", "."),
])
def test_invalid_folder_arguments_leave_settings_untouched(installer: Installer, args):
    before = installer.global_config.read_bytes()
    state = installer.claude_state.read_bytes()

    result = installer.run(*args)

    assert result.returncode != 0
    assert "오류:" in result.stderr
    assert not list(installer.project.iterdir())
    assert installer.global_config.read_bytes() == before
    assert installer.claude_state.read_bytes() == state


def test_piped_install_to_plain_folder(installer: Installer):
    target = installer.project.parent / "plain folder"
    target.mkdir()
    result = subprocess.run(
        ["/bin/bash", "-s", "--", "--target", str(target), "--local", "--update-agents"],
        input=installer.script.read_text(), cwd=installer.project, env=installer.env,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (target / "AGENTS.md").exists()
    agent = tomllib.loads((target / ".codex/agents/shuclientbot.toml").read_text())
    assert '"local_path":' not in agent["developer_instructions"]
    assert "기획·정책·Slack 질문은 로컬 레포 없이" in agent["developer_instructions"]
    assert not list(installer.project.iterdir())


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(__file__), *sys.argv[1:]]))
