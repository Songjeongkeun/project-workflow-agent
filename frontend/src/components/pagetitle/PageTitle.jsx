import styles from "./PageTitle.module.css";

// 화면 맨 위 제목, 설명, 오른쪽 버튼
export default function PageTitle({ title, sub, actions }) {
  return (
    <div className={styles.pageTitle}>
      <div>
        <h1>{title}</h1>
        {sub && <p className={`muted ${styles.sub}`}>{sub}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}
