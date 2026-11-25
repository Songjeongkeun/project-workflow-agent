import { useContext, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { postJson } from "../../api/client";
import { AuthContext } from "../../context/AuthContext";
import useApi from "../../hooks/useApi";
import Button from "../../components/button/Button";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import PageTitle from "../../components/pagetitle/PageTitle";
import TableCard from "../../components/tablecard/TableCard";
import { DECISION_LABEL, RESULT_LABEL, ROLE_LABEL, dayTimeText, shortSha } from "../../utils/labels";
import styles from "./ApprovalDetailPage.module.css";

// 승인 지점별 화면 제목과 승인 버튼 이름 (피그마 SCR-007)
const TITLE = { G2: "화면 설계 승인", G3: "코드 변경 승인", G4: "배포 승인" };
const APPROVE_LABEL = { G2: "승인", G3: "변경 승인", G4: "배포 승인" };
const COMMENT_HINT = {
  G2: "반려할 경우 수정할 내용을 입력하세요.",
  G3: "변경 필요성과 작업 범위를 검토해주세요. 반려 시 의견이 필요합니다.",
  G4: "배포에 대한 의견을 입력하세요. 반려 시 의견이 필요합니다.",
};

// 승인 상세 (SCR-007, G2~G4)
// data = { id, project_id, project_name, gate, role, run_id, status, requested_at, target_hash, target_version,
//          can_decide, decision: {approver, role, decision, comment, created_at} | null, g2, g3, g4 }
export default function ApprovalDetailPage() {
  const { aid } = useParams();
  const navigate = useNavigate();
  const { refresh } = useContext(AuthContext);
  const [, error, data, reload] = useApi(`/approvals/${aid}`);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [commentError, setCommentError] = useState(false);

  if (!data) return <div className={styles.page}>{error ? <ErrorBox error={error} /> : <Loading />}</div>;

  // 승인 또는 반려. 처리하면 승인함의 처리한 내역으로 간다
  const handleDecide = async (decision) => {
    if (decision === "reject" && comment.trim().length === 0) {
      setCommentError(true); // 피그마 "반려 의견 오류": 의견 칸을 빨갛게
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      await postJson(`/approvals/${data.id}/${decision}`, { target_hash: data.target_hash, comment });
      await refresh();
      navigate("/approvals?view=done");
    } catch (e) {
      setActionError(e.message);
      await reload();
    } finally {
      setBusy(false);
    }
  };

  const blocked = Boolean(data.g4?.target_problem);
  const decidable = data.can_decide && data.gate !== "G1";

  return (
    <div className={styles.page}>
      <PageTitle title={TITLE[data.gate] ?? data.gate} sub={subText(data)} actions={<StateBadge data={data} />} />

      <StateNotice data={data} />
      <ErrorBox error={actionError} />

      {data.g2 && <G2 data={data} />}
      {data.g3 && <G3 data={data} />}
      {data.g4 && <G4 data={data} />}

      {decidable && (
        <>
          <label className={`${styles.comment} ${commentError ? styles.commentError : ""}`}>
            의견
            <textarea
              rows={2}
              placeholder={COMMENT_HINT[data.gate]}
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                setCommentError(false);
              }}
            />
            {commentError && <span className={styles.commentMessage}>반려할 때는 의견을 입력해야 합니다.</span>}
          </label>
          <div className={styles.footer}>
            <span>{footerText(data)}</span>
            <div className={styles.buttons}>
              {data.gate === "G4" && data.run_id && (
                <Button variant="secondary" to={`/projects/${data.project_id}/runs/${data.run_id}`}>
                  검사 결과 보기
                </Button>
              )}
              <Button variant="secondary" disabled={busy} onClick={() => handleDecide("reject")}>
                반려
              </Button>
              <Button disabled={busy || blocked} onClick={() => handleDecide("approve")}>
                {APPROVE_LABEL[data.gate] ?? "승인"}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// 제목 아래 한 줄
function subText(data) {
  const requested = `요청 ${dayTimeText(data.requested_at)}`;
  if (data.gate === "G2") return `화면 계획 v${data.target_version ?? "-"}  ·  ${requested}`;
  if (data.gate === "G3") return `후보 커밋 ${shortSha(data.g3?.candidate_sha)}  ·  ${ROLE_LABEL[data.role]} 담당  ·  ${requested}`;
  return `${data.gate} · ${data.project_name} · ${ROLE_LABEL[data.role]} 담당 · ${requested}`;
}

function footerText(data) {
  if (data.gate === "G2") return `${ROLE_LABEL[data.role]} 담당으로 화면 계획 v${data.target_version ?? "-"}을 승인합니다.`;
  if (data.gate === "G3") return `승인은 후보 커밋 ${shortSha(data.g3?.candidate_sha)}에만 적용됩니다.`;
  return "승인한 SHA만 배포합니다. 완료는 실제 주소 검사까지 통과한 후 표시합니다.";
}

// 제목 오른쪽 상태 표시
function StateBadge({ data }) {
  let tone = "warn";
  let text = `${data.gate} 승인 대기`;
  if (data.status === "approved") [tone, text] = ["ok", "승인됨"];
  else if (data.status === "rejected") [tone, text] = ["bad", "반려됨"];
  else if (data.status === "void") [tone, text] = ["muted", "무효"];
  else if (data.g4?.target_problem) text = "G4 승인 불가";
  return <span className={`${styles.badge} ${styles[tone]}`}>{text}</span>;
}

// 처리됨, 무효, 권한 없음 안내 (피그마 "처리됨", "무효 승인", "권한 없음")
function StateNotice({ data }) {
  if (data.status === "void") {
    return (
      <Box tone="muted" title="무효가 된 승인 요청입니다">
        대상 버전이 바뀌어 이 요청으로는 결정할 수 없습니다. 새 요청은 승인함에서 확인하세요.
      </Box>
    );
  }
  if (data.decision) {
    const d = data.decision;
    return (
      <Box tone={d.decision === "reject" ? "bad" : "ok"} title={`${DECISION_LABEL[d.decision]}했습니다`}>
        {`${d.approver} (${ROLE_LABEL[d.role]})  ·  ${dayTimeText(d.created_at)}`}
        {d.comment && `  ·  의견: ${d.comment}`}
      </Box>
    );
  }
  if (data.status === "pending" && !data.can_decide) {
    return (
      <Box tone="muted" title={`${ROLE_LABEL[data.role]} 담당만 결정할 수 있습니다`}>
        내용은 볼 수 있지만 승인이나 반려는 할 수 없습니다.
      </Box>
    );
  }
  return null;
}

// G2 화면 승인: 왼쪽 화면 목록, 오른쪽 화면 계획 검사
function G2({ data }) {
  const g2 = data.g2;
  return (
    <div className={styles.split}>
      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>검토할 화면 {g2.screens.length}개</h2>
        {g2.carried_over && <p className={styles.help}>이전 승인을 이어받았습니다: {g2.carried_over}</p>}
        <ul className={styles.screens}>
          {g2.screens.map((screen) => (
            <li key={screen.id} className={styles.screen}>
              <div>
                <p className={styles.screenName}>{screen.name}</p>
                <p className={styles.help}>{screen.id}</p>
              </div>
              {screen.figma_url && (
                <Button variant="secondary" onClick={() => window.open(screen.figma_url, "_blank", "noreferrer")}>
                  Figma 프레임 열기 ↗
                </Button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className={styles.panel}>
        <h2 className={styles.sideTitle}>화면 계획 검사</h2>
        <div className={styles.checkRow}>
          <span>화면·기능 연결</span>
          <Mark ok={g2.check.passed} />
        </div>
        {!g2.check.passed && <p className={styles.help}>빠진 항목: {g2.check.missing.join(", ")}</p>}
      </section>
    </div>
  );
}

// G3 범위 밖 변경: 안내 상자와 파일 표
function G3({ data }) {
  const g3 = data.g3;
  return (
    <>
      <Box tone="warn" title="계획 범위를 넘는 변경입니다">
        {`기술 명세에 없던 파일 ${g3.changes.length}개가 바뀌었습니다. 승인 전에는 다음 단계로 진행하지 않습니다.`}
      </Box>
      <TableCard>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>변경 파일</th>
              <th>이유</th>
            </tr>
          </thead>
          <tbody>
            {g3.changes.map((change) => (
              <tr key={change.path}>
                <td className={`mono ${styles.strong}`}>{change.path}</td>
                <td>{change.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableCard>
    </>
  );
}

// G4 배포 승인: 배포 대상과 버전, 완료 조건 표
function G4({ data }) {
  const g4 = data.g4;
  const target = g4.target ?? {};
  return (
    <>
      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>배포 대상과 버전</h2>
        <dl className={styles.grid}>
          <div>
            <dt>저장소 / 브랜치</dt>
            <dd className={styles.strongValue}>
              {target.repository || "미연결"} · {target.branch || "미확정"}
            </dd>
          </div>
          <div>
            <dt>후보 커밋 SHA</dt>
            <dd className={`${styles.strongValue} ${styles.sha}`}>{g4.sha ?? "아직 생성되지 않음"}</dd>
          </div>
          <div>
            <dt>배포 대상</dt>
            <dd>
              {target.environment === "test" ? "테스트 환경" : target.environment}
              {target.url && ` · ${target.url}`}
            </dd>
          </div>
          <div>
            <dt>검사 계획</dt>
            <dd>{`완료 조건 ${g4.results.length}개 · 실제 주소에서 재검사`}</dd>
          </div>
        </dl>
      </section>
      <TableCard>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>완료 조건</th>
              <th>배포 전 검사</th>
              <th>배포 후 확인</th>
            </tr>
          </thead>
          <tbody>
            {g4.results.map((r) => (
              <tr key={r.ac_id}>
                <td className={styles.strong}>{r.ac_id}</td>
                <td className={r.result === "fail" ? styles.failText : ""}>{RESULT_LABEL[r.result]}</td>
                <td>배포 후 실제 주소에서 다시 검사</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableCard>
      {g4.target_problem && (
        <Box tone="warn" title="승인할 수 없습니다">
          {`${g4.target_problem} 서버 설정을 고친 뒤 실행을 다시 진행하세요.`}
        </Box>
      )}
    </>
  );
}

function Mark({ ok }) {
  return <span className={`${styles.badge} ${ok ? styles.ok : styles.warn}`}>{ok ? "통과" : "미정"}</span>;
}

function Box({ tone, title, children }) {
  return (
    <div className={`${styles.box} ${styles[tone]}`}>
      <p className={styles.boxTitle}>{title}</p>
      <p className={styles.boxText}>{children}</p>
    </div>
  );
}

