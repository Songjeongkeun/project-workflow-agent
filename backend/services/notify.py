"""단계 알림(Slack)과 문서 게시(Notion) (02 4.6·4.7, 04 7.4·7.5).

두 부분으로 나뉜다.
1. 쌓기: 이벤트가 생긴 그 DB 트랜잭션 안에서 notifications에 "보낼 것"(pending)을 쌓는다.
   - 이벤트와 같은 트랜잭션이라, 승인은 저장됐는데 알림만 빠지거나 그 반대가 되는 일이 없다.
   - 바로 보내지 않으므로 Slack이 느리거나 죽어 있어도 실행은 멈추지 않는다 (04 7.5).
2. 보내기: 백그라운드 작업자(main.py)가 몇 초마다 send_pending()을 불러 실제로 보낸다.
   실패하면 기록하고 간격을 두고 다시 보낸다.

Notion도 같은 표(notifications, channel='notion')에 쌓는다. 받는 곳(recipient) 칸에는 문서 종류
(planning, tech_spec, checks, deploy)를 적고, payload에 게시할 마크다운 전체를 담는다.

규칙 (02 4.7)
- 메시지는 이 파일의 함수가 정해진 형식으로 만든다. LLM이 직접 보내지 않는다.
- 요구사항 원문, 코드, 자격 증명은 넣지 않는다. 요약과 링크만 보낸다.
- 승인의 기준은 서버의 승인 기록이다. Slack 메시지는 알림 용도로만 쓴다.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import config
from db import fetch_all, fetch_one, get_connection, insert, now, update
from services import artifacts, members, notion_docs

log = logging.getLogger(__name__)

GATE_NAME = {"G1": "기획서와 완료 조건", "G2": "Figma 화면", "G3": "계획 범위를 넘는 코드 변경", "G4": "배포 대상과 버전"}


# ── 쌓기 ───────────────────────────────────────────────────


def slack_ids(conn, project_id: int, roles: tuple[str, ...]) -> list[str]:
    """이 역할 중 하나라도 가진 멤버의 Slack 사용자 ID. 한 사람이 여러 역할이어도 한 번만."""
    ids = []
    for m in members.list_members(conn, project_id):
        if m["slack_user_id"] and set(m["roles"]) & set(roles) and m["slack_user_id"] not in ids:
            ids.append(m["slack_user_id"])
    return ids


def enqueue(conn, project_id: int, event: str, key: str, text: str, *,
            run_id: int | None = None, roles: tuple[str, ...] = (), to_channel: bool = False) -> int:
    """알림을 쌓는다. 쌓은 수를 돌려준다.

    key       : 이 이벤트를 구분하는 값. 받는 사람마다 뒤에 ID를 붙여 dedupe_key가 된다.
                같은 dedupe_key는 한 번만 쌓인다 (서버를 다시 켜서 같은 단계를 또 지나가도 두 번 안 간다)
    roles     : 이 역할을 가진 멤버 모두에게 개인 메시지 (Slack ID가 있는 멤버만)
    to_channel: 프로젝트 알림 채널(SLACK_CHANNEL_ID)에도 보낼지
    """
    recipients = slack_ids(conn, project_id, roles)
    if to_channel and config.SLACK_CHANNEL_ID:
        recipients.append(config.SLACK_CHANNEL_ID)

    count = 0
    for recipient in recipients:
        try:
            insert(
                conn, "notifications",
                project_id=project_id, run_id=run_id, channel="slack", event=event, recipient=recipient,
                dedupe_key=f"{key}-{recipient}", status="pending", payload={"text": text}, next_try_at=now(),
            )
            count += 1
        except sqlite3.IntegrityError:
            pass  # 이미 쌓은 알림
    return count


def publish(conn, project_id: int, event: str, key: str, kind: str, markdown: str, *,
            run_id: int | None = None, version_id: int | None = None) -> int:
    """Notion 문서 게시를 쌓는다. NOTION_TOKEN과 NOTION_PARENT_PAGE가 없으면 쌓지 않는다.

    kind      : 문서 종류. 프로젝트마다 종류별로 페이지 하나를 두고 내용을 바꿔 간다
    version_id: 기획서처럼 산출물 버전이 있으면, 게시한 페이지 주소를 그 버전의 external_url에 적는다
    """
    if not config.notion_on():
        return 0
    payload = {"kind": kind, "markdown": markdown, "version_id": version_id}
    try:
        insert(
            conn, "notifications",
            project_id=project_id, run_id=run_id, channel="notion", event=event, recipient=kind,
            dedupe_key=f"{key}-notion", status="pending", payload=payload, next_try_at=now(),
        )
        return 1
    except sqlite3.IntegrityError:
        return 0  # 이미 쌓은 게시


def link(path: str) -> str:
    """화면 주소. 예) link("/approvals/3") → http://localhost:5173/approvals/3"""
    return config.APP_URL.rstrip("/") + path


def run_page(project: dict) -> str:
    """프로젝트의 실행 상태 화면 주소."""
    return link("/projects/" + str(project["id"]))


def _project(conn, project_id: int) -> dict:
    return fetch_one(conn, "select * from projects where id = ?", (project_id,))


def _criteria(conn, project_id: int) -> dict[str, dict]:
    """확정된 기획서의 완료 조건 {id: 조건}. 검사 결과에 조건 문장을 붙일 때 쓴다."""
    version = artifacts.latest(conn, project_id, "planning", "confirmed")
    if version is None:
        return {}
    return {c["id"]: c for c in artifacts.planning_bundle(version)["criteria"]}


def _short(text: str | None, limit: int = 200) -> str:
    """요약이나 실패 이유를 짧게. 긴 로그가 통째로 가지 않게 한다."""
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── 이벤트별 메시지 (02 4.7 표) ────────────────────────────


def a01_done(conn, project_id: int, version: dict, bundle: dict, open_questions: int, g1_request: dict) -> None:
    """A01 기획 초안 완료 → 기획 담당."""
    p = _project(conn, project_id)
    features = bundle["planning"].get("release_scope", {}).get("features", [])
    text = (
        f"[{p['name']}] 기획서 v{version['version']} 초안이 나왔습니다.\n"
        f"기능 {len(features)}개, 완료 조건 {len(bundle['criteria'])}개, 남은 질문 {open_questions}개\n"
        f"G1 확인: {link('/projects/' + str(project_id) + '/planning')}"
    )
    enqueue(conn, project_id, "a01_done", f"p{project_id}-g1-{g1_request['id']}", text, roles=("planner",))
    page = link("/projects/" + str(project_id) + "/planning")
    publish(conn, project_id, "a01_done", f"p{project_id}-planning-v{version['version']}-draft", "planning",
            notion_docs.planning(version, bundle, page), version_id=version["id"])


def planning_confirmed(conn, project_id: int, version: dict) -> None:
    """G1 확정 → Notion 기획서 페이지를 "확정 vN"으로 (변경 요청을 다시 확정할 때도 여기로 온다)."""
    version = artifacts.get_version(conn, version["id"])  # 확정으로 바뀐 상태를 다시 읽는다
    page = link("/projects/" + str(project_id) + "/planning")
    publish(conn, project_id, "g1_confirmed", f"p{project_id}-planning-v{version['version']}-confirmed", "planning",
            notion_docs.planning(version, artifacts.planning_bundle(version), page), version_id=version["id"])


def scope_waiting(conn, run: dict, waiting: dict) -> None:
    """A02가 Figma 작업 범위 확인을 기다림 → 디자인 담당."""
    p = _project(conn, run["project_id"])
    text = (
        f"[{p['name']}] Figma 작업 범위를 확인해 주세요.\n"
        f"페이지: {_short(waiting.get('page'), 80)}, 프레임 {len(waiting.get('frames', []))}개\n"
        f"확인하기: {run_page(p)}"
    )
    enqueue(conn, p["id"], "scope_waiting", f"run{run['id']}-scope", text, run_id=run["id"], roles=("designer",))


def a03_done(conn, run: dict, out: dict) -> None:
    """A03 개발 완료 → 개발 담당 (계획 밖 변경이 있으면 G3 알림이 따로 간다)."""
    p = _project(conn, run["project_id"])
    sha = (out.get("candidate_sha") or "")[:12]
    text = (
        f"[{p['name']}] 개발이 끝났습니다. 후보 버전 {sha}\n"
        f"{_short(out.get('summary'))}\n"
        f"기술 명세와 실행 상태: {run_page(p)}"
    )
    enqueue(conn, p["id"], "a03_done", f"run{run['id']}-a03-{sha}", text, run_id=run["id"], roles=("developer",))
    ts = artifacts.latest(conn, p["id"], "tech_spec")
    if ts is not None:
        publish(conn, p["id"], "a03_done", f"run{run['id']}-tech-{sha}", "tech_spec",
                notion_docs.tech_spec(ts, out.get("tech_spec") or {}, sha, run_page(p)), run_id=run["id"])


def checks_ready(conn, run: dict, sha: str, results: list[dict]) -> None:
    """A04 배포 전 검사가 끝나 G4를 기다림 → Notion 검사 결과 페이지."""
    p = _project(conn, run["project_id"])
    publish(conn, p["id"], "checks", f"run{run['id']}-checks-{sha}", "checks",
            notion_docs.checks(sha, results, _criteria(conn, p["id"]), run_page(p)), run_id=run["id"])


def gate_opened(conn, run: dict, gate: str, req: dict, summary: str) -> None:
    """G2·G3·G4 승인 요청 → 그 승인 지점의 담당 (G4는 개발 담당에게도 검사 결과를 알린다)."""
    p = _project(conn, run["project_id"])
    roles = (members.GATE_ROLE[gate], "developer") if gate == "G4" else (members.GATE_ROLE[gate],)
    text = (
        f"[{p['name']}] {gate} 승인 요청: {GATE_NAME[gate]}\n"
        f"{summary}\n"
        f"승인하기: {link('/approvals/' + str(req['id']))}"
    )
    enqueue(conn, p["id"], f"{gate.lower()}_requested", f"run{run['id']}-{gate}-{req['id']}", text,
            run_id=run["id"], roles=roles)


def rejected(conn, run: dict, req: dict, comment: str | None) -> None:
    """G2~G4 반려 → 다시 할 단계의 담당 (화면이면 디자인, 코드면 개발)."""
    p = _project(conn, run["project_id"])
    role = "designer" if req["gate"] == "G2" else "developer"
    step = "화면 설계(A02)" if req["gate"] == "G2" else "개발(A03)"
    text = (
        f"[{p['name']}] {req['gate']}가 반려되어 {step}부터 다시 합니다.\n"
        f"반려 의견: {_short(comment)}\n"
        f"실행 상태: {run_page(p)}"
    )
    enqueue(conn, p["id"], "rejected", f"run{run['id']}-reject-{req['id']}", text, run_id=run["id"], roles=(role,))


def failed(conn, run: dict, failure: dict) -> None:
    """실패, 반복 한도 초과 → 그 단계의 담당 역할 (멈춘 단계, 이유, 다음에 할 일)."""
    p = _project(conn, run["project_id"])
    n = fetch_one(conn, "select count(*) as n from run_steps where run_id = ?", (run["id"],))["n"]
    text = (
        f"[{p['name']}] 실행이 멈췄습니다 ({failure['step']})\n"
        f"이유: {_short(failure['reason'])}\n"
        f"다음에 할 일: 원인을 확인한 뒤 실행 상태 화면에서 [다시 시도]\n"
        f"{run_page(p)}"
    )
    enqueue(conn, p["id"], "failed", f"run{run['id']}-fail-{n}", text, run_id=run["id"], roles=(failure["role"],))


def deployed(conn, run: dict, results: list[dict]) -> None:
    """A05 배포와 배포 후 검사 완료 → 배포 담당, 프로젝트 채널."""
    p = _project(conn, run["project_id"])
    passed = sum(1 for r in results if r["result"] == "pass")
    text = (
        f"[{p['name']}] 배포가 끝났습니다.\n"
        f"주소: {run['deployment_url']}\n"
        f"커밋: {(run['pushed_sha'] or '')[:12]}, 배포 후 검사 {passed}/{len(results)} 통과\n"
        f"실행 기록: {link('/projects/' + str(p['id']) + '/runs/' + str(run['id']))}"
    )
    enqueue(conn, p["id"], "deployed", f"run{run['id']}-done", text, run_id=run["id"],
            roles=("deployer",), to_channel=True)
    record = link("/projects/" + str(p["id"]) + "/runs/" + str(run["id"]))
    publish(conn, p["id"], "deployed", f"run{run['id']}-deploy", "deploy",
            notion_docs.deploy(run, results, _criteria(conn, p["id"]), record), run_id=run["id"])


def change_registered(conn, cr: dict) -> None:
    """변경 요청 분석 끝 → 다시 승인해야 하는 담당 역할 (바뀐 기능, 영향받는 단계, 재승인 링크)."""
    p = _project(conn, cr["project_id"])
    changed = cr["changed_ids"] or {}
    ids = changed.get("added", []) + changed.get("modified", []) + changed.get("removed", [])
    features = sorted({i for i in ids if i.startswith("FEAT-")})
    steps = (cr["impact"] or {}).get("rerun_steps", [])
    roles = tuple(dict.fromkeys(members.GATE_ROLE[g] for g in (cr["reapproval_gates"] or [])))
    text = (
        f"[{p['name']}] 변경 요청이 등록되었습니다.\n"
        f"바뀐 항목 {len(ids)}개 (기능: {', '.join(features) or '없음'})\n"
        f"다시 실행할 단계: {', '.join(steps) or '없음'}, 다시 승인할 지점: {', '.join(cr['reapproval_gates'] or [])}\n"
        f"변경 내용: {link('/projects/' + str(p['id']) + '/changes/' + str(cr['id']))}"
    )
    enqueue(conn, p["id"], "change_registered", f"cr{cr['id']}", text, roles=roles)


# ── 보내기 ─────────────────────────────────────────────────


def _scrub(message: str) -> str:
    """오류 메시지에 토큰이 섞여 들어와도 DB에 남지 않게 지운다."""
    for token in (config.SLACK_BOT_TOKEN, config.NOTION_TOKEN):
        if token:
            message = message.replace(token, "***")
    return message[:500]


def _publish_notion(row: dict, notion) -> None:
    """Notion 게시 한 건. 처음이면 프로젝트 페이지와 문서 페이지를 만들고, 그다음부터는 내용만 바꾼다.

    만든 페이지 ID는 바로 notion_pages에 저장한다. 내용 바꾸기가 실패해 다시 시도해도 페이지가 두 개 생기지 않는다.
    """
    kind, pid = row["payload"]["kind"], row["project_id"]

    def page_of(kind: str) -> dict | None:
        with get_connection() as conn:
            return fetch_one(conn, "select * from notion_pages where project_id = ? and kind = ?", (pid, kind))

    def make(kind: str, parent_id: str, title: str, intro: str | None = None) -> dict:
        made = notion.create_page(parent_id, title, intro, notion_docs.ICONS.get(kind))
        with get_connection() as conn:
            insert(conn, "notion_pages", project_id=pid, kind=kind, page_id=made["id"], url=made.get("url"))
        return page_of(kind)

    project = page_of("project")
    if project is None:
        with get_connection() as conn:
            name = _project(conn, pid)["name"]
        project = make("project", notion.page_id_of(config.NOTION_PARENT_PAGE), name, notion_docs.NOTICE)
    page = page_of(kind) or make(kind, project["page_id"], notion_docs.TITLES.get(kind, kind))

    try:
        notion.replace_markdown(page["page_id"], row["payload"]["markdown"])
    except Exception as e:
        if notion.is_missing(e):  # Notion에서 페이지를 지웠다 → 기록을 지워 다음 시도 때 새로 만든다
            with get_connection() as conn:
                conn.execute("delete from notion_pages where id = ?", (page["id"],))
        raise

    if row["payload"].get("version_id") and page["url"]:
        with get_connection() as conn:  # 기획서 화면의 [Notion 페이지] 링크
            update(conn, "artifact_versions", row["payload"]["version_id"], external_url=page["url"])


def _newer_exists(row: dict) -> bool:
    """같은 문서의 더 새 게시가 이미 쌓여 있는지. 있으면 이 게시는 건너뛴다.

    예전 게시가 실패했다가 나중에 성공하면 새 내용을 옛 내용으로 덮어쓰게 되므로 막는다.
    """
    with get_connection() as conn:
        newer = fetch_one(
            conn,
            "select id from notifications where channel = 'notion' and project_id = ? and recipient = ? and id > ?",
            (row["project_id"], row["recipient"], row["id"]),
        )
    return newer is not None


def send_pending(sender=None, notion=None) -> int:
    """보낼 차례인 알림과 게시를 처리한다. 처리한 수를 돌려준다. NOTIFY_ENABLED가 꺼져 있으면 아무것도 안 한다.

    sender: Slack 보내기 함수. 테스트에서 가짜를 넣을 때 쓴다. 기본은 integrations.slack.send
    notion: Notion 모듈. 테스트에서 가짜를 넣을 때 쓴다. 기본은 integrations.notion
    """
    if not config.NOTIFY_ENABLED:
        return 0

    with get_connection() as conn:
        rows = fetch_all(
            conn,
            "select * from notifications where status in ('pending', 'failed') "
            "and attempts < ? and next_try_at <= ? order by id limit 20",
            (config.NOTIFY_MAX_ATTEMPTS, now()),
        )

    sent = 0
    for row in rows:
        attempts = row["attempts"] + 1
        try:
            # 바깥 호출은 DB 연결 밖에서 한다. MCP 관련 코드는 실제로 보낼 때만 불러온다
            if row["channel"] == "notion":
                if _newer_exists(row):
                    with get_connection() as conn:
                        update(conn, "notifications", row["id"], status="sent", attempts=attempts, sent_at=now(),
                               last_error="더 새 내용이 있어 건너뜀")
                    continue
                if notion is None:
                    from integrations import notion
                _publish_notion(row, notion)
            else:
                if sender is None:
                    from integrations import slack
                    sender = slack.send
                sender(row["recipient"], row["payload"]["text"])
        except Exception as e:
            wait = config.NOTIFY_RETRY_SECONDS * 2 ** (attempts - 1)  # 30초, 60초, 120초 ...
            next_try = (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(timespec="milliseconds")
            error = _scrub(str(e) or type(e).__name__)
            log.warning("%s 실패 (%s/%s): id=%s %s", row["channel"], attempts, config.NOTIFY_MAX_ATTEMPTS, row["id"], error)
            with get_connection() as conn:
                update(conn, "notifications", row["id"], status="failed", attempts=attempts,
                       next_try_at=next_try, last_error=error)
            continue
        with get_connection() as conn:
            update(conn, "notifications", row["id"], status="sent", attempts=attempts, sent_at=now(), last_error=None)
        sent += 1
    return sent
