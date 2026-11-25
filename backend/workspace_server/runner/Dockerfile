# 생성된 코드의 설치·빌드·테스트 명령을 실행하는 이미지 (workspace_server/sandbox.py)
# 서버에서 한 번 빌드: docker build -t orbit-runner:1 backend/workspace_server/runner
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Debian은 시스템 Python에 pip 설치를 막는다(PEP 668). 설치 위치가 작업 공간 안(PYTHONUSERBASE)이라 풀어 둔다
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    npm_config_cache=/tmp/.npm
