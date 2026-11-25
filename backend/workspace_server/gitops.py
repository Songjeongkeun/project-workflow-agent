"""작업 공간에서 git 명령을 실행하는 도우미.

모든 git 실행을 git() 함수 하나로 모음
- 셸을 거치지 않고 인자 리스트로 실행 (명령 끼워 넣기를 방지해준다)
- 실패하면 예외를 던진다 (server.py의 guarded가 잡아서 기록)
"""

import subprocess # 파이썬에서 다른 프로그램을 실행하는 표준 라이브러리
from pathlib import Path


# 아무 파일도 없는 "빈 트리"의 git 해시. 모든 git 저장소에서 같은 고정 값
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

def git(root: Path, *args: str, timeout: int = 60) -> str:
    """root 폴더에서 `git <args...>`를 실행하고 표준 출력을 돌려준다.

    *args: 인자를 몇 개든 받아 튜플로 저장. 예) git(root, "commit", "-m", "메시지")
    """

    r = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        # Windows 기본 인코딩(cp949) 대신 utf-8로 읽는다. 한글 커밋 메시지·파일 이름이 깨지지 않게 한다
        encoding="utf-8",
        errors="replace",
        timeout=timeout
    )

    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} 실패")
    return r.stdout.strip()

def ensure_repo(root: Path) -> None:
    """작업 공간이 아직 없으면 폴더를 만들고 git 저장소로 시작한다.

    프로젝트를 만들 때 따로 준비하지 않아도, 처음 도구를 부를 때 자동으로 만들어진다.
    """

    if not (root / ".git").exists():
        # parents=True: 중간 폴더(data/workspace)도 없으면 같이 만들어준다.
        root.mkdir(parents=True, exist_ok=True)

        git(root, "init", "-b", "candidate")

def changed_paths(root: Path, sha: str, base: str | None = None) -> list[str]:
    """base 커밋부터 sha 커밋까지 바뀐 파일 경로 목록.
    
    제어기가 G3를 판단할 때 쓴다: 이 목록 중 기술 명세의 planned_paths에 없는 파일이 있으면 계획 밖 변경.
    base를 안 주면 빈 트리와 비교하므로 "처음부터 sha까지 모든 파일"이 된다.
    """

    out = git(root, "diff", "--name-only", base or EMPTY_TREE, sha)

    return [line for line in out.splitlines() if line]