import { Link } from "react-router-dom";
import StatusBadge from "../statusbadge/StatusBadge";
import { AGENT_LABEL, agoText } from "../../utils/labels";
import styles from "./ProjectCard.module.css";

// 프로젝트 목록의 카드 하나 (피그마 SCR-002: 폭 전체, 높이 172px)
// project = { id, name, roles, current_step, status, updated_at, my_pending_approvals }
export default function ProjectCard({ project }) {
  const pending = project.my_pending_approvals;
  return (
    <Link to={`/projects/${project.id}`} className={styles.card}>
      <div className={styles.top}>
        <h2 className={styles.name}>{project.name}</h2>
        <StatusBadge status={project.status} />
      </div>
      <div className={styles.step}>{project.current_step ? AGENT_LABEL[project.current_step] : "시작 전"}</div>
      <div className={styles.bottom}>
        <span className={`${styles.pending} ${pending > 0 ? styles.hasPending : ""}`}>내 승인 대기 {pending}건</span>
        <span className={styles.updated}>마지막 갱신&nbsp;&nbsp;{agoText(project.updated_at)}</span>
      </div>
    </Link>
  );
}
