import { useContext, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { AuthContext } from "../../context/AuthContext";
import Button from "../../components/button/Button";
import styles from "./LoginPage.module.css";

// 로그인
export default function LoginPage() {
  const { me, login } = useContext(AuthContext);
  const navigate = useNavigate();
  const [inputs, setInputs] = useState({ login_id: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (me) return <Navigate to="/projects" replace />;

  const handleChange = (e) => {
    const { name, value } = e.target;
    setInputs((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (inputs.login_id.trim().length === 0 || inputs.password.length === 0) return;
    setBusy(true);
    setError("");
    try {
      await login(inputs.login_id.trim(), inputs.password);
      navigate("/projects");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.brand}>Orbit</div>
      <form className={styles.form} onSubmit={handleSubmit}>
        <h1 className={styles.title}>로그인</h1>
        <p className={styles.sub}>팀 작업 공간에 로그인하세요.</p>
        <label className={styles.field}>
          아이디
          <input
            name="login_id"
            value={inputs.login_id}
            onChange={handleChange}
            placeholder="아이디를 입력하세요"
            autoFocus
            autoComplete="username"
          />
        </label>
        <label className={styles.field}>
          비밀번호
          <input
            type="password"
            name="password"
            value={inputs.password}
            onChange={handleChange}
            placeholder="비밀번호를 입력하세요"
            autoComplete="current-password"
          />
        </label>
        {/* 피그마 "로그인 오류": 입력 칸 아래 빨간 글씨 한 줄 */}
        {error && <p className={styles.error} role="alert">{error}</p>}
        <Button block disabled={busy || !inputs.login_id || !inputs.password}>{busy ? "로그인 중…" : "로그인"}</Button>
      </form>
    </div>
  );
}
