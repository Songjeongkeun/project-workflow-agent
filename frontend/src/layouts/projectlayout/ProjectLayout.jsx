import { Outlet, useOutletContext } from "react-router-dom";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import styles from "./ProjectLayout.module.css";

// 프로젝트 안의 화면들. 프로젝트는 AppLayout이 불러 둔 것을 받는다.
// (프로젝트 메뉴는 왼쪽 메뉴에, 프로젝트 이름은 위쪽 줄의 경로에 있다)
// 하위 화면은 useOutletContext()로 { project, reloadProject }를 받는다.
// project = { id, name, is_owner, has_ui, roles, run_id, status, current_step }
export default function ProjectLayout() {
  const { project, projectError, reloadProject } = useOutletContext();

  if (projectError && !project) return <div className={styles.message}><ErrorBox error={projectError} /></div>;
  if (!project) return <div className={styles.message}><Loading /></div>;

  return <Outlet context={{ project, reloadProject }} />;
}
