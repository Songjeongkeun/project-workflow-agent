import { useContext } from "react";
import { Navigate, Outlet, useMatch, useNavigate } from "react-router-dom";
import { AuthContext } from "../../context/AuthContext";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Sidebar from "../../components/sidebar/Sidebar";
import Header from "../../components/header/Header";
import Loading from "../../components/loading/Loading";
import styles from "./AppLayout.module.css";

// 로그인한 화면의 공통 구성: 왼쪽 메뉴 + (위쪽 줄 + 본문)
// 프로젝트 안(/projects/:pid/...)이면 프로젝트를 여기서 한 번 불러와
// 왼쪽 메뉴(프로젝트 메뉴), 위쪽 줄(경로), 프로젝트 화면(ProjectLayout)이 같이 쓴다.
export default function AppLayout() {
  const { me, loading, logout } = useContext(AuthContext);
  const navigate = useNavigate();
  // 승인함 대기 건수를 3초마다 갱신
  const [, , liveMe] = useApi(me ? "/me" : null, POLL_MS);

  const match = useMatch("/projects/:pid/*");
  const pid = match?.params.pid;
  const [, projectError, project, reloadProject] = useApi(me && pid ? `/projects/${pid}` : null, POLL_MS);

  if (loading) {
    return (
      <div className={styles.center}>
        <Loading />
      </div>
    );
  }
  if (!me) return <Navigate to="/login" replace />;

  const pending = liveMe?.pending_approvals ?? me.pending_approvals;

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className={styles.shell}>
      <Sidebar me={me} pending={pending} project={pid ? project : null} onLogout={handleLogout} />
      <div className={styles.body}>
        <Header me={me} project={pid ? project : null} />
        <main className={styles.main}>
          <Outlet context={{ project, projectError, reloadProject }} />
        </main>
      </div>
    </div>
  );
}
