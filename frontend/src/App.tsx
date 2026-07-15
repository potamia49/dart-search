import { AppShell, Group, NavLink, Title } from '@mantine/core'
import { NavLink as RouterNavLink, Navigate, Route, Routes } from 'react-router-dom'
import SearchPage from './pages/SearchPage'
import JobsPage from './pages/JobsPage'
import ResultPage from './pages/ResultPage'

function App() {
  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={3}>DART 정보 검색기</Title>
          <Group gap="xs">
            <NavLink
              component={RouterNavLink}
              to="/search"
              label="검색"
              style={{ width: 'auto', borderRadius: 4 }}
            />
            <NavLink
              component={RouterNavLink}
              to="/jobs"
              label="작업 현황"
              style={{ width: 'auto', borderRadius: 4 }}
            />
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
    </AppShell>
  )
}

export default App
