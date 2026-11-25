import { Link } from "react-router-dom";
import styles from "./Button.module.css";

// 버튼. to를 주면 버튼 모양의 링크가 된다.
// variant: primary | secondary | ghost | danger, size: "sm"
export default function Button({ to, variant = "primary", size, block, className: extra, children, ...rest }) {
  const className = [styles.button, styles[variant], size === "sm" && styles.sm, block && styles.block, extra].filter(Boolean).join(" ");

  if (to) {
    return (
      <Link className={className} to={to}>
        {children}
      </Link>
    );
  }
  return (
    <button className={className} {...rest}>
      {children}
    </button>
  );
}
