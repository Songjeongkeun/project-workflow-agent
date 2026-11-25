import styles from "./ErrorBox.module.css";

// 오류 메시지. Error 객체나 문자열을 받는다. 없으면 아무것도 그리지 않는다.
export default function ErrorBox({ error }) {
  if (!error) return null;
  return (
    <div className={styles.error} role="alert">
      {typeof error === "string" ? error : error.message}
    </div>
  );
}
