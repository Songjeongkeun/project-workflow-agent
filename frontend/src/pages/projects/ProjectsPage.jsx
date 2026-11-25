import { useContext, useState } from "react";
import { useNavigate } from "react-router-dom";
import { postJson } from "../../api/client";
import { AuthContext } from "../../context/AuthContext";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import Empty from "../../components/empty/Empty";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import Modal from "../../components/modal/Modal";
import PageTitle from "../../components/pagetitle/PageTitle";
import ProjectCard from "../../components/projectcard/ProjectCard";
import styles from "./ProjectsPage.module.css";

// 프로젝트 목록 (SCR-002). data = { projects: [...] }
export default function ProjectsPage() {
  const [loading, error, data] = useApi("/projects", POLL_MS);
  const { refresh } = useContext(AuthContext);
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  const closeDialog = () => {
    setCreating(false);
    setName("");
    setActionError("");
  };

  // 새 프로젝트를 만들면 대화 화면으로
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (name.trim().length === 0) return;
    setBusy(true);
    setActionError("");
    try {
      const result = await postJson("/projects", { name: name.trim() });
      await refresh();
      navigate(`/projects/${result.id}/chat`);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const newButton = <Button onClick={() => setCreating(true)}>새 프로젝트</Button>;
  const empty = data && data.projects.length === 0;

  return (
    <div className={styles.page}>
      <PageTitle title="프로젝트 목록" sub="내가 참여한 프로젝트" actions={!empty && newButton} />
      <ErrorBox error={error} />
      {loading && !data && <Loading />}
      {empty && <Empty title="아직 참여한 프로젝트가 없습니다" description="새 프로젝트를 만들어 시작하세요." action={newButton} />}
      <div className={styles.list}>
        {data?.projects.map((project) => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>

      {creating && (
        <Modal title="새 프로젝트" onClose={closeDialog}>
          <form className={styles.form} onSubmit={handleSubmit}>
            <label className={styles.field}>
              프로젝트 이름
              <input
                placeholder="프로젝트 이름을 입력하세요"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </label>
            <p className={styles.help}>생성 후 대화에서 만들 서비스를 설명합니다.</p>
            <ErrorBox error={actionError} />
            <div className={styles.buttons}>
              <Button type="button" variant="secondary" onClick={closeDialog}>
                취소
              </Button>
              <Button disabled={busy || !name.trim()}>{busy ? "만드는 중…" : "프로젝트 만들기"}</Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
