import { useEffect, useRef, useState } from "react";
import { getJson } from "../api/client";

// 진행 상황은 3초마다 다시 조회한다
export const POLL_MS = 3000;

// GET 요청을 불러온다. intervalMs를 주면 그 간격으로 다시 조회한다.
// 다시 조회할 때는 이전 데이터를 유지해 화면이 깜빡이지 않게 한다.
// 사용: const [loading, error, data, reload] = useApi(path, POLL_MS)
export default function useApi(path, intervalMs) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const currentPath = useRef(path);

  // 응답이 늦게 와도 지금 보고 있는 주소의 결과만 반영한다
  const fetchData = async (target) => {
    try {
      const result = await getJson(target);
      if (currentPath.current !== target) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (currentPath.current === target) setError(e);
    } finally {
      if (currentPath.current === target) setLoading(false);
    }
  };

  useEffect(() => {
    currentPath.current = path;
    setLoading(true);
    setError(null);
    setData(null);
    if (!path) return;

    fetchData(path);
    if (!intervalMs) return;
    const timer = setInterval(() => fetchData(path), intervalMs);
    return () => clearInterval(timer);
  }, [path, intervalMs]);

  const reload = async () => {
    if (path) await fetchData(path);
  };

  return [loading, error, data, reload];
}
