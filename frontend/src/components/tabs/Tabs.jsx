import styles from "./Tabs.module.css";

// 밑줄 탭 (피그마: 선택한 탭은 보라 글씨와 2px 밑줄)
// items = [{ value, label }], value = 선택한 값, onChange(value)
export default function Tabs({ items, value, onChange }) {
  return (
    <div className={styles.tabs} role="tablist">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          aria-selected={item.value === value}
          className={`${styles.tab} ${item.value === value ? styles.active : ""}`}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
