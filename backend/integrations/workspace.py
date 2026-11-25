"""workspace 서버의 도구를 부른다.

모든 도구는 run_id와 agent를 받는다. 서버가 권한을 검사하고 tool_calls에 기록
"""

import config
from integrations.mcp_client import McpError, call_tool_http

class Workspace:
    """한 실행(run)의 한 에이전트가 쓰는 작업 공간"""

    def __init__(self, run_id: int, agent: str):
        self.run_id, self.agent = run_id, agent

    def _call(self, tool: str, timeout: int = 120, **args) -> dict:
        """도구를 부른다. 거부되거나 실패하면 McpError (에이전트는 실패를 예외로 알린다).

        timeout: 응답을 기다릴 최대 초. 명령 실행처럼 오래 걸리는 도구는 늘려서 부른다.
        """
        out = call_tool_http(config.WORKSPACE_URL, tool, {"run_id": self.run_id, "agent": self.agent, **args}, timeout=timeout)
        if not out.get("ok"):
            raise McpError(f"{tool} {'거부' if out.get('denied') else '실패'}: {out.get('reason')}")
        return out

    def list_files(self, path: str = ".") -> dict:
        return self._call("list_files", path=path)

    def read_file(self, path: str) -> dict:
        return self._call("read_file", path=path)

    def write_file(self, path: str, content: str) -> dict:
        return self._call("write_file", path=path, content=content)

    def run(self, command: str) -> dict:
        # 서버는 명령을 COMMAND_TIMEOUT초까지 기다리므로, 그보다 조금 더 기다린다
        return self._call("run_command", timeout=config.COMMAND_TIMEOUT + 30, command=command)

    def diff(self) -> dict:
        return self._call("git_diff")

    def commit(self, message: str) -> str:
        """커밋하고 SHA를 돌려준다. 메시지에 run-<실행ID>와 FEAT-001 형식 기능 ID가 있어야 한다."""
        return self._call("git_commit", message=message)["sha"]

    def push(self, sha: str) -> dict:
        return self._call("git_push", sha=sha)

    def deploy_local(self, sha: str) -> dict:
        """승인한 커밋을 Docker 테스트 환경에 띄운다. {deployed_sha, deployment_id, deployment_url}

        이미지 빌드가 몇 분 걸릴 수 있어 명령 한도보다 조금 더 기다린다.
        """
        return self._call("deploy_local", timeout=config.COMMAND_TIMEOUT + 60, sha=sha)