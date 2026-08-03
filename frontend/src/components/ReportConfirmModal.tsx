import { useEffect, useState } from 'react'
import { Alert, Button, Code, Group, List, Loader, Modal, Stack, Text } from '@mantine/core'
import { extractApiErrorMessage } from '../api/client'
import { getSelectionSummary } from '../api/results'
import type { SelectionSummaryResponse } from '../types'

interface ReportConfirmModalProps {
  opened: boolean
  jobId: number
  /** 확인 대상 `results.id` 목록 — 호출부에서 **참조가 안정적인 값**(useMemo)으로 넘길 것.
   * 매 렌더 새 배열이면 아래 useEffect가 요약 조회를 무한 반복한다. */
  ids: number[]
  /** 실제 생성(POST /generate-report) 진행 중 — 생성이 끝날 때까지 모달을 닫지 않는다. */
  generating: boolean
  /** 선택이 많을 때 소요 시간을 안내할 기준 건수(호출부의 LARGE_SELECTION_HINT). */
  largeSelectionThreshold: number
  onCancel: () => void
  onConfirm: () => void
}

/** §4-12-A 선택 항목 보고서 생성 **사전 확인** 모달 (2026-08-03).
 *
 * 생성 **결과**를 보여주는 `ReportResultModal`과는 별개다 — 이쪽은 생성 앞단이다.
 *
 * 왜 필요한가: "현재 필터 전체 선택"(§4-11-A)으로 클릭 한 번에 수천 건이 선택될 수 있고,
 * 선택은 탭을 넘나들며 **합집합으로 누적**된다(휴면·폐업 추정 / 매출액·총자산 조건 제외
 * 탭 포함). 그대로 생성하면 조건에 맞지 않는 회사의 감사 수임 제안서까지 로컬 폴더에
 * 수천 개가 만들어지고, 사용자는 그걸 우편으로 발송하게 된다. 되돌릴 수 없는 행동은
 * 아니지만(폴더를 지우면 된다) **"의도한 선택인지" 한 번 되묻는 값이 크다.**
 *
 * 경고가 있어도 **생성을 막지 않는다** — 사용자가 알고 진행할 수 있게 보여주기만 한다.
 * 반대로 요약 조회 자체가 실패하면 생성 버튼을 잠근다(집계 없이 맹목적으로 수천 건을
 * 만들지 않는다). 요약과 생성은 입력·에러 계약이 동일해, 요약이 200이면 생성도 같은
 * 이유로 거부되지 않는다. */
export default function ReportConfirmModal({
  opened,
  jobId,
  ids,
  generating,
  largeSelectionThreshold,
  onCancel,
  onConfirm,
}: ReportConfirmModalProps) {
  const [summary, setSummary] = useState<SelectionSummaryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!opened || ids.length === 0) return
    // 모달을 다시 열 때마다 새로 조회한다 — 선택은 그 사이에 바뀔 수 있다.
    let cancelled = false
    setLoading(true)
    setError(null)
    setSummary(null)
    getSelectionSummary(jobId, ids)
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch((err) => {
        if (cancelled) return
        setError(
          extractApiErrorMessage(
            err,
            '선택한 회사 정보를 확인하지 못했습니다. 프로그램이 실행 중인지 확인한 뒤 다시 시도하세요.',
          ),
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [opened, jobId, ids])

  // "의도한 선택인지 되물어야 할" 경고 항목들 — 서로 배타적이지 않으므로(한 회사가 매출액·
  // 총자산 조건에 동시에 걸릴 수 있다) 합계를 내지 않고 0보다 큰 항목만 한 줄씩 나열한다.
  // `no_disclosure`(감사보고서 없음)는 여기 넣지 않는다 — **검수 대상이 아니라 단순 참고
  // 사항**이라 아래에서 별도 안내 톤(파란 Alert)으로 분리해 보여준다.
  const warnings = summary
    ? [
        {
          key: 'stale',
          count: summary.stale_disclosure,
          text: '휴면·폐업 추정 회사',
          note: '최근 1년 이내 DART 공시가 없는 회사입니다.',
        },
        {
          key: 'revenue',
          count: summary.excluded_revenue,
          text: '매출액 조건 제외 회사',
          note: '원문에서 확인한 매출액이 검색 조건 범위를 벗어난 회사입니다.',
        },
        {
          key: 'assets',
          count: summary.excluded_assets,
          text: '총자산 조건 제외 회사',
          note: '원문에서 확인한 총자산이 검색 조건 범위를 벗어난 회사입니다.',
        },
        {
          key: 'failed',
          count: summary.failed,
          text: '파싱 실패(검수 미완료) 회사',
          // 백엔드가 2026-08-03에 세분화해, 이제 여기엔 **원문이 있는데 못 읽은** 건만
          // 들어간다("감사보고서 없음"은 아래 no_disclosure로 분리됐다).
          note: '감사보고서 원문 파싱에 실패해 검수가 필요한 회사입니다.',
        },
      ].filter((item) => item.count > 0)
    : []

  const canConfirm = !!summary && !loading && !error
  const isLargeSelection = !!summary && summary.total > largeSelectionThreshold
  // 재무 이력이 0건인 회사는 생성 자체가 건너뛰어진다 — 실제 산출물은 이 값이 상한이다
  // (이력이 있어도 전 연도가 결측이면 더 줄 수 있어 "최대"라고 쓴다).
  const maxGenerated = summary ? Math.max(summary.total - summary.no_history, 0) : 0

  return (
    <Modal
      opened={opened}
      // 생성이 진행되는 동안에는 닫지 않는다 — 중간에 닫히면 무엇이 돌고 있는지
      // 알 수 없고, 같은 선택으로 다시 눌러 중복 폴더가 생길 수 있다.
      onClose={() => {
        if (!generating) onCancel()
      }}
      closeOnClickOutside={!generating}
      closeOnEscape={!generating}
      withCloseButton={!generating}
      title="선택 항목 보고서를 생성할까요?"
      size="lg"
    >
      <Stack>
        {loading && (
          <Group gap="xs">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              선택한 회사 {ids.length.toLocaleString()}건을 확인하는 중입니다…
            </Text>
          </Group>
        )}

        {error && (
          <Alert color="red" title="선택한 회사를 확인하지 못했습니다">
            <Text size="sm">{error}</Text>
            <Text size="sm" mt="xs">
              무엇이 만들어질지 확인하지 못한 상태에서 파일을 생성하지 않도록, 생성 버튼을
              잠갔습니다. 창을 닫고 다시 시도하세요.
            </Text>
          </Alert>
        )}

        {summary && (
          <>
            <Text size="sm">
              선택한 <b>{summary.total.toLocaleString()}개 회사</b>의 감사 수임 제안서(HTML)를
              회사마다 1개씩 만듭니다.
            </Text>

            {warnings.length > 0 ? (
              <Alert color="yellow" title="선택에 아래 회사가 섞여 있습니다">
                <List size="sm" spacing={4}>
                  {warnings.map((item) => (
                    <List.Item key={item.key}>
                      <b>
                        {item.text} {item.count.toLocaleString()}건
                      </b>
                      이 포함돼 있습니다.{' '}
                      <Text span size="xs" c="dimmed">
                        {item.note}
                      </Text>
                    </List.Item>
                  ))}
                </List>
                <Text size="xs" c="dimmed" mt="xs">
                  한 회사가 여러 항목에 동시에 해당할 수 있어{' '}
                  <b>위 건수의 합은 선택 건수와 맞지 않을 수 있습니다</b>. 그대로 진행해도
                  되지만, 우편 발송 대상으로 의도한 선택인지 확인하세요.
                </Text>
              </Alert>
            ) : (
              <Alert color="green">
                선택한 회사 중 휴면·폐업 추정, 매출액·총자산 조건 제외, 파싱 실패에 해당하는
                회사는 없습니다.
              </Alert>
            )}

            {/* 감사보고서 없음은 경고가 아니다 — 사용자가 고칠 것도, 검수할 것도 없고
                "원문이 애초에 공시되지 않았다"는 사실 안내일 뿐이라 파란 안내 톤으로
                위 경고 블록과 분리해 보여준다. */}
            {summary.no_disclosure > 0 && (
              <Alert color="blue" variant="light" title="참고: 감사보고서 없음 회사가 포함돼 있습니다">
                <Text size="sm">
                  선택한 회사 중 <b>{summary.no_disclosure.toLocaleString()}건</b>은 공시된
                  감사보고서 원문이 아예 없어 재무 정보를 수집하지 못했습니다.{' '}
                  <b>검수 대상은 아니며</b>, 위 파싱 실패 건수와도 겹치지 않습니다.
                </Text>
                <Text size="xs" c="dimmed" mt={4}>
                  이런 회사는 대부분 재무 이력도 없어 보고서 생성에서 건너뛰어집니다.
                </Text>
              </Alert>
            )}

            <Stack gap={4}>
              <Text size="sm" fw={600}>
                저장 위치
              </Text>
              <Text size="sm">
                이 프로그램이 실행 중인 <b>PC의 보고서 폴더</b> 아래 <Code>YYYY-MM-DD</Code>
                (생성한 날짜) 폴더에 저장됩니다 — 브라우저 다운로드 폴더가 아닙니다. 같은 날
                다시 생성해도 <b>기존 폴더를 덮어쓰지 않고</b> <Code>_2</Code>,{' '}
                <Code>_3</Code> 폴더가 새로 만들어집니다.
              </Text>
              <Text size="xs" c="dimmed">
                우편 발송용 라벨 엑셀도 같은 폴더에 함께 만들어지며, 전체 경로는 생성이 끝난
                뒤 안내합니다.
              </Text>
            </Stack>

            {/* 실제 산출물 건수 안내 — 재무 이력이 0건인 회사는 백엔드가 생성 시
                건너뛴다. 프론트가 미리 걸러내지 않고(넘기는 id는 선택 전체 그대로)
                기대치만 미리 맞춰 준다. */}
            {summary.no_history > 0 ? (
              <Stack gap={2}>
                <Text size="sm">
                  선택한 {summary.total.toLocaleString()}건 중{' '}
                  <b>{summary.no_history.toLocaleString()}건</b>은 수집된 재무 이력이 없어
                  파일이 만들어지지 않습니다 — 실제로는{' '}
                  <b>최대 {maxGenerated.toLocaleString()}건</b>이 만들어집니다.
                </Text>
                <Text size="xs" c="dimmed">
                  만들어지지 않은 회사는 생성 결과에서 사유와 함께 안내합니다. 재무 이력이
                  있어도 연도별 값이 모두 비어 있으면 추가로 건너뛸 수 있어, 실제 건수는 위
                  값보다 적을 수 있습니다.
                </Text>
              </Stack>
            ) : (
              <Text size="xs" c="dimmed">
                재무 이력의 연도별 값이 모두 비어 있는 회사는 파일이 만들어지지 않을 수 있으며,
                그런 회사는 생성 결과에서 사유와 함께 안내합니다.
              </Text>
            )}

            {isLargeSelection && (
              <Text size="xs" c="dimmed">
                선택이 많아 <b>생성에 시간이 걸릴 수 있습니다</b> — 완료될 때까지 이 창을 닫지
                마세요.
              </Text>
            )}
          </>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={onCancel} disabled={generating}>
            취소
          </Button>
          <Button onClick={onConfirm} disabled={!canConfirm} loading={generating}>
            {summary
              ? summary.no_history > 0
                ? `최대 ${maxGenerated.toLocaleString()}건 생성`
                : `${summary.total.toLocaleString()}건 생성`
              : '생성'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
