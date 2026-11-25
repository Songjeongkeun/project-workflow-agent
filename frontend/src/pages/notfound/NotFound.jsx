import { Link } from "react-router-dom";
import styles from "./NotFound.module.css";

// 없는 주소로 들어왔을 때
export default function NotFound() {
  return (
    <div className={styles.center}>
      <div className={styles.box}>
        <h1>페이지를 찾을 수 없습니다</h1>
        <p className="muted">주소가 바뀌었거나 없는 화면입니다.</p>
        <Link to="/projects">프로젝트 목록으로 가기</Link>
      </div>
    </div>
  );
}
