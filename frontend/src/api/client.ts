import axios from 'axios'

// 모든 요청은 /api 프리픽스로 나가고, dev 서버(vite.config.ts)의 proxy 설정이
// http://localhost:8000 (FastAPI 백엔드)로 전달한다. 프론트는 DART API 키를
// 전혀 다루지 않는다 — 모든 DART 호출은 백엔드가 대신 수행한다 (CLAUDE.md 원칙).
export const apiClient = axios.create({
  baseURL: '/api',
})
