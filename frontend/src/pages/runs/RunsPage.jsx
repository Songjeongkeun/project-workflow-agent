import { useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { postJson } from "../../api/client";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import Empty from "../../components/empty/Empty";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import PageTitle from "../../components/pagetitle/PageTitle";
import StatusBadge from "../../components/statusbadge/StatusBadge";
import TableCard from "../../components/tablecard/TableCard";
import {
  DECISION_LABEL,
  RESULT_LABEL,
  ROLE_LABEL,
  RUN_STATUS_LABEL,
  dayTimeText,
  kText,
  rangeText,
  runName,
  shortSha,
} from "../../utils/labels";
import styles from "./RunsPage.module.css";

// 실행 기록 (SCR-008): 무엇이 왜 배포됐는지 거꾸로 따라간다
// list = { runs: [{ id, plan_version, status, started_at, finished_at, change_request_id, redeploy_of_run_id, can_redeploy }], has_active_run }
// detail = { id, status, trace: {...}, test_results: [...], approvals: [...], usage: [...] }
export default function RunsPage() {
  const { project } = useOutletContext();
  const { rid } = useParams();
  const navigate = useNavigate();
  const [, listError, list] = useApi(`/projects/${project.id}/runs`, POLL_MS);
  // 주소에 실행 번호가 없으면 가장 최근 실행을 보여 준다
  const selected = rid ? Number(rid) : list?.runs[0]?.id;
  const [, detailError, detail] = useApi(selected ? `/runs/${selected}` : null, POLL_MS);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  if (!list) return <div className={styles.page}>{listError ? <ErrorBox error={listError} /> : <Loading />}</div>;

  const title = <PageTitle title="실행 기록" sub="요청부터 배포 결과까지 연결된 실행을 확인합니다." />;
  if (list.runs.length === 0) {
    return (
      <div className={styles.page}>
        {title}
        <Empty
          title="아직 실행이 없습니다"
          description="기획서를 확정하면 첫 실행이 시작됩니다."
          action={<Button variant="secondary" to={`/projects/${project.id}/planning`}>기획서 보기</Button>}
        />
      </div>
    );
  }
  const current = list.runs.find((r) => r.id === selected);

  // 다시 배포하면 새 G4 승인 화면으로
  const handleRedeploy = async (runId) => {
    setBusy(true);
    setActionError("");
    try {
      const result = await postJson(`/runs/${runId}/redeploy`);
      navigate(`/approvals/${result.approval_id}`);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.page}>
      {title}
      <ErrorBox error={actionError || detailError} />
      <div className={styles.layout}>
        <nav className={styles.list}>
          {list.runs.map((run) => (
            <Link
              key={run.id}
              className={`${styles.item} ${run.id === selected ? styles.selected : ""}`}
              to={`/projects/${project.id}/runs/${run.id}`}
            >
              <span className={styles.itemName}>{runName(run.id)}</span>
              <span className={styles.itemMeta}>
                계획 v{run.plan_version} · {RUN_STATUS_LABEL[run.status]}
                {run.redeploy_of_run_id && " · 재배포"}
                {run.change_request_id && ` · 변경 CR-${String(run.change_request_id).padStart(3, "0")}`}
              </span>
              <span className={styles.itemTime}>{rangeText(run.started_at, run.finished_at)}</span>
            </Link>
          ))}
        </nav>

        <section className={styles.detail}>
          {!detail && <Loading />}
          {detail && (
            <Detail
              detail={detail}
              project={project}
              canRedeploy={current?.can_redeploy}
              blocked={list.has_active_run}
              busy={busy}
              onRedeploy={() => handleRedeploy(detail.id)}
            />
          )}
        </section>
      </div>
      <p className={styles.note}>
        {current?.can_redeploy
          ? list.has_active_run
            ? "진행 중인 실행이 있어 다시 배포할 수 없습니다."
            : "배포 담당만 다시 배포할 수 있습니다. 새 G4 승인을 요청합니다."
          : "필수 검사가 끝나지 않은 실행은 다시 배포할 수 없습니다."}
      </p>
    </div>
  );
}

function Detail({ detail, project, canRedeploy, blocked, busy, onRedeploy }) {
  const t = detail.trace;
  const cr = t.change_request_id ? `변경 요청 CR-${String(t.change_request_id).padStart(3, "0")}` : "변경 요청 없음";
  // 추적 경로: 배포 → 커밋 → 실행 계획 → 기획서 → 변경 요청 → 원문
  const chain = [
    t.deployment_id ? `배포 ${t.deployment_id}` : "배포 미생성",
    t.sha ? `커밋 ${shortSha(t.sha)}` : "커밋 미연결",
    `실행 계획 v${t.plan_version}`,
    `기획서 v${t.planning_version}`,
    cr,
    "원문 메시지",
  ];

  // 같은 완료 조건의 배포 전/후 결과를 한 줄로
  const byAc = {};
  for (const r of detail.test_results) {
    byAc[r.ac_id] = byAc[r.ac_id] ?? { pre: null, post: null };
    byAc[r.ac_id][r.phase] = r.result;
  }

  return (
    <>
      <div className={styles.detailHead}>
        <h2 className={styles.detailTitle}>{runName(detail.id)}</h2>
        <div className={styles.headRight}>
          <StatusBadge status={detail.status} />
          {canRedeploy && (
            <Button disabled={blocked || busy} onClick={onRedeploy}>
              이 버전 다시 배포
            </Button>
          )}
        </div>
      </div>

      <h3 className={styles.sectionTitle}>추적 경로</h3>
      <p className={styles.chain}>{chain.join("  →  ")}</p>
      {t.deployment_url && (
        <p className={styles.small}>
          배포 주소{" "}
          <a href={t.deployment_url} target="_blank" rel="noreferrer">
            {t.deployment_url}
          </a>
          {t.redeploy_of_run_id && `  ·  재배포 원본 ${runName(t.redeploy_of_run_id)}`}
        </p>
      )}
      <div className={styles.traceButtons}>
        <Button variant="secondary" to={`/projects/${project.id}/planning`}>
          기획서 v{t.planning_version}
        </Button>
        <Button variant="secondary" to={`/projects/${project.id}/chat`}>
          원문 대화 보기
        </Button>
        {t.change_request_id && (
          <Button variant="secondary" to={`/projects/${project.id}/changes/${t.change_request_id}`}>
            변경 요청 보기
          </Button>
        )}
      </div>

      <h3 className={styles.sectionTitle}>완료 조건별 검사</h3>
      {Object.keys(byAc).length === 0 ? (
        <p className={styles.small}>아직 검사하지 않았습니다.</p>
      ) : (
        <TableCard>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>조건</th>
                <th>배포 전</th>
                <th>배포 후</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byAc).map(([ac, r]) => (
                <tr key={ac}>
                  <td className={styles.strong}>{ac}</td>
                  <td className={r.pre === "fail" ? styles.fail : ""}>{r.pre ? RESULT_LABEL[r.pre] : "미실행"}</td>
                  <td className={r.post === "fail" ? styles.fail : ""}>{r.post ? RESULT_LABEL[r.post] : "미실행"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableCard>
      )}

      <h3 className={styles.sectionTitle}>승인 이력</h3>
      {detail.approvals.length === 0 ? (
        <p className={styles.small}>아직 승인 기록이 없습니다.</p>
      ) : (
        <ul className={styles.approvals}>
          {detail.approvals.map((a, i) => (
            <li key={i}>
              <span className={styles.gate}>{a.gate}</span>
              {`${a.approver} · ${ROLE_LABEL[a.role]} · ${a.gate === "G1" && a.decision === "approve" ? "확정" : DECISION_LABEL[a.decision] ?? a.decision} · ${dayTimeText(a.created_at)}`}
              {a.comment && <span className={styles.comment}>의견: {a.comment}</span>}
            </li>
          ))}
        </ul>
      )}

      <div className={styles.usage}>
        <span>토큰 사용량  입력 / 출력</span>
        <span>
          {detail.usage.length === 0
            ? "기록 없음"
            : detail.usage.map((u) => `${u.agent} ${kText(u.input_tokens)} / ${kText(u.output_tokens)}`).join(" · ")}
        </span>
      </div>
    </>
  );
}
