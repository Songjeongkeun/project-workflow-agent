"""Notion에 게시할 문서 내용 (02 4.6).

문서는 이 파일의 함수가 정해진 형식의 마크다운으로 만든다. LLM이 직접 쓰지 않는다.
Notion은 팀이 확인하는 곳이다. 버전·승인·변경 이력의 기준은 서버 DB이고,
Notion에서 내용을 고쳐도 Orbit에는 반영하지 않는다 (내용 변경은 변경 요청으로).
"""

TITLES = {
    "planning": "기획서와 완료 조건",
    "tech_spec": "기술 명세",
    "checks": "검사 결과",
    "deploy": "배포 기록",
}

# 페이지 아이콘 (project = 프로젝트 상위 페이지)
ICONS = {"project": "📁", "planning": "📋", "tech_spec": "🧩", "checks": "✅", "deploy": "🚢"}

NOTICE = "Orbit이 자동으로 게시하는 문서입니다. 여기서 고친 내용은 Orbit에 반영되지 않습니다. 내용을 바꾸려면 Orbit에서 변경 요청을 하세요."


def _head(status: str, link: str, color: str = "blue_bg") -> str:
    """맨 위 상태 상자와 안내 문구 (Notion 마크다운의 callout 블록).

    color: 초안 yellow_bg, 확정·모두 통과 green_bg, 실패가 있으면 red_bg
    """
    return (
        f'<callout icon="📌" color="{color}">\n\t**{status}** · [Orbit에서 보기]({link})\n</callout>\n'
        f'<callout icon="ℹ️" color="gray_bg">\n\t{NOTICE}\n</callout>\n\n'
    )


def _color(results: list[dict]) -> str:
    return "green_bg" if results and all(r["result"] == "pass" for r in results) else "red_bg"


def _one_line(text) -> str:
    return str(text or "").replace("\n", " ").strip()


def planning(version: dict, bundle: dict, link: str) -> str:
    """기획서(brief.md)와 완료 조건 목록."""
    confirmed = version["status"] == "confirmed"
    status = "확정" if confirmed else "초안"
    lines = [_head(f"{status} v{version['version']}", link, "green_bg" if confirmed else "yellow_bg"),bundle["brief"].strip(), "", "## 완료 조건", ""]
    for c in bundle["criteria"]:
        need = "필수" if c.get("required") else "선택"
        lines.append(f"- **{c['id']}** ({c.get('feature_id', '')}, {need}) {_one_line(c.get('condition'))}")
    return "\n".join(lines) + "\n"


def tech_spec(version: dict, spec: dict, sha: str, link: str) -> str:
    """A03 기술 명세 (확인용)."""
    lines = [_head(f"v{version['version']} · 후보 버전 {sha[:12]}", link), "## 구조", "", _one_line(spec.get("architecture")) or "(없음)", ""]
    lines += ["## API", ""]
    for a in spec.get("apis", []):
        related = ", ".join(a.get("related_features", []))
        lines.append(f"- **{a.get('id', '')}** `{a.get('method', '')} {a.get('path', '')}` ({related})")
    if not spec.get("apis"):
        lines.append("- (없음)")
    lines += ["", "## 데이터", ""]
    for e in spec.get("entities", []):
        lines.append(f"- {_one_line(e.get('name') if isinstance(e, dict) else e)}")
    if not spec.get("entities"):
        lines.append("- (없음)")
    lines += ["", "## 바꿀 파일 범위", ""] + [f"- `{p}`" for p in spec.get("planned_paths", [])]
    return "\n".join(lines) + "\n"


def _results(results: list[dict], criteria: dict[str, dict]) -> list[str]:
    mark = {"pass": "통과", "fail": "실패"}
    lines = []
    for r in results:
        c = criteria.get(r["ac_id"], {})
        lines.append(f"- **{r['ac_id']}** {mark.get(r['result'], r['result'])} · {_one_line(c.get('condition'))}")
    return lines or ["- (검사 결과 없음)"]


def checks(sha: str, results: list[dict], criteria: dict[str, dict], link: str) -> str:
    """A04 배포 전 검사 결과 (G4 승인 대상)."""
    passed = sum(1 for r in results if r["result"] == "pass")
    lines = [_head(f"배포 전 검사 {passed}/{len(results)} 통과 · 버전 {sha[:12]}", link, _color(results)),"## 완료 조건별 결과", ""]
    return "\n".join(lines + _results(results, criteria)) + "\n"


def deploy(run: dict, results: list[dict], criteria: dict[str, dict], link: str) -> str:
    """A05 배포 결과와 배포 후 검사."""
    passed = sum(1 for r in results if r["result"] == "pass")
    lines = [
        _head(f"배포 완료 · 배포 후 검사 {passed}/{len(results)} 통과", link, _color(results)),
        "## 배포",
        "",
        f"- 주소: {run['deployment_url']}",
        f"- 커밋: `{(run['pushed_sha'] or '')[:12]}`",
        f"- 배포 ID: {run['deployment_id']}",
        "",
        "## 배포 후 검사",
        "",
    ]
    return "\n".join(lines + _results(results, criteria)) + "\n"
