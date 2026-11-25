import { createContext, useEffect, useState } from "react";
import { getJson, postJson, setUnauthorizedHandler, TOKEN_KEY } from "../api/client";

export const AuthContext = createContext();

// 로그인 상태: 토큰은 localStorage에 두고, 내 정보(me)는 /me에서 받아 온다.
// me = { id, login_id, name, slack_user_id, projects: [...], pending_approvals }
export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [me, setMe] = useState(null);
  const [loading, setLoading] = useState(true);

  // 토큰이 바뀌면 localStorage에도 반영
  useEffect(() => {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  }, [token]);

  // 토큰이 생기면 내 정보를 불러온다
  useEffect(() => {
    if (!token) {
      setMe(null);
      setLoading(false);
      return;
    }
    const loadMe = async () => {
      try {
        setMe(await getJson("/me"));
      } catch {
        setMe(null);
      } finally {
        setLoading(false);
      }
    };
    loadMe();
  }, [token]);

  // 서버가 401을 주면 로그아웃
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null);
      setMe(null);
    });
  }, []);

  const login = async (loginId, password) => {
    const data = await postJson("/auth/login", { login_id: loginId, password });
    setLoading(true);
    setToken(data.token);
  };

  const logout = async () => {
    try {
      await postJson("/auth/logout");
    } catch {
      // 서버 오류여도 화면에서는 로그아웃한다
    } finally {
      setToken(null);
      setMe(null);
    }
  };

  // 승인 처리 등으로 내 정보가 바뀌었을 때 다시 불러온다
  const refresh = async () => {
    if (!localStorage.getItem(TOKEN_KEY)) {
      setMe(null);
      return;
    }
    try {
      setMe(await getJson("/me"));
    } catch {
      setMe(null);
    }
  };

  return (
    <AuthContext.Provider value={{ me, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}
