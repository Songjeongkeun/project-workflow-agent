import { useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { postJson } from "../../api/client";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import PageTitle from "../../components/pagetitle/PageTitle";
import TableCard from "../../components/tablecard/TableCard";
import { AGENT_LABEL, GATE_LABEL, ROLE_LABEL, dayTimeText, runName } from "../../utils/labels";
import styles from "./RunStatusPage.module.css";

const STEP_TEXT = { pending: "대기", running: "진행 중", waiting: "대기 중", completed: "완료", failed: "실패", skipped: "건너뜀", void: "무효" };
// 단계 아래 줄에 붙는 승인 지점 설명 (피그마 "완료 · G1 확정")
const GATE_NOTE = { A01: "G1 확정", A02: "G2 승인", A03: "G3 필요 시", A05: "G4 승인" };
// 표의 "산출물" 열
const OUTPUT = {
  A01: "기획서 · 기능과 완료 조건",
  A02: "Figma 화면 · 화면 계획",
  A03: "기술 명세 · 구현 후보",
  A04: "완료 조건별 검사 결과",
  A05: "배포 주소 · 배포 후 검사",
};
// 아직 결과가 없을 때 "상태" 열
const NOT_YET = { A02: "아직 시작하지 않음", A03: "아직 시작하지 않음", A04: "아직 검사하지 않음", A05: "아직 배포하지 않음" };
const GATE_AGENT = { G1: "A01", G2: "A02", G3: "A03", G4: "A05" };

const countPass = (rows) => `${rows.filter((r) => r.result === "pass").length}/${rows.length}`;

// 실행 상태 (SCR-005, 프로젝트의 첫 화면)
// data = { run_id, status, current_step, steps, waiting, input, retry, failure, deployment_url,
//          post_results, pre_results, deployment, plan_version, replaced_by }
export default function RunStatusPage() {
  const { project, reloadProject } = useOutletContext();
  const pid = project.id;
  const [, error, data, reload] = useApi(`/projects/${pid}/run`, POLL_MS);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  if (!data) return <div className={styles.page}>{error ? <ErrorBox error={error} /> : <Loading />}</div>;

  // 범위 확인, 다시 시도 버튼
  const handleAction = async (path) => {
    setBusy(true);
    setActionError("");
    try {
      await postJson(path);
      await reload();
      await reloadProject();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const historyButton = (
    <Button to={`/projects/${pid}/runs${data.run_id ? `/${data.run_id}` : ""}`} variant="secondary">
      실행 기록
    </Button>
  );

  // Figma 작업 범위 확인은 화면 전체를 쓴다 (피그마 "SCR-005 · Figma 작업 범위 확인")
  if (data.input?.type === "scope_confirm") {
    return (
      <ScopeConfirm
        data={data}
        busy={busy}
        actionError={actionError}
        onConfirm={() => handleAction(`/runs/${data.run_id}/scope-confirm`)}
      />
    );
  }

  if (data.status === "done") {
    return <Done data={data} project={project} historyButton={historyButton} />;
  }

  return (
    <div className={styles.page}>
      <PageTitle title="실행 상태" sub={subText(data)} actions={historyButton} />
      <ErrorBox error={actionError} />
      <Pipeline data={data} />
      <StatusNotice data={data} pid={pid} busy={busy} onRetry={() => handleAction(`/runs/${data.run_id}/retry`)} />
      <OutputTable data={data} pid={pid} />
      <div>
        <Button to={`/projects/${pid}/changes`} variant="secondary" className={styles.wide}>
          변경 요청
        </Button>
      </div>
    </div>
  );
}

// "실행 RUN-003 · 기획서 v1 · 화면 설계 승인 대기"
function subText(data) {
  if (!data.run_id) return data.status === "waiting_input" ? "G1 확정 전 · 답해야 할 질문이 있습니다" : "G1 확정 전";
  const now = AGENT_LABEL[data.current_step]?.split(" ").slice(1).join(" ");
  const phase = {
    running: `${now ?? ""} 진행 중`,
    waiting_input: "입력 대기",
    waiting_approval: data.waiting ? `${GATE_LABEL[data.waiting.gate].split(" ").slice(1).join(" ")} 대기` : "승인 대기",
    failed: `${data.current_step ?? ""} 실패`,
    void: "무효",
  }[data.status];
  return [runName(data.run_id), `기획서 v${data.plan_version}`, phase].filter(Boolean).join("  ·  ");
}

// 5단계 진행 상자. 지금 단계는 보라, 기다리거나 멈춘 상태는 갈색 글씨
function Pipeline({ data }) {
  return (
    <div className={styles.pipeline}>
      {data.steps.map((step) => {
        const current = data.run_id && step.agent === data.current_step && step.status !== "completed";
        let note = GATE_NOTE[step.agent];
        if (data.waiting && GATE_AGENT[data.waiting.gate] === step.agent && step.status === "waiting") {
          note = `${data.waiting.gate} ${ROLE_LABEL[data.waiting.role]}`;
        }
        if (step.agent === "A04" && data.retry && data.retry.count > 0) note = `수정 ${data.retry.count}/${data.retry.limit}`;
        const label = step.status === "waiting" && data.status === "waiting_approval" ? "승인 대기" : STEP_TEXT[step.status];
        return (
          <div key={step.agent} className={styles.step}>
            <div className={`${styles.stepName} ${current ? styles.current : ""}`}>{AGENT_LABEL[step.agent]}</div>
            <div className={`${styles.stepNote} ${current && step.status !== "running" ? styles.attention : ""}`}>
              {note ? `${label} · ${note}` : label}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// 단계 상자 아래의 안내 상자와 버튼 (승인 대기, 실패, 무효, 기획 확정 전)
function StatusNotice({ data, pid, busy, onRetry }) {
  if (!data.run_id) {
    const needAnswer = data.status === "waiting_input";
    return (
      <>
        <Box tone="warn" title={needAnswer ? "답해야 할 질문이 있습니다" : "기획서 확정을 기다리고 있습니다"}>
          {needAnswer
            ? "대화에서 질문에 답하면 기획서 초안이 갱신됩니다."
            : "기획서 검토 화면에서 확정하면 실행이 시작됩니다."}
        </Box>
        <ActionRow button={<Button to={`/projects/${pid}/${needAnswer ? "chat" : "planning"}`}>{needAnswer ? "대화에서 보완하기" : "기획서 검토"}</Button>} />
      </>
    );
  }

  if (data.waiting) {
    const w = data.waiting;
    const to = w.gate === "G1" ? `/projects/${pid}/planning` : `/approvals/${w.approval_id}`;
    return (
      <>
        <Box tone="warn" title={`${ROLE_LABEL[w.role]} 담당의 승인을 기다리고 있습니다`}>
          {`${GATE_LABEL[w.gate]}  ·  담당 역할: ${ROLE_LABEL[w.role]}  ·  요청 ${dayTimeText(w.requested_at)}`}
        </Box>
        <ActionRow
          text="내가 이 승인의 담당 역할일 때만 버튼이 보입니다."
          button={w.can_decide && <Button to={to}>승인 화면으로 가기</Button>}
        />
      </>
    );
  }

  if (data.failure) {
    const f = data.failure;
    return (
      <>
        <Box tone="bad" title={`${AGENT_LABEL[f.step] ?? f.step} 단계를 완료하지 못했습니다`}>
          {`${f.step} · ${f.reason}`}
        </Box>
        <ActionRow
          text={`${ROLE_LABEL[f.role] ?? "담당"} 담당만 멈춘 ${f.step} 단계부터 다시 시도할 수 있습니다.`}
          button={f.can_retry && <Button disabled={busy} onClick={onRetry}>다시 시도</Button>}
        />
      </>
    );
  }

  if (data.status === "void") {
    return (
      <>
        <Box tone="muted" title="무효가 된 실행입니다">변경 요청으로 대체되어 더 진행하지 않습니다.</Box>
        {data.replaced_by && (
          <ActionRow button={<Button to={`/projects/${pid}/runs/${data.replaced_by}`}>새 실행 {runName(data.replaced_by)} 보기</Button>} />
        )}
      </>
    );
  }

  if (data.status === "running") {
    return (
      <Box tone="info" title={`${AGENT_LABEL[data.current_step] ?? ""} 진행 중입니다`}>
        끝나면 이 화면이 자동으로 갱신됩니다.
      </Box>
    );
  }
  return null;
}

// 단계별 산출물 표: 단계 / 산출물 / 상태 / 작업
function OutputTable({ data, pid }) {
  const runPath = data.run_id ? `/projects/${pid}/runs/${data.run_id}` : null;
  const g4 = data.waiting?.gate === "G4" && data.waiting.can_decide ? `/approvals/${data.waiting.approval_id}` : null;

  const rows = data.steps.map((step) => {
    let state;
    if (step.agent === "A01") state = data.plan_version ? `v${data.plan_version} 기준` : "초안 작성 중";
    else if (step.status === "skipped") state = "화면 없음 · 건너뜀";
    else if (step.summary) state = step.status === "waiting" && data.waiting ? `${step.summary} · 승인 대기` : step.summary;
    else state = step.status === "running" ? "진행 중" : NOT_YET[step.agent];

    const figma = step.links?.find((l) => !l.url.startsWith("/"));
    const action = {
      A01: { label: "기획서 보기", to: `/projects/${pid}/planning` },
      A02: figma ? { label: "화면 보기", href: figma.url } : null,
      A03: step.summary && runPath ? { label: "개발 보기", to: runPath } : null,
      A04: step.summary && runPath ? { label: "검사 결과 보기", to: runPath } : null,
      A05: g4 ? { label: "배포 검토", to: g4 } : null,
    }[step.agent];
    return { step, state, action };
  });

  return (
    <TableCard>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>단계</th>
            <th>산출물</th>
            <th>상태</th>
            <th>작업</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ step, state, action }) => (
            <tr key={step.agent}>
              <td className={styles.strong}>{AGENT_LABEL[step.agent]}</td>
              <td>{OUTPUT[step.agent]}</td>
              <td>{state}</td>
              <td>
                {!action && <span className={styles.none}>-</span>}
                {action?.to && <Link to={action.to}>{action.label}</Link>}
                {action?.href && (
                  <a href={action.href} target="_blank" rel="noreferrer">
                    {action.label} ↗
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableCard>
  );
}

// 배포 완료 (피그마 "SCR-005 · 배포 완료")
function Done({ data, project, historyButton }) {
  const post = data.post_results ?? [];
  const pre = data.pre_results ?? [];
  const allPass = post.length > 0 && post.every((r) => r.result === "pass");
  return (
    <div className={styles.page}>
      <PageTitle
        title="실행 상태"
        sub={`${project.name} · ${runName(data.run_id)} · 기획서 v${data.plan_version}`}
        actions={historyButton}
      />
      <div className={styles.doneBanner}>
        <p className={styles.doneTitle}>테스트 환경에 배포했습니다</p>
        <p className={styles.doneText}>
          {allPass ? `배포 후 필수 검사 ${post.length}개가 모두 통과했습니다.` : `배포 후 검사 ${countPass(post)} 통과`}
        </p>
        <div className={styles.doneUrl}>
          <span>{data.deployment_url}</span>
          {data.deployment_url && (
            <Button variant="secondary" onClick={() => window.open(data.deployment_url, "_blank", "noreferrer")}>
              서비스 열기 ↗
            </Button>
          )}
        </div>
      </div>
      <div className={`${styles.pipeline} ${styles.donePipeline}`}>
        {data.steps.map((step) => (
          <div key={step.agent} className={styles.step}>
            <div className={styles.stepName}>{AGENT_LABEL[step.agent]}</div>
            <div className={styles.okNote}>{step.status === "skipped" ? "건너뜀" : "완료"}</div>
          </div>
        ))}
      </div>
      <TableCard>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>배포 ID</th>
              <th>커밋</th>
              <th>검사 결과</th>
              <th>완료 시각</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className={styles.strong}>{data.deployment?.id ?? "-"}</td>
              <td className="mono">{data.deployment?.sha?.slice(0, 7) ?? "-"}</td>
              <td>{`배포 전 ${countPass(pre)} · 배포 후 ${countPass(post)}`}</td>
              <td>{dayTimeText(data.deployment?.finished_at)}</td>
            </tr>
          </tbody>
        </table>
      </TableCard>
    </div>
  );
}

// 피그마 "Figma 작업 범위 확인": 왼쪽 작업 대상, 오른쪽 변경 범위와 버튼
function ScopeConfirm({ data, busy, actionError, onConfirm }) {
  const input = data.input;
  return (
    <div className={styles.page}>
      <PageTitle title="화면 설계 작업 범위" sub={`A02 화면 설계  ·  기획서 v${data.plan_version} 기준`} />
      <ErrorBox error={actionError} />
      <div className={styles.scope}>
        <section className={styles.panel}>
          <h2 className={styles.panelTitle}>Figma 작업 대상</h2>
          <p className={styles.label}>대상 파일</p>
          <p className={styles.valueStrong}>{input.figma_file}</p>
          <p className={styles.label}>대상 페이지</p>
          <p className={styles.value}>{input.page}</p>
          <p className={styles.label}>작업 범위</p>
          {input.frames.map((frame) => (
            <p key={frame} className={styles.valueStrong}>
              {frame}
            </p>
          ))}
          <p className={styles.help}>{input.changes}</p>
        </section>
        <div className={styles.side}>
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>변경되는 범위</h2>
            <p className={styles.value}>새 프레임 {input.frames.length}개</p>
            <p className={styles.help}>
              이 확인은 작업 범위에 대한 동의입니다.
              <br />
              생성한 화면은 G2에서 별도로 검토합니다.
            </p>
          </section>
          {input.can_confirm ? (
            <Button block disabled={busy} onClick={onConfirm}>
              범위 확인 후 초안 만들기
            </Button>
          ) : (
            <p className={styles.help}>디자인 담당의 확인을 기다리고 있습니다.</p>
          )}
          <p className={styles.small}>디자인 담당  ·  승인된 대상에서만 작업합니다.</p>
        </div>
      </div>
    </div>
  );
}

// 색이 있는 안내 상자 (피그마: 높이 94px, 제목 굵게 + 설명)
function Box({ tone, title, children }) {
  return (
    <div className={`${styles.box} ${styles[tone]}`}>
      <p className={styles.boxTitle}>{title}</p>
      <p className={styles.boxText}>{children}</p>
    </div>
  );
}

// 안내 상자 아래 줄: 왼쪽 설명, 오른쪽 버튼
function ActionRow({ text, button }) {
  if (!text && !button) return null;
  return (
    <div className={styles.actionRow}>
      <span>{text}</span>
      {button && <div className={styles.actionButton}>{button}</div>}
    </div>
  );
}
