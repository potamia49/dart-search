import axios from 'axios'

// 모든 요청은 /api 프리픽스로 나가고, dev 서버(vite.config.ts)의 proxy 설정이
// http://localhost:8000 (FastAPI 백엔드)로 전달한다. 프론트는 DART API 키를
// 전혀 다루지 않는다 — 모든 DART 호출은 백엔드가 대신 수행한다 (CLAUDE.md 원칙).
export const apiClient = axios.create({
  baseURL: '/api',
  // 배열 파라미터를 **같은 이름의 반복 쿼리**(`?f=A&f=B`)로 직렬화한다 — axios 기본값은
  // `?f[]=A&f[]=B`라 FastAPI의 `list[str]` 파라미터가 **하나도 안 잡힌다**(모르는
  // 파라미터로 조용히 무시돼 "필터를 걸었는데 결과가 그대로"가 된다).
  // §4-14의 값 목록 필터(`induty_name_in` 등)가 이 형식을 쓴다 — 값이 업종명 같은
  // 자유 텍스트라 콤마 구분을 쓸 수 없어(값 자체에 쉼표가 흔함) 백엔드가 반복
  // 파라미터로 계약했다. 배열이 아닌 파라미터에는 영향이 없다.
  paramsSerializer: { indexes: null },
})

/** FastAPI 에러 응답(`{"detail": ...}`)에서 사용자에게 보여줄 문구를 뽑는다.
 *
 * 백엔드는 400/404/507 등에서 **이미 한국어로 다듬은 문장**을 `detail` 문자열로 주므로
 * 그대로 노출하는 편이 프론트에서 상태코드별 문구를 새로 지어내는 것보다 정확하다.
 * 다만 422(pydantic 검증 실패)만은 `detail`이 문자열이 아니라 오류 객체 배열이라
 * 그대로 찍으면 `[object Object]`가 되므로 `msg`만 모아 합친다. 그 밖의 경우(네트워크
 * 단절 등 응답 자체가 없는 경우 포함)는 호출부가 준 `fallback`을 쓴다. */
export function extractApiErrorMessage(error: unknown, fallback: string): string {
  if (!axios.isAxiosError(error)) return fallback
  const detail: unknown = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown } | null)?.msg)
      .filter((msg): msg is string => typeof msg === 'string' && msg.trim().length > 0)
    if (messages.length > 0) return messages.join(' / ')
  }
  return fallback
}
