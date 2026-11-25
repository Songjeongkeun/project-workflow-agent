import { RUN_STATUS_LABEL } from "../../utils/labels";
import styles from "./StatusBadge.module.css";

// 실행 상태 배지 (진행 중, 승인 대기, 완료 ...)
export default function StatusBadge({ status }) {
  return <span className={`${styles.badge} ${styles[status]}`}>{RUN_STATUS_LABEL[status]}</span>;
}
