"""산출물 버전. 본문은 data/ 아래 파일로 두고 DB에는 위치와 해시만 둔다 (06 3.3).

기획 묶음(planning): planning.json(요구사항 JSON + 근거 + 질문), brief.md(기획서), acceptance_criteria.json(완료 조건).
세 파일을 합쳐 해시를 낸다. G1 승인은 이 해시에 묶인다.
"""

import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

import config
from db import fetch_all, fetch_one, insert, update


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def hash_folder(folder: Path, names: list[str]) -> str:
    """파일 이름과 내용을 이름순으로 이어 SHA-256. 한 글자만 바뀌어도 값이 달라진다."""
    h = hashlib.sha256()
    for name in sorted(names):
        h.update(name.encode() + b"\0" + (folder / name).read_bytes() + b"\0")
    return h.hexdigest()


def get_version(conn, version_id: int | None) -> dict | None:
    if version_id is None:
        return None
    return fetch_one(conn, "select * from artifact_versions where id = ?", (version_id,))


def latest(conn, project_id: int, kind: str, status: str | None = None) -> dict | None:
    """가장 최근 버전. status를 주면 그 상태인 것 중 최근 버전."""
    if status:
        return fetch_one(
            conn,
            "select * from artifact_versions where project_id = ? and kind = ? and status = ? order by version desc limit 1",
            (project_id, kind, status),
        )
    return fetch_one(
        conn,
        "select * from artifact_versions where project_id = ? and kind = ? order by version desc limit 1",
        (project_id, kind),
    )


def add_version(conn, project_id: int, kind: str, files: dict[str, str], agent: str) -> dict:
    """파일을 쓰고 초안 버전을 만든다. 이전 초안은 무효로 바꾼다 (검토할 초안은 하나만 둔다)."""
    prev = latest(conn, project_id, kind)
    number = prev["version"] + 1 if prev else 1
    rel = Path(f"p{project_id}") / kind / f"v{number}"
    folder = config.DATA_DIR / rel
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")

    if prev is not None and prev["status"] == "draft":
        update(conn, "artifact_versions", prev["id"], status="void")
    version_id = insert(
        conn, "artifact_versions",
        project_id=project_id, kind=kind, version=number,
        content_hash=hash_folder(folder, list(files)), content_path=rel.as_posix(),
        prev_version_id=prev["id"] if prev else None, created_by_agent=agent, status="draft",
    )
    return get_version(conn, version_id)


def read_text(version: dict, name: str) -> str:
    return (config.DATA_DIR / version["content_path"] / name).read_text(encoding="utf-8")


def read_json(version: dict, name: str):
    return json.loads(read_text(version, name))


def confirm(conn, version: dict, expected_hash: str) -> None:
    """확정. 같은 종류의 이전 확정본은 무효로 바꾼다."""
    if version["content_hash"] != expected_hash:
        raise HTTPException(status_code=409, detail="그사이에 내용이 바뀌었습니다. 새 버전을 다시 불러오세요.")
    olds = fetch_all(
        conn,
        "select id from artifact_versions where project_id = ? and kind = ? and status = 'confirmed'",
        (version["project_id"], version["kind"]),
    )
    for old in olds:
        update(conn, "artifact_versions", old["id"], status="void")
    update(conn, "artifact_versions", version["id"], status="confirmed")


def planning_bundle(version: dict) -> dict:
    """기획 묶음을 한 번에 읽는다: {"planning", "evidence", "questions", "criteria", "brief"}"""
    data = read_json(version, "planning.json")
    data["criteria"] = read_json(version, "acceptance_criteria.json")["criteria"]
    data["brief"] = read_text(version, "brief.md")
    return data


def write_planning(conn, project_id: int, out: dict) -> dict:
    """A01 결과를 기획 묶음 초안으로 저장한다."""
    body = {"planning": out["planning"], "evidence": out["evidence"], "questions": out["questions"]}
    files = {
        "planning.json": dumps(body),
        "brief.md": out["brief_markdown"],
        "acceptance_criteria.json": dumps({"criteria": out["criteria"]}),
    }
    return add_version(conn, project_id, "planning", files, "A01")
