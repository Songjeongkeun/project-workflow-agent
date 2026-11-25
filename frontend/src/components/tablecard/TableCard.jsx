import styles from "./TableCard.module.css";

// 표가 든 흰 상자 (피그마 "데이터 표": 머리글 12px 굵게, 행 구분선, 바깥 여백 없음)
export default function TableCard({ children }) {
  return <div className={styles.card}>{children}</div>;
}
