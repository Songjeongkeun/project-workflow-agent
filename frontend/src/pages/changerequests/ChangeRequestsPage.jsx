import { useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { postJson } from "../../api/client";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import Empty from "../../components/empty/Empty";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import Modal from "../../components/modal/Modal";
import PageTitle from "../../components/pagetitle/PageTitle";
import TableCard from "../../components/tablecard/TableCard";
import { ROLE_LABEL, dayTimeText } from "../../utils/labels";
import styles from "./ChangeRequestsPage.module.css";

// 목록의 상태 글 (피그마 "분석 완료 · 재승인 대기", "영향 분석 중")
const STATUS = {
  analyzing: { text: "영향 분석 중", tone: "info" },
  awaiting_approval: { text: "분석 완료 · 재승인 대기", tone: "warn" },
  rerunning: { text: "재실행 중", tone: "info" },
  done: { text: "반영 완료", tone: "ok" },
  rejected: { text: "닫힘", tone: "muted" },
};
// 다시 승인할 지점의 "처리" 열
const reapprovalText = (r) => {
  if (r.status === "approved") return r.gate === "G1" ? "재확정 완료" : "재승인 완료";
  if (r.status === "rejected") return "반려";
  if (r.status === "void") return "무효";
  if (r.status === "waiting") return "후보 생성 후 요청";
  return r.gate === "G1" ? "재확정 필요" : "재승인 필요";
};
// 승인 지점의 담당 역할 (백엔드 members.GATE_ROLE과 같다)
const GATE_ROLE = { G1: "planner", G2: "designer", G3: "developer", G4: "deployer" };
const crName = (id) => `CR-${String(id).padStart(3, "0")}`;
// 날짜만 (피그마 "11월 12일")
const dayText = (iso) => dayTimeText(iso).split(" ").slice(0, 2).join(" ");

// 변경 요청 (SCR-009)
// list = { change_requests: [{ id, status, text, by, created_at }] }
// detail = { id, status, request: { by, text, created_at }, from_version, to_version, changed, impact, reapproval, carried_over }
export default function ChangeRequestsPage() {
  const { project, reloadProject } = useOutletContext();
  const { cid } = useParams();
  const navigate = useNavigate();
  const [, listError, list, reloadList] = useApi(`/projects/${project.id}/change-requests`, POLL_MS);
  // 주소에 번호가 없으면 첫 번째 요청을 보여 준다
  const selected = cid ? Number(cid) : list?.change_requests[0]?.id;
  const [, , detail] = useApi(selected ? `/change-requests/${selected}` : null, POLL_MS);
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  const closeDialog = () => {
    setWriting(false);
    setText("");
    setActionError("");
  };

  // 등록하면 새 요청의 상세로 이동
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (text.trim().length === 0) return;
    setBusy(true);
    setActionError("");
    try {
      const result = await postJson(`/projects/${project.id}/change-requests`, { text });
      closeDialog();
      await reloadList();
      await reloadProject();
      navigate(`/projects/${project.id}/changes/${result.id}`);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.page}>
      <PageTitle
        title="변경 요청"
        sub="기획 변경의 차이와 다시 확인할 범위를 검토합니다."
        actions={<Button onClick={() => setWriting(true)} className={styles.register}>변경 요청 등록</Button>}
      />
      {!list && (listError ? <ErrorBox error={listError} /> : <Loading />)}
      {list && list.change_requests.length === 0 && (
        <Empty title="변경 요청이 없습니다" description="확정한 기획을 바꾸려면 [변경 요청 등록]을 누르세요." />
      )}
      {list && list.change_requests.length > 0 && (
        <>
          <div className={styles.layout}>
            <nav className={styles.list}>
              {list.change_requests.map((c) => (
                <Link
                  key={c.id}
                  className={`${styles.item} ${c.id === selected ? styles.selected : ""}`}
                  to={`/projects/${project.id}/changes/${c.id}`}
                >
                  <span className={styles.itemName}>{crName(c.id)}</span>
                  <span className={styles.itemText}>{c.text}</span>
                  <span className={`${styles.itemStatus} ${styles[STATUS[c.status]?.tone]}`}>{STATUS[c.status]?.text ?? c.status}</span>
                  <span className={styles.itemMeta}>
                    {c.by} · {dayText(c.created_at)}
                  </span>
                </Link>
              ))}
            </nav>
            <section className={styles.detail}>{detail ? <Detail detail={detail} /> : <Loading />}</section>
          </div>
          {detail?.to_version && (
            <div className={styles.after}>
              <Button variant="secondary" to={`/projects/${project.id}/planning`}>
                기획서 v{detail.to_version} 검토
              </Button>
            </div>
          )}
        </>
      )}

      {writing && (
        <Modal title="변경 요청 등록" onClose={closeDialog}>
          <form className={styles.form} onSubmit={handleSubmit}>
            <label className={styles.field}>
              바꿀 내용
              <textarea
                rows={4}
                placeholder="예: 점검 시간과 문의처를 상단에서 한 번에 볼 수 있게 해주세요."
                value={text}
                onChange={(e) => setText(e.target.value)}
                autoFocus
              />
            </label>
            <p className={styles.help}>등록하면 영향 범위를 분석하고, 다시 승인할 지점을 알려 드립니다.</p>
            <ErrorBox error={actionError} />
            <div className={styles.buttons}>
              <Button type="button" variant="secondary" onClick={closeDialog}>
                취소
              </Button>
              <Button disabled={busy || !text.trim()}>{busy ? "등록 중…" : "등록"}</Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

function Detail({ detail }) {
  const analyzing = detail.status === "analyzing";
  const ch = detail.changed;
  const im = detail.impact;
  const join = (a) => (a && a.length ? a.join(", ") : "없음");

  // 다시 승인할 지점 + 그대로 유지되는 승인을 한 표로
  const rows = [
    ...detail.reapproval.map((r) => ({ gate: r.gate, role: r.role, action: reapprovalText(r), reason: "변경 영향을 받음", link: r.approval_id })),
    ...detail.carried_over.map((c) => ({ gate: c.gate, role: c.role ?? GATE_ROLE[c.gate], action: "기존 승인 유지", reason: c.reason })),
  ].sort((a, b) => a.gate.localeCompare(b.gate));

  return (
    <>
      <h2 className={styles.detailTitle}>{detail.request.text}</h2>
      <p className={styles.label}>원문 요청</p>
      <p className={styles.quote}>
        “{detail.request.text}”
        <span className={styles.by}>
          {detail.request.by} · {dayTimeText(detail.request.created_at)}
        </span>
      </p>

      <h3 className={styles.sectionTitle}>기능과 완료 조건 차이</h3>
      {analyzing ? (
        <p className={styles.text}>분석 중입니다.</p>
      ) : (
        <p className={styles.text}>
          {`수정  ${join(ch.modified)}`}
          <br />
          {`추가  ${join(ch.added)}  /  삭제  ${join(ch.removed)}  ·  기획서 v${detail.from_version} → ${detail.to_version ? `v${detail.to_version}` : "-"}`}
        </p>
      )}

      <h3 className={styles.sectionTitle}>영향받는 산출물</h3>
      {analyzing ? (
        <p className={styles.text}>분석 중입니다.</p>
      ) : (
        <p className={styles.text}>
          {`화면  ${join(im.screens)}`}
          <br />
          {`코드  ${join(im.code)}  ·  테스트  ${im.tests ?? 0}건`}
          <br />
          {`API·데이터  ${im.apis && im.apis.length ? im.apis.join(", ") : "영향 없음"}`}
        </p>
      )}

      <h3 className={styles.sectionTitle}>다시 승인할 지점</h3>
      {analyzing || rows.length === 0 ? (
        <p className={styles.text}>분석이 끝나면 다시 승인할 지점과 담당 역할이 표시됩니다.</p>
      ) : (
        <TableCard>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>지점 / 역할</th>
                <th>처리</th>
                <th>이유</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.gate}>
                  <td className={styles.strong}>
                    {r.gate} · {ROLE_LABEL[r.role] ?? "-"}
                  </td>
                  <td>{r.link ? <Link to={`/approvals/${r.link}`}>{r.action}</Link> : r.action}</td>
                  <td>{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableCard>
      )}
    </>
  );
}
