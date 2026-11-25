import { NavLink } from "react-router-dom";
import { rolesText } from "../../utils/labels";
import styles from "./Sidebar.module.css";

// 프로젝트 안의 메뉴 (예전에는 위쪽 탭이었다)
const PROJECT_MENU = [
  { path: "", label: "실행 상태" },
  { path: "/chat", label: "대화" },
  { path: "/planning", label: "기획서" },
  { path: "/runs", label: "실행 기록" },
  { path: "/changes", label: "변경 요청" },
  { path: "/members", label: "멤버와 역할" },
];

// 왼쪽 메뉴 (어두운 색)
// 위: 프로젝트 목록, 승인함(대기 건수)
// 가운데: 프로젝트 안이면 프로젝트 이름과 그 프로젝트의 메뉴
// 아래: 사용자 이름, 역할(프로젝트 안) 또는 아이디, 로그아웃
export default function Sidebar({ me, pending, project, onLogout }) {
  const linkClass = ({ isActive }) => `${styles.link} ${isActive ? styles.active : ""}`;
  const base = project ? `/projects/${project.id}` : "";

  return (
    <aside className={styles.sidebar}>
      <div className={styles.brand}>Orbit</div>
      <nav className={styles.nav}>
        <NavLink to="/projects" end className={linkClass}>
          프로젝트 목록
        </NavLink>
        <NavLink to="/approvals" className={linkClass}>
          승인함 <span className={styles.count}>{pending ?? 0}</span>
        </NavLink>
      </nav>

      {project && (
        <div className={styles.project}>
          <div className={styles.projectName}>{project.name}</div>
          <nav className={styles.nav}>
            {PROJECT_MENU.map((item) => (
              <NavLink key={item.path} to={`${base}${item.path}`} end={item.path === ""} className={linkClass}>
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      )}

      <div className={styles.user}>
        <div className={styles.userName}>{me.name}</div>
        <div className={styles.userSub}>{project && project.roles.length ? rolesText(project.roles) : me.login_id}</div>
        <button className={styles.logout} onClick={onLogout}>
          로그아웃
        </button>
      </div>
    </aside>
  );
}
