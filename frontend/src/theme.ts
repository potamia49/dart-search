import { createTheme, type MantineColorsTuple } from '@mantine/core'

/**
 * 금바다세무회계 브랜드 팔레트 (geumbada-report-style.css v2.0과 동일 톤)
 * - 메인 블루 #1F4E79 / 딥 네이비 #0A192F / 골드 #C9A227
 * 재무 보고서 서식과 앱 화면의 색·폰트를 하나로 맞추기 위한 테마.
 */
const geumbada: MantineColorsTuple = [
  '#f0f5fb', // 0
  '#dde8f4', // 1
  '#bcd0e8', // 2
  '#97b5db', // 3
  '#779ecf', // 4
  '#6390c9', // 5
  '#2c6597', // 6  (그라디언트 밝은 끝)
  '#1f4e79', // 7  ← primary (버튼 등)
  '#163a5a', // 8  (primary-dark)
  '#0a192f', // 9  (딥 네이비 — 헤더/풋터/표 헤더)
]

const gold: MantineColorsTuple = [
  '#fbf7e8',
  '#f4ecc7',
  '#ead79a',
  '#e0c26a',
  '#d7b345',
  '#d2a92f',
  '#c9a227', // 6  ← 브랜드 골드
  '#a9861c',
  '#8a6c14',
  '#6b530c',
]

export const theme = createTheme({
  primaryColor: 'geumbada',
  primaryShade: { light: 7, dark: 6 },
  colors: { geumbada, gold },
  defaultRadius: 'md',
  fontFamily:
    "'Noto Sans KR', 'Malgun Gothic', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  headings: {
    fontFamily: "'Noto Sans KR', 'Malgun Gothic', -apple-system, sans-serif",
    fontWeight: '700',
  },
})
