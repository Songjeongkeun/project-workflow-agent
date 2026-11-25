"""GitHub MCP로 게시한 커밋 확인"""

import config
from integrations.mcp_client import McpError, call_tool_http

def headers() -> dict:
    if not config.GITHUB_TOKEN:
        raise McpError(".env에 GITHUB_TOKEN을 넣어주세요.")
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "X-MCP-Readonly": "true",
        "X-MCP-Toolsets": "repos"
    }

def _call(tool: str, args: dict):
    return call_tool_http(config.GITHUB_MCP_URL, tool, args, headers=headers())

def commit_exists(repository: str, sha: str) -> bool:
    """repository = "owner/name". 그 SHA의 커밋이 원격 저장소에 있으면 True."""
    owner, repo = repository.split("/")

    try:
        out = _call("get_commit", {"owner": owner, "repo": repo, "sha": sha})
    except McpError as e:
        if "404" in str(e) or "No commit found" in str(e):
            return False
        raise
    return isinstance(out, dict) and out.get("sha", "").startswith(sha)

