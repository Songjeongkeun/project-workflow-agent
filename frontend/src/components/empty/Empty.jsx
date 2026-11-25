import styles from "./Empty.module.css";

// 비었을 때 보여 주는 상자
// title을 주면 피그마의 큰 빈 화면(제목 24px, 설명, 버튼)으로 보인다.
export default function Empty({ title, description, action, children }) {
  if (title) {
    return (
      <div className={styles.large}>
        <p className={styles.title}>{title}</p>
        {description && <p className={styles.description}>{description}</p>}
        {action && <div className={styles.action}>{action}</div>}
      </div>
    );
  }
  return <div className={styles.empty}>{children}</div>;
}
