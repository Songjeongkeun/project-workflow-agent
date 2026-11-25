// 서버와 주고받는 공통 함수. 모든 요청은 /api로 시작하고 JSON을 쓴다.
// 실패하면 서버의 { detail } 메시지로 Error를 던진다. (error.status에 HTTP 상태를 담는다)

export const API_URL = "/api";
export const TOKEN_KEY = "orbit.token";

// 401이 오면 로그아웃시키는 함수 (AuthProvider가 등록한다)
let onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

// 실패 응답에서 보여 줄 메시지를 고른다 (422는 detail이 목록이라 공통 문구)
function errorMessage(data, status) {
  if (typeof data?.detail === "string") return data.detail;
  if (status === 422 || Array.isArray(data?.detail)) return "입력값을 확인해 주세요.";
  return `요청이 실패했습니다 (${status}).`;
}

async function request(method, path, body) {
  const headers = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인해 주세요.");
  }

  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login" && onUnauthorized) onUnauthorized();
    const error = new Error(errorMessage(data, response.status));
    error.status = response.status;
    throw error;
  }
  return data;
}

export const getJson = (path) => request("GET", path);
export const postJson = (path, body = {}) => request("POST", path, body);
export const patchJson = (path, body) => request("PATCH", path, body);
