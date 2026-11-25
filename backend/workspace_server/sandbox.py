"""생성된 코드를 격리해서 실행한다 (backend 11번).

npm install, make test는 명령 이름만 정해져 있고, 실제로 무엇을 할지는
에이전트가 쓴 파일(package.json의 postinstall, Makefile의 test:)이 정한다.
그래서 서버에서 바로 돌리지 않고, 명령마다 새 컨테이너를 띄워 그 안에서 실행한 뒤 지운다.

컨테이너 안에서 보이는 것: 이 프로젝트의 작업 공간(/workspace)뿐
보이지 않는 것: .env(토큰), orbit.db, 다른 프로젝트, docker 소켓
"""

import json
import subprocess
import uuid
from pathlib import Path

import config
from workspace_server.guards import Denied

# 패키지를 받아야 하는 명령만 네트워크를 연다. 빌드·테스트는 네트워크 없이 (밖으로 보내기 방지)
NEEDS_NETWORK = (
    ["npm", "install"],
    ["pip", "install", "-r", "requirements.txt"],
)


def docker_args(root: Path, project_id: int, args: list[str], name: str) -> list[str]:
    """`docker run` 인자 목록을 만든다. 테스트에서 이 함수만 따로 확인한다."""
    network = "bridge" if args in NEEDS_NETWORK else "none"
    return [
        "docker", "run", "--rm", "--name", name,
        "--label", f"orbit.project={project_id}",
        "--network", network,
        # root가 아닌 사용자로 실행. 만든 파일의 소유자도 서버 사용자와 같아서 git이 그대로 다룰 수 있다
        "--user", config.SANDBOX_USER,
        # 권한 상승 막기: 리눅스 권한을 모두 버리고, setuid 프로그램으로 권한을 얻지 못하게 한다
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        # 자원 한도: 무한 루프나 fork 폭탄이 서버 전체를 멈추지 못하게
        "--memory", config.SANDBOX_MEMORY, "--cpus", config.SANDBOX_CPUS, "--pids-limit", "512",
        # 환경 변수는 아래 것만 준다 (서버의 토큰은 넘어가지 않는다)
        "--env", "HOME=/tmp",
        # pip 설치 위치를 작업 공간 안으로: 컨테이너가 지워져도 다음 명령(make test)에서 쓸 수 있게
        "--env", "PIP_USER=1",
        "--env", "PYTHONUSERBASE=/workspace/.orbit/python",
        "--env", "PATH=/workspace/.orbit/python/bin:/usr/local/bin:/usr/bin:/bin",
        "--tmpfs", "/tmp:rw,size=512m",
        # 이 프로젝트 폴더만 붙인다
        "-v", f"{root}:/workspace", "-w", "/workspace",
        config.SANDBOX_IMAGE, *args,
    ]


def run(root: Path, project_id: int, args: list[str]) -> dict:
    """컨테이너에서 명령을 실행하고 run_command와 같은 모양의 결과를 돌려준다."""
    name = f"orbit-cmd-p{project_id}-{uuid.uuid4().hex[:8]}"
    try:
        r = subprocess.run(
            docker_args(root, project_id, args, name),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=config.COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # subprocess만 끊으면 컨테이너는 계속 돈다. 이름으로 찾아서 멈춘다 (--rm이라 멈추면 지워짐)
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        return {"exit_code": None, "timed_out": True, "stdout": "",
                "stderr": f"{config.COMMAND_TIMEOUT}초를 넘겨 멈췄습니다."}

    return {"exit_code": r.returncode, "timed_out": False,
            "stdout": r.stdout[-config.OUTPUT_LIMIT:], "stderr": r.stderr[-config.OUTPUT_LIMIT:]}


# ---- 배포 전 docker-compose.yml 검사 ----

# 이 중 하나라도 있으면 컨테이너가 서버 자체를 다룰 수 있다
BAD_SERVICE_KEYS = ("privileged", "network_mode", "pid", "ipc", "userns_mode",
                    "cap_add", "devices", "security_opt", "cgroup_parent")


def compose_problems(cfg: dict, folder: Path, port: int) -> list[str]:
    """`docker compose config`로 해석한 설정에서 허용하지 않는 항목을 찾는다. 테스트는 이 함수만 본다."""
    folder = folder.resolve()

    def inside(p) -> bool:
        return Path(p).resolve().is_relative_to(folder)

    problems = []
    for name, svc in (cfg.get("services") or {}).items():
        problems += [f"{name}.{key}" for key in BAD_SERVICE_KEYS if svc.get(key)]

        # 서버의 폴더를 붙이는 것은 저장소 폴더 안만 허용 (예: - /:/host, - ~/orbit/.env:/x 는 거부)
        for v in svc.get("volumes") or []:
            if v.get("type") == "bind" and not inside(v.get("source", "/")):
                problems.append(f"{name}.volumes {v.get('source')}")

        # 이미지 빌드 재료도 저장소 안만. 밖을 가리키면 .env가 이미지에 들어갈 수 있다
        build = svc.get("build") or {}
        if build and not inside(build.get("context", "/")):
            problems.append(f"{name}.build.context {build.get('context')}")

        # 바깥 포트는 이 프로젝트에 정해 준 포트(9000+pid)만
        for p in svc.get("ports") or []:
            if str(p.get("published") or "") not in ("", str(port)):
                problems.append(f"{name}.ports {p.get('published')}")

    # 이름 있는 볼륨으로 서버 폴더를 붙이는 우회 (driver_opts: {o: bind, device: /})
    for vname, vol in (cfg.get("volumes") or {}).items():
        if (vol or {}).get("driver_opts"):
            problems.append(f"volumes.{vname}.driver_opts")

    return problems


def check_compose(folder: Path, project: str, env: dict, port: int) -> None:
    """docker compose가 해석한 최종 설정으로 검사한다 (YAML을 직접 읽으면 extends, ${변수} 등을 놓친다)."""
    r = subprocess.run(
        ["docker", "compose", "-p", project, "config", "--format", "json"],
        cwd=folder, env={"PATH": "/usr/local/bin:/usr/bin:/bin", **env},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if r.returncode != 0:
        raise Denied(f"docker-compose.yml을 읽을 수 없습니다: {(r.stderr or r.stdout)[-500:]}")

    problems = compose_problems(json.loads(r.stdout), folder, port)
    if problems:
        raise Denied("docker-compose.yml에 허용하지 않는 설정이 있습니다: " + ", ".join(problems))
