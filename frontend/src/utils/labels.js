// 화면에 보여 줄 한국어 이름표와 작은 표시 함수

export const ROLE_LABEL = { planner: "기획", designer: "디자인", developer: "개발", deployer: "배포" };
export const ROLES = ["planner", "designer", "developer", "deployer"];

export const GATE_LABEL = {
  G1: "G1 기획 확정",
  G2: "G2 화면 승인",
  G3: "G3 범위 밖 변경",
  G4: "G4 배포 승인",
};

export const AGENT_LABEL = {
  A01: "A01 기획",
  A02: "A02 화면 설계",
  A03: "A03 개발",
  A04: "A04 테스트",
  A05: "A05 배포",
};

export const RUN_STATUS_LABEL = {
  running: "진행 중",
  waiting_input: "입력 대기",
  waiting_approval: "승인 대기",
  failed: "실패",
  void: "무효",
  done: "완료",
};

export const CHANGE_STATUS_LABEL = {
  analyzing: "분석 중",
  awaiting_approval: "재승인 대기",
  rerunning: "재실행 중",
  done: "완료",
  rejected: "반려",
};

export const DECISION_LABEL = { approve: "승인", reject: "반려", carry_over: "승계" };
export const RESULT_LABEL = { pass: "통과", fail: "실패", not_run: "미실행" };

export function rolesText(roles) {
  return roles.length ? roles.map((r) => ROLE_LABEL[r]).join(" · ") : "역할 없음";
}

export function timeText(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

export function shortSha(sha) {
  return sha ? sha.slice(0, 7) : "-";
}

// 피그마 표기: "11월 12일 13:40"
export function dayTimeText(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${hh}:${mm}`;
}

// 피그마 표기: "방금 전", "5분 전", "3시간 전", "3일 전" (한 달이 넘으면 날짜)
export function agoText(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "방금 전";
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)}일 전`;
  return dayTimeText(iso);
}

// 실행 번호 표기 (피그마 "RUN-003")
export function runName(id) {
  return id ? `RUN-${String(id).padStart(3, "0")}` : "-";
}

// 토큰 수 표기 (피그마 "12.4k")
export function kText(n) {
  if (!n) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

// 시작 ~ 끝 (같은 날이면 끝은 시각만): "11월 12일 13:20 ~ 14:35"
export function rangeText(start, end) {
  if (!start) return "-";
  if (!end) return `${dayTimeText(start)} ~`;
  const a = new Date(start);
  const b = new Date(end);
  const sameDay = a.toDateString() === b.toDateString();
  return `${dayTimeText(start)} ~ ${sameDay ? dayTimeText(end).split(" ").pop() : dayTimeText(end)}`;
}
