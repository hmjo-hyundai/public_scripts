# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest>=8,<9", "mcp-proxy==0.13.0"]
# ///
"""Real MCP initialization and tool calls over IPv4/IPv6 with a broken system proxy."""
import ast
import asyncio
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest


ROOT = Path(__file__).resolve().parents[1]


def installed_runtime_code():
    source = (ROOT / "setup-skills-and-mcp.sh").read_text()
    start = source.index("code = (\n")
    end = source.index("\nprint(json.dumps", start)
    statement = ast.parse(source[start:end]).body[0]
    return ast.literal_eval(statement.value)


@contextmanager
def dual_stack_server(tmp_path):
    if not socket.has_dualstack_ipv6():
        pytest.skip("Dual-stack IPv6 is unavailable on this host")
    with socket.create_server(("::", 0), family=socket.AF_INET6,
                              dualstack_ipv6=True) as listener:
        port = listener.getsockname()[1]
        code = '''
import socket,sys,uvicorn
from mcp.server.fastmcp import FastMCP
server = FastMCP("ShucleRiderRAG", instructions="Preserve server instructions",
                 host="0.0.0.0", stateless_http=True)
@server.tool()
def echo(value: str) -> str:
    """Return the supplied value."""
    return value
listener = socket.socket(fileno=int(sys.argv[1]))
uvicorn.Server(uvicorn.Config(server.streamable_http_app(), log_level="warning")).run(sockets=[listener])
'''
        log = (tmp_path / "server.log").open("w+")
        process = subprocess.Popen([sys.executable, "-c", code, str(listener.fileno())],
                                   pass_fds=(listener.fileno(),), stdout=log, stderr=log)
        try:
            with httpx.Client(trust_env=False, timeout=0.3) as client:
                for _ in range(40):
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(log.read())
                    try:
                        if client.get(f"http://127.0.0.1:{port}/mcp").status_code == 406:
                            break
                    except httpx.TransportError:
                        time.sleep(0.05)
                else:
                    pytest.fail("MCP fixture server did not start")
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
def test_runtime_initializes_and_calls_tools_with_broken_proxy(tmp_path, host):
    with dual_stack_server(tmp_path) as port:
        url = f"http://{host}:{port}/mcp"
        env = {name: "http://127.0.0.1:9" for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
        )}
        env.update(NO_PROXY="", no_proxy="")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", installed_runtime_code(), "--transport", "streamablehttp",
                  "--log-level", "WARNING", url],
            env=env,
        )

        async def check():
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "ShucleRiderRAG"
                    assert initialized.instructions == "Preserve server instructions"
                    listing = await session.list_tools()
                    tool = next(tool for tool in listing.tools if tool.name == "echo")
                    assert tool.inputSchema["properties"]["value"]["type"] == "string"
                    result = await session.call_tool("echo", {"value": "connected"})
                    assert not result.isError
                    assert result.content[0].text == "connected"

        asyncio.run(asyncio.wait_for(check(), timeout=15))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
