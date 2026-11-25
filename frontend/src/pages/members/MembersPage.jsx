import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { patchJson, postJson } from "../../api/client";
import useApi from "../../hooks/useApi";
import Button from "../../components/button/Button";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import Modal from "../../components/modal/Modal";
import PageTitle from "../../components/pagetitle/PageTitle";
import RolePicker from "../../components/rolepicker/RolePicker";
import TableCard from "../../components/tablecard/TableCard";
import { ROLE_LABEL, rolesText } from "../../utils/labels";
import styles from "./MembersPage.module.css";

const EMPTY = { login_id: "", slack_user_id: "", roles: [] };

// 멤버와 역할 (SCR-010)
// data = { members: [{ user_id, login_id, name, roles, slack_user_id, invited_by, is_owner }],
//          gates: [{ gate, role, owners }], gates_without_owner, can_edit }
export default function MembersPage() {
  const { project, reloadProject } = useOutletContext();
  const [, error, data, reload] = useApi(`/projects/${project.id}/members`);
  // 열린 대화상자: { mode: "invite" } 또는 { mode: "edit", member }
  const [dialog, setDialog] = useState(null);
  const [inputs, setInputs] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  if (!data) return <div className={styles.page}>{error ? <ErrorBox error={error} /> : <Loading />}</div>;

  const openInvite = () => {
    setInputs(EMPTY);
    setActionError("");
    setDialog({ mode: "invite" });
  };
  const openEdit = (member) => {
    setInputs({ login_id: member.login_id, slack_user_id: member.slack_user_id ?? "", roles: member.roles });
    setActionError("");
    setDialog({ mode: "edit", member });
  };
  const close = () => setDialog(null);

  // 초대 또는 역할 수정 저장
  const handleSubmit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setActionError("");
    try {
      if (dialog.mode === "invite") {
        await postJson(`/projects/${project.id}/members`, {
          login_id: inputs.login_id.trim(),
          roles: inputs.roles,
          slack_user_id: inputs.slack_user_id.trim() || null,
        });
      } else {
        await patchJson(`/projects/${project.id}/members/${dialog.member.user_id}`, {
          roles: inputs.roles,
          slack_user_id: inputs.slack_user_id.trim(),
        });
      }
      close();
      await reload();
      await reloadProject(); // 내 역할이 바뀌었을 수 있다 (왼쪽 메뉴, 위쪽 줄)
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const inviting = dialog?.mode === "invite";

  return (
    <div className={styles.page}>
      <PageTitle
        title="멤버와 역할"
        sub={`프로젝트 멤버 ${data.members.length}명  ·  프로젝트 생성자만 멤버와 역할을 수정할 수 있습니다.`}
        actions={data.can_edit && <Button onClick={openInvite} className={styles.wide}>멤버 초대</Button>}
      />

      <TableCard>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>이름</th>
              <th>역할</th>
              <th>Slack 사용자 ID</th>
              <th>초대한 사람</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.members.map((m) => (
              <tr key={m.user_id}>
                <td className={styles.strong}>
                  {m.name}
                  {m.is_owner && " · 생성자"}
                  <span className={styles.login}>{m.login_id}</span>
                </td>
                <td>{m.roles.length ? rolesText(m.roles) : "역할 없음"}</td>
                <td>{m.slack_user_id || "미등록"}</td>
                <td>{m.invited_by ?? "-"}</td>
                <td>
                  {data.can_edit ? (
                    <button type="button" className={styles.linkButton} onClick={() => openEdit(m)}>
                      역할 수정
                    </button>
                  ) : (
                    "보기 전용"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableCard>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>승인 담당</h2>
        <div className={styles.gates}>
          {data.gates.map((g) => (
            <div key={g.gate}>
              <p className={styles.gateName}>
                {g.gate} {ROLE_LABEL[g.role]}
              </p>
              <p className={g.owners.length ? styles.owners : styles.noOwner}>{g.owners.join(" · ") || "미지정"}</p>
            </div>
          ))}
        </div>
      </section>

      <p className={styles.note}>담당 역할을 가진 멤버 한 명이 승인하면 다음 단계로 진행합니다.</p>

      {data.gates_without_owner.length > 0 && (() => {
        // "G4 배포" 처럼 지점과 담당 역할. 여러 개면 한 상자에 모은다
        const names = data.gates_without_owner.map((gate) => {
          const g = data.gates.find((x) => x.gate === gate);
          return `${gate} ${ROLE_LABEL[g?.role] ?? ""}`;
        });
        const roles = names.map((n) => n.split(" ")[1]).join(", ");
        return (
          <section className={styles.warnBox}>
            <p className={styles.warnTitle}>{names.join(" · ")} 담당자가 없습니다</p>
            <p className={styles.warnText}>
              {data.can_edit
                ? `승인을 요청하려면 [역할 수정]에서 ${roles} 담당을 지정하세요.`
                : `프로젝트 생성자에게 ${roles} 담당 지정을 요청하세요.`}
            </p>
          </section>
        );
      })()}

      {dialog && (
        <Modal title={inviting ? "멤버 초대" : "역할 수정"} onClose={close}>
          <p className={styles.dialogSub}>
            {inviting
              ? "프로젝트에 참여할 팀원과 역할을 지정합니다."
              : `${dialog.member.name}의 역할과 Slack 사용자 ID를 바꿉니다.`}
          </p>
          <form className={styles.form} onSubmit={handleSubmit}>
            <label className={styles.field}>
              아이디
              <input
                value={inputs.login_id}
                onChange={(e) => setInputs((p) => ({ ...p, login_id: e.target.value }))}
                placeholder="팀원의 사내 아이디"
                readOnly={!inviting}
                className={!inviting ? styles.readOnly : ""}
                autoFocus={inviting}
              />
            </label>
            <div className={styles.field}>
              역할 · 여러 개 선택 가능
              <RolePicker value={inputs.roles} onChange={(roles) => setInputs((p) => ({ ...p, roles }))} />
            </div>
            <label className={styles.field}>
              Slack 사용자 ID
              <input
                value={inputs.slack_user_id}
                onChange={(e) => setInputs((p) => ({ ...p, slack_user_id: e.target.value }))}
                placeholder="U로 시작하는 사용자 ID"
              />
              <span className={styles.hint}>승인 요청 알림을 받을 계정입니다.</span>
            </label>
            {inviting && <p className={styles.hint}>이미 있는 계정만 초대할 수 있습니다. 계정은 운영 담당자가 서버에서 만듭니다.</p>}
            <ErrorBox error={actionError} />
            <div className={styles.buttons}>
              <Button type="button" variant="secondary" onClick={close}>
                취소
              </Button>
              <Button disabled={busy || (inviting && !inputs.login_id.trim())}>{inviting ? "초대" : "저장"}</Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
