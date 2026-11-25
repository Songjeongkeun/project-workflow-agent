"""Orbit API 서버.

실행 (backend 폴더에서, 가상환경을 켠 뒤):
    uvicorn main:app --reload

API 문서: http://127.0.0.1:8000/docs
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from db import init_db
from routers import approvals, auth, changes, planning, projects, runs
from services import controller, notify, jobs

logging.basicConfig(level=logging.INFO)


async def agent_sweeper():
    """몇 초마다 DB를 보고 진행해야 할 실행 
    ex) A01을 작업자에게 맡긴다.
    """
    while True:
        try:
            await asyncio.to_thread(jobs.sweep)
        except Exception:
            logging.exception("에이전트 작업자 오류")
        await asyncio.sleep(config.AGENT_SWEEP_SECONDS)


async def notify_worker():
    """몇 초마다 쌓인 Slack 알림을 보낸다 (04 7.5: 실패해도 실행은 계속, 간격을 두고 다시 시도).

    보내기는 동기 함수라 별도 스레드(to_thread)에서 돌려 서버 응답을 막지 않는다.
    오류가 나도 작업자는 멈추지 않고 다음 차례에 다시 시도한다.
    """
    while True:
        try:
            await asyncio.to_thread(notify.send_pending)
        except Exception:
            logging.exception("알림 작업자 오류")
        await asyncio.sleep(config.NOTIFY_INTERVAL)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()  # 테이블 준비
    workers = [asyncio.create_task(notify_worker()), asyncio.create_task(agent_sweeper())]
    yield
    for w in workers:
        w.cancel()
    jobs.shutdown()


app = FastAPI(title="Orbit API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(planning.router)
app.include_router(runs.router)
app.include_router(approvals.router)
app.include_router(changes.router)
