"""생성 프로젝트의 작업 공간. 모델이 쓰는 파일 도구의 안전장치를 한 곳에 모았다.

- 모든 경로는 작업 공간 폴더 안의 상대 경로만 허용 (절대 경로, .., 숨김 파일/폴더 금지)
- allowed_files가 있으면 그 파일만 쓸 수 있다 (확정한 작업 계획의 범위). 범위 밖 쓰기는 거부하고 기록한다.
- 명령 실행은 사람이 정한 테스트 명령 하나뿐이다 (모델이 임의 명령을 실행할 수 없다).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILE_BYTES = 200_000
MAX_READ_CHARS = 20_000
MAX_LIST = 500
OUTPUT_TAIL = 4000


class ScopeError(Exception):
    pass


def normalize_path(rel: Any) -> str:
    """안전한 상대 경로로 정규화한다. 안전하지 않으면 ValueError."""
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("경로가 비어 있습니다.")
    rel = rel.strip().replace("\\", "/")
    if rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel):
        raise ValueError(f"절대 경로는 쓸 수 없습니다: {rel}")
    p = PurePosixPath(rel)
    if any(part == ".." for part in p.parts):
        raise ValueError(f"상위 폴더(..)는 쓸 수 없습니다: {rel}")
    if any(part.startswith(".") for part in p.parts):
        raise ValueError(f"숨김 파일/폴더(.env, .git 등)는 쓸 수 없습니다: {rel}")
    if not p.parts:
        raise ValueError("경로가 비어 있습니다.")
    return p.as_posix()


class Workspace:
    def __init__(
        self,
        root: str | Path,
        allowed_files: list[str] | None = None,
        test_command: list[str] | None = None,
        test_timeout: int = 180,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed = {normalize_path(p) for p in allowed_files} if allowed_files is not None else None
        self.test_command = test_command
        self.test_timeout = test_timeout
        self.log: list[dict[str, Any]] = []
        self.scope_violations: list[str] = []

    def _full(self, norm: str) -> Path:
        full = (self.root / norm).resolve()
        if not full.is_relative_to(self.root):  # 심볼릭 링크 등으로 밖으로 나가는 경우
            raise ValueError(f"작업 공간 밖입니다: {norm}")
        return full

    def write_file(self, path: str, content: str) -> str:
        norm = normalize_path(path)
        if self.allowed is not None and norm not in self.allowed:
            self.scope_violations.append(norm)
            self.log.append({"action": "write", "path": norm, "ok": False, "reason": "범위 밖"})
            raise ScopeError(
                f"'{norm}'는 확정한 작업 계획에 없는 파일이라 쓰지 않았습니다. "
                "꼭 필요하면 쓰지 말고 최종 응답의 unresolved에 사유를 적으세요."
            )
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ValueError(f"파일이 너무 큽니다 ({size} bytes > {MAX_FILE_BYTES}). 나누어 쓰세요.")
        full = self._full(norm)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        self.log.append({"action": "write", "path": norm, "ok": True, "bytes": size})
        return f"{norm} 저장 ({size} bytes)"

    def read_file(self, path: str) -> str:
        norm = normalize_path(path)
        full = self._full(norm)
        if not full.is_file():
            raise ValueError(f"없는 파일입니다: {norm}")
        text = full.read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= MAX_READ_CHARS else text[:MAX_READ_CHARS] + "\n...(이후 생략)"

    def list_files(self) -> list[str]:
        out = []
        for f in sorted(self.root.rglob("*")):
            rel = f.relative_to(self.root)
            if f.is_file() and not any(part.startswith(".") for part in rel.parts):
                out.append(rel.as_posix())
        return out[:MAX_LIST]

    def files_containing(self, *needles: str) -> list[str]:
        """needles 중 하나라도 들어 있는 파일 목록."""
        hits = []
        for rel in self.list_files():
            try:
                text = (self.root / rel).read_text(encoding="utf-8", errors="ignore")
                if any(n in text for n in needles):
                    hits.append(rel)
            except OSError:
                pass
        return hits

    def run_tests(self) -> dict[str, Any]:
        """사람이 정한 테스트 명령을 작업 공간에서 실행한다 (셸 없이, 시간 제한 있음)."""
        if not self.test_command:
            raise ValueError("테스트 명령이 설정되지 않아 실행할 수 없습니다.")
        try:
            r = subprocess.run(
                self.test_command, cwd=self.root, capture_output=True, text=True,
                timeout=self.test_timeout, errors="replace",
            )
            result = {"exit_code": r.returncode, "timed_out": False, "output": (r.stdout + r.stderr)[-OUTPUT_TAIL:]}
        except subprocess.TimeoutExpired as e:
            out = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
            result = {"exit_code": None, "timed_out": True, "output": out[-OUTPUT_TAIL:]}
        except FileNotFoundError as e:
            result = {"exit_code": None, "timed_out": False, "output": f"명령을 찾을 수 없습니다: {e}"}
        self.log.append({"action": "run_tests", "ok": result["exit_code"] == 0})
        return result
