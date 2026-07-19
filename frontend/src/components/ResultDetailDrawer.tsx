import { Fragment, useEffect, useState } from 'react'
import { Anchor, Badge, Button, Drawer, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import type { FinancialSnapshotResponse, ResultResponse } from '../types'
import { getResultHistory } from '../api/results'
import { BASIC_COLUMNS, FINANCIAL_GROUPS, formatCell } from '../util/resultColumns'
import DocumentSectionModal, { type DocumentSectionTarget } from './DocumentSectionModal'

interface ResultDetailDrawerProps {
  jobId: number
  result: ResultResponse | null
  onClose: () => void
}

const DART_ORIGINAL_DOC_BASE = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='

// §4-8 원문 보기 버튼 4개.
const DOC_SECTION_BUTTONS: { section: DocumentSectionTarget['section']; label: string }[] = [
  { section: 'bs', label: '재무상태표' },
  { section: 'is', label: '손익계산서' },
  { section: 'cf', label: '현금흐름표' },
  { section: 'notes', label: '주석' },
]

/** STEP 7(최근 N년 재무이력) 표 — Drawer가 열릴 때(선택된 result가 바뀔 때)만 lazy fetch한다. */
function FinancialHistorySection({
  jobId,
  resultId,
  onOpenDocument,
}: {
  jobId: number
  resultId: number
  onOpenDocument: (target: DocumentSectionTarget) => void
}) {
  const [history, setHistory] = useState<FinancialSnapshotResponse[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setHistory(null)
    setError(null)
    setLoading(true)
    getResultHistory(jobId, resultId)
      .then((data) => {
        if (!cancelled) setHistory(data)
      })
      .catch(() => {
        if (!cancelled) setError('재무 이력을 불러오지 못했습니다.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [jobId, resultId])

  if (loading) return <Loader size="sm" />
  if (error) return <Text c="red" size="sm">{error}</Text>
  if (!history || history.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        조회된 재무 이력이 없습니다. (매출액 조건으로 제외되었거나 감사보고서를 찾지 못한 경우
        이력이 수집되지 않을 수 있습니다.)
      </Text>
    )
  }

  return (
    <Table.ScrollContainer minWidth={400}>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>항목</Table.Th>
            {history.map((snap) => (
              <Table.Th key={snap.fiscal_year}>
                <Stack gap={2}>
                  <Text span fw={600} size="sm">{snap.fiscal_year}</Text>
                  {snap.rcept_no && (
                    <Anchor
                      component="button"
                      type="button"
                      size="xs"
                      onClick={() =>
                        onOpenDocument({
                          section: 'bs',
                          rceptNo: snap.rcept_no ?? undefined,
                          yearLabel: snap.fiscal_year,
                        })
                      }
                    >
                      원문
                    </Anchor>
                  )}
                </Stack>
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {FINANCIAL_GROUPS.map((group) => (
            <Fragment key={group.section}>
              <Table.Tr>
                <Table.Td colSpan={1 + history.length}>
                  <Text span fw={600} size="sm" c="dimmed">{group.title}</Text>
                </Table.Td>
              </Table.Tr>
              {group.items.map((item) => (
                <Table.Tr key={item.snapKey}>
                  <Table.Td>{item.label}</Table.Td>
                  {history.map((snap) => (
                    <Table.Td key={snap.fiscal_year}>{item.format(snap[item.snapKey])}</Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Fragment>
          ))}
          <Table.Tr>
            <Table.Td>파싱상태</Table.Td>
            {history.map((snap) => (
              <Table.Td key={snap.fiscal_year}>{snap.parse_status ?? '-'}</Table.Td>
            ))}
          </Table.Tr>
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  )
}

/** 행 클릭 시 당기·전기 전 항목 + DART 원문 링크를 보여주는 상세 패널 (상세개발계획.md §7-3). */
export default function ResultDetailDrawer({ jobId, result, onClose }: ResultDetailDrawerProps) {
  const [docTarget, setDocTarget] = useState<DocumentSectionTarget | null>(null)

  // Drawer가 닫히거나 다른 result로 바뀌면 열려 있던 원문 모달도 닫는다.
  useEffect(() => {
    setDocTarget(null)
  }, [result?.id])

  return (
    <Drawer
      opened={result !== null}
      onClose={onClose}
      position="right"
      size="xl"
      title={result ? `${result.corp_name ?? '(회사명 없음)'} 상세` : ''}
    >
      {result && (
        <Stack>
          <Group>
            {result.parse_status && <Badge>{result.parse_status}</Badge>}
            {result.excluded_by_revenue === 1 && <Badge color="orange">매출액 제외</Badge>}
            {result.excluded_by_assets === 1 && <Badge color="orange">총자산 제외</Badge>}
            {result.rcept_no && (
              <Anchor
                href={`${DART_ORIGINAL_DOC_BASE}${result.rcept_no}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                DART 원문 보기
              </Anchor>
            )}
          </Group>

          {result.parse_note && (
            <Text size="sm" c="dimmed">
              비고: {result.parse_note}
            </Text>
          )}

          <Title order={5}>기본정보</Title>
          <SimpleGrid cols={2} spacing="xs">
            {BASIC_COLUMNS.map((col) => (
              <Text key={col.key} size="sm">
                <Text span fw={600}>
                  {col.label}:
                </Text>{' '}
                {formatCell(col, result)}
              </Text>
            ))}
          </SimpleGrid>

          <Title order={5}>재무정보 (당기 · 전기)</Title>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>항목</Table.Th>
                <Table.Th>당기</Table.Th>
                <Table.Th>전기</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {FINANCIAL_GROUPS.map((group) => (
                <Fragment key={group.section}>
                  <Table.Tr>
                    <Table.Td colSpan={3}>
                      <Text span fw={600} size="sm" c="dimmed">{group.title}</Text>
                    </Table.Td>
                  </Table.Tr>
                  {group.items.map((item) => (
                    <Table.Tr key={item.curKey}>
                      <Table.Td>{item.label}</Table.Td>
                      <Table.Td>{item.format(result[item.curKey])}</Table.Td>
                      <Table.Td>{item.format(result[item.prvKey])}</Table.Td>
                    </Table.Tr>
                  ))}
                </Fragment>
              ))}
            </Table.Tbody>
          </Table>

          <Title order={5}>원문 보기</Title>
          <Group gap="xs">
            {DOC_SECTION_BUTTONS.map((btn) => (
              <Button
                key={btn.section}
                size="xs"
                variant="light"
                onClick={() => setDocTarget({ section: btn.section })}
              >
                {btn.label}
              </Button>
            ))}
          </Group>

          <Title order={5}>재무 이력 (최근 N년)</Title>
          <FinancialHistorySection
            jobId={jobId}
            resultId={result.id}
            onOpenDocument={setDocTarget}
          />

          <DocumentSectionModal
            jobId={jobId}
            resultId={result.id}
            corpName={result.corp_name}
            target={docTarget}
            onClose={() => setDocTarget(null)}
          />
        </Stack>
      )}
    </Drawer>
  )
}
