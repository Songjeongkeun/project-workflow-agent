"""MCP 서버를 잠깐 띄워 도구 하나를 부르고 결과를 돌려준다.

Slack MCP 서버는 Node 프로그램(npm 패키지)이라 npx로 띄우고, 표준 입출력(stdio)으로 대화한다.
제어기와 알림 서비스는 보통 함수(def)라서, 비동기인 MCP 클라이언트를 asyncio.run으로 감싼다.
알림은 백그라운드 작업자에서만 부르므로, 매번 서버를 띄우는 비용(몇 초)은 문제되지 않는다.
"""

import asyncio
import json

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


class McpError(Exception):
    """MCP 도구 호출 실패. 메시지에 토큰이 들어가지 않게 짧게 자른다."""


def _params(package: str, env: dict) -> StdioServerParameters:
    # env에는 그 서버에 필요한 토큰만 넣는다. PATH 같은 기본 값은 MCP SDK가 알아서 더한다.
    # Windows에서 npx가 npx.cmd인 것도 MCP SDK가 찾아 준다.
    return StdioServerParameters(command="npx", args=["-y", package], env=env)


def call_tool(package: str, tool: str, args: dict, env: dict, timeout: int = 90):
    """npm 패키지로 된 MCP 서버를 띄워 도구 하나를 부른다. 결과가 JSON이면 dict로 돌려준다.

    package: 예) "@modelcontextprotocol/server-slack"
    env    : 그 서버에만 넘길 환경 변수 (토큰)
    timeout: 처음에는 npx가 패키지를 내려받느라 오래 걸릴 수 있어 넉넉하게 둔다
    """

    async def run():
        async with stdio_client(_params(package, env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                text = "".join(c.text for c in result.content if getattr(c, "text", None))
                if result.isError:
                    raise McpError(text[:300])
                try:
                    return json.loads(text)
                except ValueError:
                    return text

    try:
        return asyncio.run(asyncio.wait_for(run(), timeout))
    except McpError:
        raise
    except Exception as e:  # 연결 실패, 시간 초과, npx 없음 등
        raise McpError(f"{type(e).__name__}: {str(e)[:300]}") from e


def list_tools(package: str, env: dict, timeout: int = 90) -> list[dict]:
    """서버의 도구 이름, 설명, 인자 모양 (연결 확인 스크립트가 쓴다)."""

    async def run():
        async with stdio_client(_params(package, env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                return [{"name": t.name, "description": t.description, "input": t.inputSchema} for t in tools]

    return asyncio.run(asyncio.wait_for(run(), timeout))


def _result(result):
    """도구 결과를 dict나 문자열로 바꾼다. stdio.HTTP모두 적용"""
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    if result.isError:
        raise McpError(text[:300])
    try:
        return json.loads(text)
    except ValueError:
        return text


def _http(headers: dict | None, timeout: int) -> httpx.AsyncClient:
    """HTTP MCP용 클라이언트. 응답 읽기는 도구의 시간 제한만큼 기다린다 (명령 실행은 몇 분 걸릴 수 있다)."""
    return httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=timeout))


def call_tool_http(url: str, tool: str, args: dict, headers: dict | None = None, timeout: int = 120):
    """HTTP로 켜져 있는 MCP 서버의 도구 하나를 부른다.

    예)
    url: http://127.0.0.1:8100/mcp
    headers : 토큰이 필요하면 ("Authorization": "Bearer ..."). 토큰을 넘겨줌
    """

    async def run():
        # mcp 1.30부터는 헤더와 시간 제한을 httpx 클라이언트에 담아 넘긴다
        async with _http(headers, timeout) as http, streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _result(await session.call_tool(tool, args))

    try:
        return asyncio.run(asyncio.wait_for(run(), timeout))
    except McpError:
        raise
    except Exception as e:
        raise McpError(f"{type(e).__name__}: {str(e)[:300]}") from e


def list_tools_http(url: str, headers: dict | None = None, timeout: int = 60) -> list[dict]:
    """도구 이름, 설명, 인자 모양 (연결 확인 스크립트)"""

    async def run():
        async with _http(headers, timeout) as http, streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                return [{"name": t.name, "description": t.description, "input": t.inputSchema} for t in tools]

    return asyncio.run(asyncio.wait_for(run(), timeout))

