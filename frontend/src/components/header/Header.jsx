import { Link } from "react-router-dom";
import { rolesText } from "../../utils/labels";
import styles from "./Header.module.css";

// 위쪽 줄: 왼쪽은 지금 위치(경로), 오른쪽은 사용자 이름과 이 프로젝트에서의 역할
// 프로젝트 밖(프로젝트 목록, 승인함)에서는 "팀 작업 공간"과 이름만 보인다.
export default function Header({ me, project }) {
  return (
    <header className={styles.header}>
      <div className={styles.path}>
        {project ? (
          <>
            <Link to="/projects">프로젝트</Link>
            <span className={styles.sep}>/</span>
            <span>{project.name}</span>
          </>
        ) : (
          "팀 작업 공간"
        )}
      </div>
      <div className={styles.user}>
        {me.name}
        {project && project.roles.length > 0 && <span className={styles.dot}>·</span>}
        {project && project.roles.length > 0 && rolesText(project.roles)}
      </div>
    </header>
  );
}
