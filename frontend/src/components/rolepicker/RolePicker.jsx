import { ROLE_LABEL, ROLES } from "../../utils/labels";
import styles from "./RolePicker.module.css";

// 역할마다 맡는 승인 지점 (피그마 "G1 기획서·완료 조건")
const ROLE_GATE = {
  planner: "G1 기획서·완료 조건",
  designer: "G2 화면",
  developer: "G3 범위 밖 변경",
  deployer: "G4 배포 대상·버전",
};

// 역할 고르기 (2×2 칸, 여러 개 선택 가능). value는 고른 역할 배열
export default function RolePicker({ value, onChange, disabled }) {
  const handleToggle = (role, checked) => {
    onChange(checked ? [...value, role] : value.filter((r) => r !== role));
  };

  return (
    <div className={styles.roles}>
      {ROLES.map((role) => {
        const checked = value.includes(role);
        return (
          <label key={role} className={`${styles.tile} ${checked ? styles.checked : ""}`}>
            <input
              type="checkbox"
              checked={checked}
              disabled={disabled}
              onChange={(e) => handleToggle(role, e.target.checked)}
            />
            <span className={styles.name}>{ROLE_LABEL[role]}</span>
            <span className={styles.gate}>{ROLE_GATE[role]}</span>
          </label>
        );
      })}
    </div>
  );
}
