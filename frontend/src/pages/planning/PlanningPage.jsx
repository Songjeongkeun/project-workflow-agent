import { useState } from "react";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { getJson, postJson } from "../../api/client";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import Empty from "../../components/empty/Empty";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import Markdown from "../../components/markdown/Markdown";
import PageTitle from "../../components/pagetitle/PageTitle";
import TableCard from "../../components/tablecard/TableCard";
import Tabs from "../../components/tabs/Tabs";
import styles from "./PlanningPage.module.css";

const TABS = [
  { value: "brief", label: "기획서" },
  { value: "criteria", label: "기능과 완료 조건" },
];

// 기획서 검토 (SCR-004, G1). 탭은 주소(?tab=criteria)에 남긴다.
// data = { version, hash, status: "draft" | "confirmed", brief, project_type, tech_stack: [...], acceptance_criteria: [...],
//          required_open_questions, can_confirm, is_planner, blocked_reason, notion_url }
export default function PlanningPage() {
  const { project, reloadProject } = useOutletContext();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "criteria" ? "criteria" : "brief";
  const [, error, data, reload] = useApi(`/projects/${project.id}/planning`, POLL_MS);
  const [evidence, setEvidence] = useState(null); // { id, items: [{ field_path, message_id, quote }] }
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  // 기획서가 아직 없으면 404
  if (error?.status === 404) {
    return (
      <div className={styles.page}>
        <PageTitle title="기획서 검토" />
        <Empty
          title="아직 기획서 초안이 없습니다"
          description="대화에서 만들 서비스를 설명하면 초안이 만들어집니다."
          action={<Button to={`/projects/${project.id}/chat`}>대화로 가기</Button>}
        />
      </div>
    );
  }
  if (!data) return <div className={styles.page}>{error ? <ErrorBox error={error} /> : <Loading />}</div>;

  const confirmed = data.status === "confirmed";
  const setTab = (v) => setParams(v === "criteria" ? { tab: "criteria" } : {});

  // 완료 조건 행을 누르면 대화 속 근거를 보여 준다
  const handleShowEvidence = async (c) => {
    setBusy(true);
    setActionError("");
    try {
      const result = await getJson(
        `/projects/${project.id}/planning/evidence?field_path=${encodeURIComponent(c.source)}`,
      );
      setEvidence({ id: c.id, items: result.evidence });
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  // 확정하면 실행 상태 화면으로
  const handleConfirm = async () => {
    setBusy(true);
    setActionError("");
    try {
      await postJson(`/projects/${project.id}/planning/confirm`, { version: data.version, hash: data.hash });
      await reloadProject();
      navigate(`/projects/${project.id}`);
    } catch (e) {
      setActionError(e.message);
      await reload();
    } finally {
      setBusy(false);
    }
  };

  const badge = (
    <span className={`${styles.badge} ${confirmed ? styles.ok : styles.warn}`}>
      {confirmed ? "확정" : "초안"} v{data.version}
    </span>
  );

  return (
    <div className={styles.page}>
      <PageTitle title={tab === "brief" ? "기획서 검토" : "기능과 완료 조건"} actions={badge} />
      <Tabs items={TABS} value={tab} onChange={setTab} />
      <ErrorBox error={actionError} />

      {tab === "brief" ? (
        <>
          <div className={styles.layout}>
            <section className={styles.brief}>
              <Markdown text={data.brief} />
            </section>
            <ReviewPanel data={data} project={project} confirmed={confirmed} />
          </div>
          <div className={styles.footer}>
            <span>
              {confirmed
                ? "확정된 기획서는 읽기 전용입니다. 바꾸려면 변경 요청을 등록하세요."
                : data.is_planner
                  ? "기획 담당만 기획서·기술 스택·완료 조건을 함께 확정할 수 있습니다."
                  : "기획 담당만 확정하거나 수정 요청할 수 있습니다. 현재는 읽기 전용입니다."}
            </span>
            <div className={styles.buttons}>
              {confirmed && (
                <Button variant="secondary" to={`/projects/${project.id}/changes`}>
                  변경 요청
                </Button>
              )}
              {!confirmed && data.is_planner && (
                <>
                  <Button variant="secondary" to={`/projects/${project.id}/chat`}>
                    수정 요청
                  </Button>
                  <Button disabled={!data.can_confirm || busy} onClick={handleConfirm} className={styles.confirm}>
                    확정
                  </Button>
                </>
              )}
              {!confirmed && !data.is_planner && (
                <Button disabled className={styles.confirm}>
                  읽기 전용
                </Button>
              )}
            </div>
          </div>
        </>
      ) : (
        <Criteria data={data} evidence={evidence} busy={busy} onSelect={handleShowEvidence} onBack={() => setTab("brief")} />
      )}
    </div>
  );
}

// 오른쪽 "기획 검토" 상자
function ReviewPanel({ data, project, confirmed }) {
  const open = data.required_open_questions;
  return (
    <aside className={styles.panel}>
      <h2 className={styles.panelTitle}>기획 검토</h2>
      <p className={styles.small}>G1  ·  기획 담당</p>

      <p className={styles.label}>프로젝트 유형</p>
      <p className={styles.value}>{data.project_type ?? "미정"}</p>

      <p className={styles.label}>기술 스택 제안</p>
      <p className={styles.valueSmall}>{data.tech_stack.map((t) => t.choice).join(" · ") || "미정"}</p>
      {data.tech_stack.some((t) => t.reason) && (
        <p className={styles.help}>{data.tech_stack.map((t) => t.reason).filter(Boolean).join(" ")}</p>
      )}

      <div className={styles.status}>
        {confirmed ? (
          <span className={`${styles.badge} ${styles.ok}`}>확정됨</span>
        ) : (
          <>
            <span className={`${styles.badge} ${open > 0 ? styles.warn : styles.ok}`}>필수 질문 {open}개</span>
            <p className={open > 0 || data.blocked_reason ? styles.warnText : styles.okText}>
              {data.blocked_reason ?? (open > 0 ? "대화에서 남은 질문에 답해 주세요." : "남은 필수 질문이 없습니다.")}
            </p>
          </>
        )}
      </div>

      <div className={styles.panelButtons}>
        {!confirmed && (
          <Button variant="secondary" block to={`/projects/${project.id}/chat`}>
            대화에서 답변하기
          </Button>
        )}
        {data.notion_url && (
          <Button variant="secondary" block onClick={() => window.open(data.notion_url, "_blank", "noreferrer")}>
            Notion에서 열기 ↗
          </Button>
        )}
      </div>
    </aside>
  );
}

// "기능과 완료 조건" 탭: 표와, 고른 항목의 근거 메시지
function Criteria({ data, evidence, busy, onSelect, onBack }) {
  const rows = data.acceptance_criteria;
  const required = rows.filter((c) => c.required).length;
  const needsReview = rows.some((c) => c.origin !== "명시");
  return (
    <>
      <p className={styles.note}>필수 완료 조건 {required}개  ·  항목을 선택하면 근거가 된 발언을 확인할 수 있습니다.</p>
      <TableCard>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>ID / 기능</th>
              <th>종류</th>
              <th>완료 조건</th>
              <th>출처</th>
              <th>필수</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr
                key={c.id}
                className={`${styles.row} ${evidence?.id === c.id ? styles.selected : ""}`}
                onClick={() => !busy && onSelect(c)}
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(c)}
              >
                <td className={styles.strong}>
                  {c.id}
                  <br />
                  {c.feature_id} {c.feature_name}
                </td>
                <td>{c.kind}</td>
                <td>{c.condition}</td>
                <td className={c.origin === "추론" ? styles.warnText : c.origin === "미정" ? styles.badText : ""}>{c.origin}</td>
                <td>{c.required ? "필수" : "선택"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableCard>

      {evidence && (
        <section className={styles.evidence}>
          <p className={styles.evidenceTitle}>{evidence.id}  ·  원문 메시지</p>
          {evidence.items.length === 0 ? (
            <p className={styles.evidenceText}>대화에 근거가 없습니다 (추론 또는 미정 항목).</p>
          ) : (
            evidence.items.map((item, i) => (
              <p key={i} className={styles.evidenceText}>
                “{item.quote}” <span className={styles.small}>메시지 #{item.message_id}</span>
              </p>
            ))
          )}
        </section>
      )}

      <div className={styles.backRow}>
        <Button variant="secondary" onClick={onBack} className={styles.back}>
          기획서로 돌아가기
        </Button>
        {needsReview && <span className={styles.warnText}>추론 항목은 기획 담당의 검토가 필요합니다.</span>}
      </div>
    </>
  );
}
