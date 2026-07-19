import { useEffect, useState } from 'react'
import { Anchor, Badge, Button, Drawer, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import type { FinancialSnapshotResponse, ResultResponse } from '../types'
import { getResultHistory } from '../api/results'
import { BASIC_COLUMNS, CASH_FLOW_COLUMNS, FINANCIAL_COLUMNS, formatCell, formatNumber, formatPercent } from '../util/resultColumns'
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

// [항목 키, 표시 라벨, 포맷 함수] — financial_snapshots 필드셋 (results의 _cur/_prv와 동일 항목, 접미어 없음).
const HISTORY_ROWS: [keyof FinancialSnapshotResponse, string, (v: unknown) => string][] = [
  ['current_assets', '유동자산', formatNumber],
  ['noncurrent_assets', '비유동자산', formatNumber],
  ['total_assets', '자산총계', formatNumber],
  ['current_liab', '유동부채', formatNumber],
  ['noncurrent_liab', '비유동부채', formatNumber],
  ['total_liab', '부채총계', formatNumber],
  ['total_equity', '자본총계', formatNumber],
  ['revenue', '매출액', formatNumber],
  ['cogs', '매출원가', formatNumber],
  ['gross_margin', '매출총이익율', formatPercent],
  ['sga', '판매비와관리비', formatNumber],
  ['operating_income', '영업이익', formatNumber],
  ['net_income', '당기순이익', formatNumber],
  // 현금흐름표 4항목 (§4-8)
  ['cf_operating', '영업활동현금흐름', formatNumber],
  ['cf_investing', '투자활동현금흐름', formatNumber],
  ['cf_financing', '재무활동현금흐름', formatNumber],
  ['cf_ending_cash', '기말의현금', formatNumber],
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
          {HISTORY_ROWS.map(([key, label, format]) => (
            <Table.Tr key={key}>
              <Table.Td>{label}</Table.Td>
              {history.map((snap) => (
                <Table.Td key={snap.fiscal_year}>{format(snap[key])}</Table.Td>
              ))}
            </Table.Tr>
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
              {[...Array(FINANCIAL_COLUMNS.length / 2).keys()].map((i) => {
                const curCol = FINANCIAL_COLUMNS[i * 2]
                const prvCol = FINANCIAL_COLUMNS[i * 2 + 1]
                const label = curCol.label.replace('_당기', '')
                return (
                  <Table.Tr key={curCol.key}>
                    <Table.Td>{label}</Table.Td>
                    <Table.Td>{formatCell(curCol, result)}</Table.Td>
                    <Table.Td>{formatCell(prvCol, result)}</Table.Td>
                  </Table.Tr>
                )
              })}
              <Table.Tr>
                <Table.Td colSpan={3}>
                  <Text span fw={600} size="sm" c="dimmed">현금흐름표</Text>
                </Table.Td>
              </Table.Tr>
              {[...Array(CASH_FLOW_COLUMNS.length / 2).keys()].map((i) => {
                const curCol = CASH_FLOW_COLUMNS[i * 2]
                const prvCol = CASH_FLOW_COLUMNS[i * 2 + 1]
                const label = curCol.label.replace('_당기', '')
                return (
                  <Table.Tr key={curCol.key}>
                    <Table.Td>{label}</Table.Td>
                    <Table.Td>{formatCell(curCol, result)}</Table.Td>
                    <Table.Td>{formatCell(prvCol, result)}</Table.Td>
                  </Table.Tr>
                )
              })}
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
