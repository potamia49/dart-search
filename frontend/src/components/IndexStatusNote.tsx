import { Alert, Stack, Text } from '@mantine/core'
import type { DartIndexStatus, FscFinancialStatus } from '../types'

/**
 * Phase 1이 쓰는 두 전역 인덱스의 갱신 상태를 함께 보여준다(M8 5단계).
 *
 * 구 `FscIndexStatusNote`(fsc_corp_index 1종, 2026-07-21 관련 코드 전체 삭제)를
 * 대체한다 — M8에서 후보를
 * 찾는 정본이 `dart_corp_index`로 바뀌었고, 매출액/총자산 참고값이
 * `fsc_financial_stat`이라는 별도 인덱스로 분리됐기 때문이다.
 *
 * 두 인덱스는 **심각도가 다르다**. 이 차이를 화면에서 뭉뚱그리지 않는 것이
 * 이 컴포넌트의 요점이다:
 *
 * - `dart_corp_index`가 비면 후보 확정이 즉시 실패한다 → 빨간 경고.
 * - `fsc_financial_stat`이 비어도 결과는 정확하다(§4-10-C — 이 값으로 후보를
 *   제외하지 않는다). 후보 목록의 참고 표시가 비고 Phase 2 처리 순서가
 *   무작위가 될 뿐이다 → 노란 안내.
 *
 * 백엔드는 두 인덱스 모두 오래됐다고 자동 갱신하지 않으므로(로그에만 남긴다)
 * 여기가 사용자가 갱신 필요를 알아차리는 유일한 지점이다.
 */
export default function IndexStatusNote({
  dartIndex,
  financialStat,
}: {
  dartIndex: DartIndexStatus | null
  financialStat: FscFinancialStatus | null
}) {
  if (!dartIndex && !financialStat) return null

  return (
    <Stack gap="xs">
      {dartIndex && <DartIndexLine status={dartIndex} />}
      {financialStat && <FinancialStatLine status={financialStat} />}
    </Stack>
  )
}

function formatDate(value: string | null): string {
  return value ? value.replace('T', ' ') : '기록 없음'
}

function DartIndexLine({ status }: { status: DartIndexStatus }) {
  if (status.row_count === 0) {
    return (
      <Alert color="red" variant="light">
        DART 기업개황 인덱스가 비어 있습니다 — 후보 확정이 즉시 실패합니다.
        관리자가 먼저 "DART 기업개황 인덱스 갱신"을 실행해야 합니다.
      </Alert>
    )
  }

  if (status.crawl_in_progress) {
    return (
      <Alert color="yellow" variant="light">
        DART 기업개황 인덱스 갱신 진행 중입니다 (현재 {status.row_count.toLocaleString()}개사
        {status.checkpoint_industry ? `, 업종 ${status.checkpoint_industry}까지` : ''}).
        지금 검색해도 되지만 아직 인덱스에 없는 회사는 후보에서 빠집니다.
      </Alert>
    )
  }

  if (status.reconcile_pending) {
    // 크롤은 끝났지만 동명 회사 교정이 밀린 상태. 이 경우 같은 이름의 회사끼리
    // 주소·업종이 교차돼 있을 수 있어(M8 6단계 실측: 위험군 42.5% 불일치)
    // 실제로 그 지역 회사가 후보에서 조용히 빠진다 — 검색 전에 알려야 한다.
    return (
      <Alert color="yellow" variant="light">
        DART 기업개황 인덱스: {status.row_count.toLocaleString()}개사 (마지막 갱신{' '}
        {formatDate(status.last_completed_at)}) — 동명 회사 교정이 아직 완료되지 않았습니다.
        같은 이름의 회사끼리 주소·업종이 뒤바뀐 채 남아 있을 수 있어, 해당 지역 회사가 후보에서
        빠질 수 있습니다. 관리자가 "동명 회사 교정"을 실행해야 합니다.
      </Alert>
    )
  }

  return (
    <Text size="sm" c="dimmed">
      DART 기업개황 인덱스: {status.row_count.toLocaleString()}개사 (마지막 갱신{' '}
      {formatDate(status.last_completed_at)} · 동명 회사 교정{' '}
      {formatDate(status.last_reconciled_at)})
    </Text>
  )
}

function FinancialStatLine({ status }: { status: FscFinancialStatus }) {
  if (status.row_count === 0) {
    return (
      <Alert color="yellow" variant="light">
        매출액·총자산 참고값(금융위 요약재무) 인덱스가 비어 있습니다. 검색과 결과
        정확도에는 영향이 없지만(최종 판정은 항상 DART 원문 기준입니다), 후보
        목록에 참고용 매출액·총자산이 표시되지 않고 재무정보 수집 순서도 무작위가
        됩니다.
      </Alert>
    )
  }

  if (status.crawl_in_progress) {
    return (
      <Text size="sm" c="dimmed">
        매출액·총자산 참고값 인덱스 갱신 진행 중입니다 (참고 표시용, 검색은 그대로 가능).
      </Text>
    )
  }

  return (
    <Text size="sm" c="dimmed">
      매출액·총자산 참고값(금융위 요약재무): {status.row_count.toLocaleString()}건 ·{' '}
      {status.years.length > 0 ? `${status.years.join('/')}년 기준` : '기준연도 없음'} (마지막
      갱신 {formatDate(status.last_completed_at)})
    </Text>
  )
}
