import { useContext } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AuthContext } from "../../context/AuthContext";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import Empty from "../../components/empty/Empty";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import PageTitle from "../../components/pagetitle/PageTitle";
import TableCard from "../../components/tablecard/TableCard";
import Tabs from "../../components/tabs/Tabs";
import { ROLE_LABEL, dayTimeText } from "../../utils/labels";
import styles from "./ApprovalsPage.module.css";

// G1은 기획서 검토 화면에서, 나머지는 승인 상세에서 처리한다
const targetPath = (item) => (item.gate === "G1" ? `/projects/${item.project_id}/planning` : `/approvals/${item.id}`);
// "G2 화면 1개" 처럼 승인 지점과 대상
const targetText = (item) => `${item.gate} ${item.summary}`;
// G1 승인은 기획서 "확정"이라 부른다
const decisionText = (item) =>
  item.decision === "reject" ? "반려" : item.decision === "carry_over" ? "승계" : item.gate === "G1" ? "확정" : "승인";

// 승인함 (SCR-006). 탭은 주소(?view=done)에 남겨 새로고침해도 유지한다.
// data = { items: [{ id, project_id, project_name, gate, role, summary, requested_at, decision?, comment?, decided_at? }] }
export default function ApprovalsPage() {
  const { me } = useContext(AuthContext);
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "done" ? "done" : "pending";
  // 대기 건수는 어느 탭에서든 탭 이름에 보인다
  const [pendingLoading, pendingError, pending] = useApi("/approvals?view=pending", POLL_MS);
  const [doneLoading, doneError, done] = useApi(view === "done" ? "/approvals?view=done" : null, POLL_MS);

  const data = view === "pending" ? pending : done;
  const loading = view === "pending" ? pendingLoading : doneLoading;
  const error = view === "pending" ? pendingError : doneError;
  const tabs = [
    { value: "pending", label: `대기 중 ${pending ? pending.items.length : ""}` },
    { value: "done", label: "처리한 내역" },
  ];

  return (
    <div className={styles.page}>
      <PageTitle
        title="승인함"
        sub={view === "pending" ? "내 역할에 도착한 요청만 표시합니다." : "내가 승인하거나 반려한 요청입니다."}
      />
      <Tabs items={tabs} value={view} onChange={(v) => setParams(v === "done" ? { view: "done" } : {})} />
      <ErrorBox error={error} />
      {loading && !data && <Loading />}

      {data && data.items.length === 0 &&
        (view === "pending" ? (
          <Empty
            title="대기 중인 승인이 없습니다"
            description="새 승인 요청이 도착하면 이곳에 표시됩니다."
            action={<Button to="/projects" variant="secondary">프로젝트 목록</Button>}
          />
        ) : (
          <Empty title="처리한 내역이 없습니다" description="승인하거나 반려한 요청이 이곳에 쌓입니다." />
        ))}

      {view === "pending" && data && data.items.length > 0 && (
        <>
          <TableCard>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>프로젝트</th>
                  <th>승인 지점 / 대상</th>
                  <th>내 역할</th>
                  <th>요청 시각</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td className={styles.strong}>{item.project_name}</td>
                    <td>{targetText(item)}</td>
                    <td>{ROLE_LABEL[item.role]}</td>
                    <td>{dayTimeText(item.requested_at)}</td>
                    <td>
                      <Link className={styles.go} to={targetPath(item)}>
                        검토하기 →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableCard>
          <p className={styles.note}>G1은 기획서에서, G2~G4는 대상 산출물을 확인한 뒤 처리합니다.</p>
        </>
      )}

      {view === "done" && data && data.items.length > 0 && (
        <TableCard>
          <table className={styles.doneTable}>
            <thead>
              <tr>
                <th>프로젝트 / 대상</th>
                <th>처리</th>
                <th>승인자 / 역할</th>
                <th>시각</th>
              </tr>
            </thead>
            {data.items.map((item) => (
              <tbody key={`${item.id}-${item.decided_at ?? ""}`} className={styles.doneRow}>
                <tr>
                  <td>
                    <Link to={targetPath(item)} className={styles.plainLink}>
                      {item.project_name}
                      <br />
                      {targetText(item)}
                    </Link>
                  </td>
                  <td className={item.decision === "reject" ? styles.reject : ""}>{decisionText(item)}</td>
                  <td>
                    {me.name} / {ROLE_LABEL[item.role]}
                  </td>
                  <td>{dayTimeText(item.decided_at)}</td>
                </tr>
                <tr>
                  <td colSpan={4} className={styles.comment}>
                    {item.comment ? `의견 · ${item.comment}` : "의견 없음"}
                  </td>
                </tr>
              </tbody>
            ))}
          </table>
        </TableCard>
      )}
    </div>
  );
}
