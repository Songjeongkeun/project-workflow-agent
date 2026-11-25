# 타입 힌트 평가 지연. 타입 힌트를 함수 정의 시 즉시 평가하지 않고 나중에 처리
from __future__ import annotations 

from importlib.metadata import metadata
import json
import os
import queue
import subprocess # 외부 프로세스 실행
# 멀티스레딩. 서버 응답을 별도 스레드에서 읽어 프로그램이 멈추지 않도록 처리
import threading
import time
from pathlib import Path
from typing import Any # 타입 힌트. 데이터 타입 표현

# 현재 실행에서 승인한 Figma MCP 도구
_APPROVED_FIGMA_TOOLS = set()


def _read_lines(stream, out: queue.Queue, label: str):
    """
    App Server 프로세스의 출력 스트림을 읽어 이벤트 큐에 전달
    """
    try:
        for line in stream:
            line = line.strip()
            if line:
                out.put((label, line))
    finally:
        out.put((label, None))


def _mcp_approval(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Figma MCP 도구의 사용자 승인 요청 처리
    (Codex App Server가 전달한 승인 요청 내용을 표시하고 
    사용자에게 작업 실행 승인 여부를 확인)
    """
    params = msg.get("params") or {}
    request = params.get("request") or params.get("elicitation") or params
    metadata = request.get("meta") or request.get("_meta") or params.get("meta") or {}
    
    tool_name = metadata.get("tool_name")
    server_name = params.get("serverName", "")

    is_figma_write = (
        tool_name == "figma.use_figma"
        and server_name in ("codex_apps", "figma")
    )

    schema = request.get("requestedSchema") or {}
    required = schema.get("required", []) if isinstance(schema, dict) else []

    # 같은 실행에서 이미 승인한 Figma 도구는 자동 승인
    if is_figma_write and tool_name in _APPROVED_FIGMA_TOOLS and not required:
        print("[자동 승인] figma.use_figma")
        return {"action": "accept", "content": {}}
    
    print("\n[MCP 승인 요청]")
    print("서버:", params.get("serverName", "(알 수 없음)"))
    if metadata:
        print("메타데이터:", json.dumps(metadata, ensure_ascii=False)[:1200])
    print("요청:", str(request.get("message", ""))[:2000])

    if schema:
        print("입력 형식:", json.dumps(schema, ensure_ascii=False)[:2000])
    answer = input("작업을 승인할까요? (Y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        return {"action": "decline", "content": None}
    
    if required:
        raw = input("필수 입력을 JSON 객체로 입력하세요 (취소: 빈 입력): ").strip()
        if not raw:
            return {"action": "decline", "content": None}
        try:
            content = json.loads(raw)
            if not isinstance(content, dict) or any(k not in content for k in required):
                raise ValueError("필수 속성이 누락됨")
        except (ValueError, json.JSONDecodeError) as exc:
            print("잘못된 JSON으로 승인 취소:", exc)
            return {"action": "decline", "content": None}
    else:
        content = {}

    if is_figma_write and not required:
        _APPROVED_FIGMA_TOOLS.add(tool_name)

    return {"action": "accept", "content": content}


def _server_request(msg: dict[str, Any]) -> dict[str, Any]:
    """
    Codex App Server가 전달한 요청을 종류별로 분류하고 처리
    - MCP 승인 요청 -> _mcp_approval()로 전달
    - 명령 실행이나 파일 변경 승인 요청도 사용자 확인을 거쳐 처리
    - 지원하지 않는 요청 -> RuntimeError 처리
    """
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if method == "mcpServer/elicitation/request":
        return _mcp_approval(msg)
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        print(f"\n[Codex 승인 요청: {method}]")
        print(json.dumps(params, ensure_ascii=False, indent=2)[:3500])
        choice = input("승인하시겠습니까? (Y/N): ").strip().lower()
        return {"decision": "accept" if choice in ("y", "yes") else "decline"}
    # No automatic acceptance of unknown request types.
    print("[지원하지 않는 서버 요청]", method)
    print(json.dumps(params, ensure_ascii=False)[:1500])
    raise RuntimeError(f"지원되지 않는 승인/상호작용 요청: {method}")


def _parse_json_output(raw: str) -> dict[str, Any]:
    """
    Codex의 최종 텍스트 응답을 JSON 객체로 반환
    """
    raw = raw.strip()
    if raw.startswith("```"):
        # 응답이 Markdown 코드 블록으로 감싸져 있으면 제거
        parts = raw.split("\n", 1)
        if len(parts) == 2:
            raw = parts[1]
        raw = raw.rsplit("```", 1)[0].strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Codex 응답은 JSON 객체여야 합니다.")
    return value


def run_codex_with_approvals(
    prompt: str,
    output_schema: dict[str, Any],
    cwd: str | Path,
    timeout: int = 900,
) -> dict[str, Any]:
    """
    Codex App Server를 실행하고 Figma MCP 작업 결과를 반환
    """
    # 새로운 실행마다 승인 기록 초기화
    _APPROVED_FIGMA_TOOLS.clear()

    cwd = str(Path(cwd).resolve())
    command = ["codex.cmd" if os.name == "nt" else "codex", "app-server", "--stdio"]
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        text=True,
        bufsize=1,
    )
    events: queue.Queue = queue.Queue()
    threading.Thread(target=_read_lines, args=(proc.stdout, events, "stdout"), daemon=True).start()
    threading.Thread(target=_read_lines, args=(proc.stderr, events, "stderr"), daemon=True).start()
    next_id = 0
    pending: dict[int, dict[str, Any]] = {}
    messages: list[str] = []
    deadline = time.monotonic() + timeout

    def send(data: dict[str, Any]):
        """
        Python에서 Codex App Server로 JSON-RPC 메세지 전송
        전달 받은 데이터를 JSON 문자열로 직렬화하여, 
        App Server 프로세스의 표준 입력(stdin)에 기록
        """
        if proc.poll() is not None:
            raise RuntimeError(f"Codex App Server 종료됨: {proc.returncode}")
        proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def poll_event() -> dict[str, Any]:
        """
        Codex App Server에서 발생한 이벤트를 수신하고 처리
        이벤트 큐를 확인하여 JSON-RPC 응답, 승인 요청, MCP 도구 실행 결과 및 작업 완료 이벤트를 구분
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Codex App Server 제한 시간 {timeout}초 초과")
            try:
                source, line = events.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if proc.poll() is not None:
                    raise RuntimeError(f"Codex App Server 비정상 종료: {proc.returncode}")
                continue
            if line is None:
                if source == "stdout":
                    raise RuntimeError("Codex App Server stdout 연결 종료")
                continue
            if source == "stderr":
                # Diagnostics go to stderr; don't confuse them with JSON-RPC.
                print("[Codex 진단]", line[:600])
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print("[Codex 비JSON 출력]", line[:300])
                continue
            if not isinstance(event, dict):
                continue
            # Server -> client request (including MCP tool approval).
            if "method" in event and "id" in event:
                try:
                    result = _server_request(event)
                except Exception as exc:
                    send({"id": event["id"], "error": {"code": -32603, "message": str(exc)}})
                    raise
                send({"id": event["id"], "result": result})
                continue
            if "id" in event and ("result" in event or "error" in event):
                pending[event["id"]] = event
                return {"_response_received": event["id"]}
            method = event.get("method")
            params = event.get("params") or {}
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("text"):
                    messages.append(item["text"])
                if item.get("type") == "mcpToolCall":
                    print("[Figma 도구]", item.get("server", ""), item.get("tool", ""), item.get("status", ""))
            elif method == "turn/completed":
                return {"_turn_completed": params}
            elif method == "error":
                print("[Codex 오류]", json.dumps(params, ensure_ascii=False)[:1600])

    def call(method: str, params: dict[str, Any]) -> Any:
        """
        Codex App Server에 JSON-RPC 요청을 보내고 응답 반환
        요청마다 고유한 ID를 생성하고, 해당 ID에 대응하는 서버 응답이 도착할 때까지 대기
        """
        nonlocal next_id
        next_id += 1
        rid = next_id
        send({"id": rid, "method": method, "params": params})
        while rid not in pending:
            event = poll_event()
            if "_turn_completed" in event:
                raise RuntimeError(f"{method} 응답 전 turn/completed 수신")
        reply = pending.pop(rid)
        if "error" in reply:
            raise RuntimeError(f"{method}: {json.dumps(reply['error'], ensure_ascii=False)}")
        return reply.get("result")

    try:
        print("[Codex App Server 시작]")
        call("initialize", {
            "clientInfo": {"name": "figma_agent", "title": "A02 Figma Agent", "version": "1.0.0"},
        })
        send({"method": "initialized", "params": {}})
        thread_resp = call("thread/start", {
            "cwd": cwd,
            "approvalPolicy": "on-request",
            "sandbox": "workspace-write",
        })
        thread_id = (thread_resp.get("thread") or {}).get("id")
        if not thread_id:
            raise RuntimeError(f"thread/start의 thread.id 누락: {thread_resp}")
        call("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": "on-request",
            "outputSchema": output_schema,
        })
        print("[Figma 작업 진행 중 — 필요한 승인만 요청합니다]")
        while True:
            event = poll_event()
            if "_turn_completed" in event:
                state = (event["_turn_completed"].get("turn") or {})
                if state.get("status") != "completed":
                    raise RuntimeError(f"Codex 작업 실패: {json.dumps(state.get('error') or state, ensure_ascii=False)[:1600]}")
                break
        if not messages:
            raise RuntimeError("Codex의 최종 agentMessage가 없습니다.")
        try:
            return _parse_json_output(messages[-1])
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"최종 응답이 JSON이 아닙니다: {messages[-1][:1000]}") from exc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
