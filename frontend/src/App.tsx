import { AppShell, Group } from '@mantine/core'
import { NavLink as RouterNavLink, Navigate, Route, Routes } from 'react-router-dom'
import SearchPage from './pages/SearchPage'
import JobsPage from './pages/JobsPage'
import ResultPage from './pages/ResultPage'

const HEADER_GRADIENT = 'linear-gradient(155deg, #0A192F 0%, #1F4E79 55%, #2C6597 100%)'

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? 'brand-nav active' : 'brand-nav'
}

function App() {
  return (
    <AppShell header={{ height: 60 }} footer={{ height: 34 }} padding="md">
      <AppShell.Header
        style={{
          background: HEADER_GRADIENT,
          border: 'none',
          boxShadow: '0 2px 12px rgba(10,25,47,0.4)',
        }}
      >
        <Group h="100%" px="lg" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <img
              src="/geumbada-logo.png"
              alt="금바다세무회계"
              style={{ height: 28, width: 'auto' }}
            />
            <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
              <span
                style={{
                  fontSize: 15,
                  fontWeight: 700,
                  color: '#C9A227',
                  letterSpacing: '-0.3px',
                }}
              >
                금바다세무회계
              </span>
              <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.78)' }}>
                DART 비상장 외감법인 재무 검색기
              </span>
            </div>
          </Group>
          <Group gap={4} wrap="nowrap">
            <RouterNavLink to="/search" className={navClass}>
              검색
            </RouterNavLink>
            <RouterNavLink to="/jobs" className={navClass}>
              작업 현황
            </RouterNavLink>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Navigate to="/search" replace />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:id/results" element={<ResultPage />} />
          <Route path="*" element={<Navigate to="/search" replace />} />
        </Routes>
      </AppShell.Main>

      <AppShell.Footer
        style={{ background: '#0A192F', border: 'none', display: 'flex', alignItems: 'center' }}
      >
        <Group h="100%" px="lg" justify="space-between" w="100%" wrap="nowrap">
          <span style={{ color: 'rgba(255,255,255,0.7)', fontSize: 11, fontWeight: 600 }}>
            금바다세무회계 · 대표 공인회계사 윤일근
          </span>
          <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: 10 }}>© 2026 금바다세무회계</span>
        </Group>
      </AppShell.Footer>
    </AppShell>
  )
}

export default App
