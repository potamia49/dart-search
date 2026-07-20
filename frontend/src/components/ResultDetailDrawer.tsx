import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import {
  Anchor,
  Badge,
  Button,
  Drawer,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import type {
  AccountDetailResponse,
  FinancialSnapshotResponse,
  ResultResponse,
} from '../types'
import { getAccountDetail, getResultHistory } from '../api/results'
import {
  BASIC_COLUMNS,
  FINANCIAL_GROUPS,
  formatCell,
  formatNumber,
  type FinancialItem,
} from '../util/resultColumns'
import DocumentSectionModal, { type DocumentSectionTarget } from './DocumentSectionModal'

interface ResultDetailDrawerProps {
  jobId: number
  result: ResultResponse | null
  onClose: () => void
}

const DART_ORIGINAL_DOC_BASE = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='

/** 세부계정 상세를 펼칠 수 있는 항목인지 — 현금흐름표(영업/투자/재무활동)도
 * 재무상태표·손익계산서와 동일한 ALEVEL 계층 구조라 펼칠 수 있다(2026-07-20).
 * "기말의현금"처럼 그 자체가 총계인 항목은 펼쳐도 세부 내역이 없다는 안내만
 * 나온다(자산총계 등과 동일한 패턴 — canExpand로는 구분하지 않는다). */
function canExpand(): boolean {
  return true
}

/** 연도 간 계정 매칭 키 — 각주 번호("(주석3)")나 항목 번호("1.")는 연도마다
 * 달라질 수 있어 제거한 뒤 비교한다. 표시는 원문 라벨을 그대로 쓴다. */
function accountKey(label: string): string {
  return label
    .replace(/\([^)]*\)/g, '')
    .replace(/^[0-9IVXivx]+[.)]\s*/, '')
    .replace(/\s+/g, '')
}

/** rcept_no별 계정 상세 캐시 — 같은 원문을 여러 번 내려받지 않는다.
 * 상세는 로컬 문서 캐시만 읽으므로 DART 쿼터를 쓰지 않는다. */
function useAccountDetails(jobId: number, resultId: number | null) {
  const [byRcept, setByRcept] = useState<Record<string, AccountDetailResponse>>({})
  const [loading, setLoading] = useState(false)
  const pending = useRef<Set<string>>(new Set())

  // 다른 회사를 열면 캐시를 비운다(원문이 회사마다 다르므로 재사용 불가).
  useEffect(() => {
    setByRcept({})
    pending.current.clear()
  }, [resultId])

  const ensure = useCallback(
    async (rceptNos: (string | null | undefined)[]) => {
      if (resultId === null) return
      const targets = Array.from(
        new Set(rceptNos.filter((r): r is string => Boolean(r))),
      ).filter((r) => !pending.current.has(r))
      if (targets.length === 0) return
      targets.forEach((r) => pending.current.add(r))
      setLoading(true)
      try {
        const loaded = await Promise.all(
          targets.map(async (r) => {
            try {
              return [r, await getAccountDetail(jobId, resultId, r)] as const
            } catch {
              return null // 원문 캐시 없음 등 — 해당 연도만 빈 상세로 둔다.
            }
          }),
        )
        const entries = loaded.filter((e): e is readonly [string, AccountDetailResponse] => e !== null)
        if (entries.length > 0) {
          setByRcept((prev) => ({ ...prev, ...Object.fromEntries(entries) }))
        }
      } finally {
        setLoading(false)
      }
    },
    [jobId, resultId],
  )

  return { byRcept, ensure, loading }
}

/** 항목 라벨 셀 — 펼치기 가능한 항목이면 ▸/▾ 토글 버튼으로 렌더한다. */
function ItemLabelCell({
  item,
  expandable,
  expanded,
  onToggle,
}: {
  item: FinancialItem
  expandable: boolean
  expanded: boolean
  onToggle: () => void
}) {
  if (!expandable) return <>{item.label}</>
  return (
    <UnstyledButton onClick={onToggle} style={{ fontSize: 'inherit' }}>
      <Group gap={4} wrap="nowrap">
        <Text span c="dimmed" size="xs">
          {expanded ? '▾' : '▸'}
        </Text>
        <Text span style={{ textDecoration: 'underline dotted' }}>
          {item.label}
        </Text>
      </Group>
    </UnstyledButton>
  )
}

/** 펼쳐진 세부계정이 없을 때 안내 행. */
function EmptyDetailRow({ colSpan }: { colSpan: number }) {
  return (
    <Table.Tr>
      <Table.Td colSpan={colSpan}>
        <Text size="xs" c="dimmed" pl="md">
          이 항목의 세부 내역이 원문에 없습니다.
        </Text>
      </Table.Td>
    </Table.Tr>
  )
}

/** 재무이력 표에서 특정 대분류의 세부계정 행 목록을 만든다.
 * 연도마다 원문(rcept_no)이 다르고 계정 구성도 달라질 수 있어, 최신 연도부터
 * 훑으며 계정 키의 합집합을 만들고 표시 라벨은 최신 연도의 것을 쓴다. */
function buildHistoryChildRows(
  field: string,
  history: FinancialSnapshotResponse[],
  byRcept: Record<string, AccountDetailResponse>,
): { key: string; label: string; level: number }[] {
  const seen = new Map<string, { key: string; label: string; level: number }>()
  for (const snap of [...history].reverse()) {
    const detail = snap.rcept_no ? byRcept[snap.rcept_no] : undefined
    for (const row of detail?.accounts[field] ?? []) {
      const key = accountKey(row.label)
      if (!seen.has(key)) seen.set(key, { key, label: row.label, level: row.level })
    }
  }
  return Array.from(seen.values())
}

/** 해당 연도 열에 쓸 값 — 원문의 당기 결산연도와 같으면 당기값, 아니면 전기값이다
 * (한 원문이 당기·전기 2개 연도를 담고 있어 연도별로 열을 골라야 한다). */
function historyCellValue(
  snap: FinancialSnapshotResponse,
  field: string,
  key: string,
  byRcept: Record<string, AccountDetailResponse>,
): number | null {
  const detail = snap.rcept_no ? byRcept[snap.rcept_no] : undefined
  if (!detail) return null
  const row = (detail.accounts[field] ?? []).find((r) => accountKey(r.label) === key)
  if (!row) return null
  return detail.fiscal_year_cur === snap.fiscal_year ? row.cur : row.prv
}

/** STEP 7(최근 N년 재무이력) 표 — Drawer가 열릴 때(선택된 result가 바뀔 때)만 lazy fetch한다. */
function FinancialHistorySection({
  jobId,
  resultId,
  details,
  onOpenDocument,
}: {
  jobId: number
  resultId: number
  details: ReturnType<typeof useAccountDetails>
  onOpenDocument: (target: DocumentSectionTarget) => void
}) {
  const [history, setHistory] = useState<FinancialSnapshotResponse[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const { byRcept, ensure, loading: detailLoading } = details

  useEffect(() => {
    let cancelled = false
    setHistory(null)
    setError(null)
    setExpanded(new Set())
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

  // 감사의견 행은 펼치기 여부와 무관하게 항상 보여야 하므로, 연도별로 세부계정을
  // 펼치기 전에도 미리 확보해 둔다(로컬 문서 캐시만 읽어 쿼터 0건).
  useEffect(() => {
    if (history && history.length > 0) {
      void ensure(history.map((s) => s.rcept_no))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ensure/history 참조가 매 렌더 바뀌어도 rcept_no 집합이 같으면 재실행할 필요가 없다.
  }, [history])

  const toggle = (field: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(field)) next.delete(field)
      else {
        next.add(field)
        // 연도마다 원문이 다르므로 이력에 등장하는 모든 공시를 확보한다.
        void ensure((history ?? []).map((s) => s.rcept_no))
      }
      return next
    })
  }

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

  const colSpan = 1 + history.length
  const detailsReady = history.some((s) => s.rcept_no && byRcept[s.rcept_no])

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
                    <Tooltip
                      multiline
                      w={260}
                      label={
                        snap.from_current_period
                          ? `당기가 ${snap.fiscal_year}년(전기 ${Number(snap.fiscal_year) - 1}년)인 감사보고서 원문을 엽니다.`
                          : `${snap.fiscal_year}년을 당기로 하는 감사보고서를 찾지 못해, 이 연도를 전기로 담고 있는 ${Number(snap.fiscal_year) + 1}년 보고서를 엽니다.`
                      }
                    >
                      <Button
                        size="compact-xs"
                        variant="light"
                        onClick={() =>
                          onOpenDocument({
                            section: 'bs',
                            rceptNo: snap.rcept_no ?? undefined,
                            yearLabel: snap.fiscal_year,
                            fromCurrentPeriod: Boolean(snap.from_current_period),
                          })
                        }
                      >
                        원문 보기
                      </Button>
                    </Tooltip>
                  )}
                  {snap.rcept_no && !snap.from_current_period && (
                    <Text size="10px" c="dimmed">전기 기준</Text>
                  )}
                </Stack>
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          <Table.Tr>
            <Table.Td fw={600}>감사의견</Table.Td>
            {history.map((snap) => {
              const detail = snap.rcept_no ? byRcept[snap.rcept_no] : undefined
              return (
                <Table.Td key={snap.fiscal_year}>
                  {detail ? (detail.audit_opinion ?? '-') : detailLoading ? <Loader size="xs" /> : '-'}
                </Table.Td>
              )
            })}
          </Table.Tr>
          {FINANCIAL_GROUPS.map((group) => (
            <Fragment key={group.section}>
              <Table.Tr>
                <Table.Td colSpan={colSpan}>
                  <Text span fw={600} size="sm" c="dimmed">{group.title}</Text>
                </Table.Td>
              </Table.Tr>
              {group.items.map((item) => {
                const field = item.snapKey as string
                const expandable = canExpand()
                const isOpen = expanded.has(field)
                const childRows = isOpen ? buildHistoryChildRows(field, history, byRcept) : []
                return (
                  <Fragment key={item.snapKey}>
                    <Table.Tr>
                      <Table.Td>
                        <ItemLabelCell
                          item={item}
                          expandable={expandable}
                          expanded={isOpen}
                          onToggle={() => toggle(field)}
                        />
                      </Table.Td>
                      {history.map((snap) => (
                        <Table.Td key={snap.fiscal_year}>{item.format(snap[item.snapKey])}</Table.Td>
                      ))}
                    </Table.Tr>
                    {isOpen && detailLoading && !detailsReady && (
                      <Table.Tr>
                        <Table.Td colSpan={colSpan}><Loader size="xs" /></Table.Td>
                      </Table.Tr>
                    )}
                    {isOpen && !detailLoading && childRows.length === 0 && (
                      <EmptyDetailRow colSpan={colSpan} />
                    )}
                    {isOpen &&
                      childRows.map((child) => (
                        <Table.Tr key={`${field}-${child.key}`}>
                          <Table.Td style={{ paddingLeft: 12 + child.level * 14 }}>
                            <Text span size="xs" c="dimmed">{child.label}</Text>
                          </Table.Td>
                          {history.map((snap) => (
                            <Table.Td key={snap.fiscal_year}>
                              <Text span size="xs" c="dimmed">
                                {formatNumber(historyCellValue(snap, field, child.key, byRcept))}
                              </Text>
                            </Table.Td>
                          ))}
                        </Table.Tr>
                      ))}
                  </Fragment>
                )
              })}
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
  const details = useAccountDetails(jobId, result?.id ?? null)

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

          <Title order={5}>재무 이력 (최근 N년)</Title>
          <Text size="xs" c="dimmed">
            가장 오른쪽 열이 최신 연도(당기)입니다. 밑줄 친 항목을 클릭하면 원문의 세부계정을
            펼쳐 볼 수 있습니다.
          </Text>
          <FinancialHistorySection
            jobId={jobId}
            resultId={result.id}
            details={details}
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
