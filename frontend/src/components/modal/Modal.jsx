import { useEffect } from "react";
import styles from "./Modal.module.css";

// 대화상자 (피그마 "새 프로젝트 대화상자": 어두운 배경 위 흰 상자)
// Esc 키나 바깥을 누르면 onClose
export default function Modal({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className={styles.backdrop} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.box} role="dialog" aria-modal="true" aria-label={title}>
        <h2 className={styles.title}>{title}</h2>
        {children}
      </div>
    </div>
  );
}
