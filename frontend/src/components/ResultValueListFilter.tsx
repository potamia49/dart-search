import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  CloseButton,
  Divider,
  Group,
  Loader,
  ScrollArea,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { extractApiErrorMessage } from '../api/client'
import { listDistinctValues } from '../api/results'
import type { DistinctValueField, DistinctValuesResponse } from '../types'
import {
  BLANK_VALUE_TOKEN,
  MAX_VALUE_FILTER_ITEMS,
  formatFilterValue,
} from '../util/resultFilters'
import type { FilterMode, ValueListFilterState } from '../util/resultFilters'

/**
 * §4-14 값 목록 컬럼 필터(업종/감사인/감사의견)의 팝오버 본문 — 엑셀 컬럼 필터의
 * "값 체크박스 목록"에 해당한다.
 *
 * 목록은 팝오버가 **열릴 때** `GET /results/distinct-values`로 가져온다(Mantine
 * Popover는 닫혀 있으면 드롭다운 내용을 마운트하지 않으므로, 이 컴포넌트의 마운트가
 * 곧 "열림"이다). 값 검색은 서버에서 하고(고유값이 700종을 넘는 컬럼이 있다) 목록 조회
 * 검색어와 같은 400ms 디바운스를 태운다.
 *
 * **이 조회에는 화면에 걸려 있는 다른 필터를 절대 섞지 않는다** — 백엔드도 Job 전체를
 * 기준으로 집계한다. 섞으면 지금 화면에서 빠져 있는 값이 목록에서도 사라져 "안 보이는
 * 값을 다시 켜기"가 불가능해진다(한 번 잘못 거르면 되돌릴 수 없는 상태가 된다).
 *
 * UI 껍데기(깔때기 아이콘 + 팝오버)는 `ResultColumnFilters.tsx`에 있고, 상태 정의·쿼리
 * 변환은 `util/resultFilters.ts`에 있다. 파일이 갈린 이유는 §4-13-C와 같다
 * (fast-refresh 규칙 + 이 컴포넌트만 데이터 조회를 하기 때문).
 */

const VALUE_SEARCH_DEBOUNCE_MS = 400

/** 팝오버 안에서 스크롤될 목록 높이 — 값이 수백 개라 팝오버 자체가 길어지면
 * 모드 전환·버튼이 화면 밖으로 밀린다. */
const LIST_HEIGHT = 220

export function ValueListFilterBody({
  jobId,
  field,
  label,
  filter,
  onChange,
  hint,
}: {
  jobId: number
  field: DistinctValueField
  label: string
  filter: ValueListFilterState
  onChange: (next: ValueListFilterState) => void
  /** 그 컬럼에서만 필요한 주의사항(예: 감사인은 목록 셀 표기와 필터 대상이 다르다). */
  hint?: string
}) {
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, VALUE_SEARCH_DEBOUNCE_MS)
  const [data, setData] = useState<DistinctValuesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // "다시 시도"용 — 값이 바뀌면 아래 useEffect가 다시 돈다.
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    listDistinctValues(jobId, field, { q: debouncedQuery.trim() })
      .then((res) => {
        // 검색어를 빠르게 고치면 이전 요청이 나중에 도착할 수 있다 — 늦게 온 응답이
        // 최신 목록을 덮어쓰지 않도록 버린다.
        if (!cancelled) setData(res)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            extractApiErrorMessage(
              err,
              '값 목록을 불러오지 못했습니다. 프로그램이 실행 중인지 확인한 뒤 다시 시도하세요.',
            ),
          )
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [jobId, field, debouncedQuery, retryToken])

  const selected = new Set(filter.values)
  const blankToken = data?.blank_token ?? BLANK_VALUE_TOKEN
  const listedValues = new Set(data?.values.map((item) => item.value) ?? [])
  if (data && data.blank_count > 0) listedValues.add(blankToken)
  // 고른 값이 지금 목록에 없을 수 있다(값 검색으로 좁혔거나 목록이 잘렸거나).
  // 그대로 두면 **체크는 걸려 있는데 해제할 방법이 없는** 상태가 되므로 위에 따로 그린다.
  const hiddenSelected = filter.values.filter((value) => !listedValues.has(value))
  const atLimit = filter.values.length >= MAX_VALUE_FILTER_ITEMS

  function setMode(mode: FilterMode) {
    onChange({ ...filter, mode })
  }

  function toggleValue(value: string, checked: boolean) {
    const next = checked
      ? [...filter.values, value]
      : filter.values.filter((v) => v !== value)
    onChange({ ...filter, values: next })
  }

  /** 지금 목록(값 검색 결과)에 보이는 값을 한 번에 고른다 — "회계법인"으로 좁힌 뒤
   * 전부 고르는 식의 쓰임새다. 목록 밖의 값은 건드리지 않는다(그래서 "이 목록"이다). */
  function selectListed() {
    const candidates = [...listedValues].filter((value) => !selected.has(value))
    onChange({ ...filter, values: [...filter.values, ...candidates] })
  }

  const listedUnselectedCount = [...listedValues].filter((value) => !selected.has(value)).length
  const canSelectListed =
    listedUnselectedCount > 0 && filter.values.length + listedUnselectedCount <= MAX_VALUE_FILTER_ITEMS
  /** 고를 값은 남아 있는데 상한 때문에 못 누르는 경우 — 버튼이 왜 회색인지 설명이 필요하다
   * (업종처럼 고유값이 700종을 넘는 컬럼은 필터를 열자마자 이 상태다). "이미 다 골라서"
   * 비활성인 경우와는 이유가 달라 구분한다. */
  const selectListedOverLimit =
    listedUnselectedCount > 0 && filter.values.length + listedUnselectedCount > MAX_VALUE_FILTER_ITEMS

  return (
    <Stack gap="xs">
      {/* 포함/제외는 이 필터의 의미를 통째로 뒤집으므로 목록보다 위에 둔다. */}
      <SegmentedControl
        size="xs"
        fullWidth
        value={filter.mode}
        onChange={(value) => setMode(value as FilterMode)}
        data={[
          { value: 'INCLUDE', label: '고른 값만 보기' },
          { value: 'EXCLUDE', label: '고른 값 빼기' },
        ]}
      />

      <TextInput
        size="xs"
        placeholder={`${label} 값 검색`}
        value={query}
        onChange={(event) => setQuery(event.currentTarget.value)}
        rightSection={
          // 검색어를 고치는 동안에는(이미 목록이 떠 있는 상태) 옛 목록이 잠시 그대로
          // 남으므로, 다시 불러오는 중임을 입력칸에서 알린다.
          loading && data ? (
            <Loader size="xs" />
          ) : query ? (
            <CloseButton size="sm" onClick={() => setQuery('')} />
          ) : null
        }
      />

      {error && (
        <Alert color="red" p="xs">
          <Text size="xs">{error}</Text>
          <Button size="compact-xs" variant="subtle" mt={4} onClick={() => setRetryToken((n) => n + 1)}>
            다시 시도
          </Button>
        </Alert>
      )}

      {loading && !data && (
        <Group gap="xs" py="xs">
          <Loader size="xs" />
          <Text size="xs" c="dimmed">
            값 목록을 불러오는 중…
          </Text>
        </Group>
      )}

      {/* 값 목록은 **이 작업 전체** 기준이라, 지금 화면에 걸린 다른 필터로 안 보이는 값도
          그대로 고를 수 있다(그렇지 않으면 한 번 잘못 거른 뒤 되돌릴 수 없다).
          큰 건수 숫자를 보기 **전에** 읽혀야 오해가 없어 목록 바로 위에 둔다. */}
      {data && (
        <Text size="xs" c="dimmed">
          목록의 건수는 <b>이 작업 전체</b> 기준입니다(다른 필터를 반영하지 않습니다) — 전체{' '}
          {data.total_rows.toLocaleString()}건.
        </Text>
      )}

      {data && (
        <ScrollArea.Autosize mah={LIST_HEIGHT} type="auto">
          <Stack gap={6} pr="xs">
            {/* 고른 값 중 지금 목록에 없는 것 — 체크 해제 경로를 잃지 않게 맨 위에 둔다. */}
            {hiddenSelected.length > 0 && (
              <>
                {hiddenSelected.map((value) => (
                  <Checkbox
                    key={`hidden-${value}`}
                    size="xs"
                    checked
                    label={
                      <Text size="xs">
                        {formatFilterValue(value)}{' '}
                        <Text span size="xs" c="dimmed">
                          (현재 목록에 없음)
                        </Text>
                      </Text>
                    }
                    onChange={(event) => toggleValue(value, event.currentTarget.checked)}
                  />
                ))}
                <Divider />
              </>
            )}

            {data.values.map((item) => {
              const checked = selected.has(item.value)
              return (
                <Checkbox
                  key={item.value}
                  size="xs"
                  checked={checked}
                  disabled={!checked && atLimit}
                  label={
                    <Text size="xs">
                      {item.value}{' '}
                      <Text span size="xs" c="dimmed">
                        ({item.count.toLocaleString()})
                      </Text>
                    </Text>
                  }
                  onChange={(event) => toggleValue(item.value, event.currentTarget.checked)}
                />
              )
            })}

            {/* "값 없음"(NULL/빈 문자열)은 값이 아니라 상태라 목록 맨 아래 고정 항목으로
                둔다 — 감사인·감사의견은 실측 43~45%가 여기 해당해, 이 항목이 없으면
                목록 필터를 켜는 순간 절반이 조용히 사라진다. */}
            {data.blank_count > 0 && (
              <>
                <Divider />
                <Checkbox
                  size="xs"
                  checked={selected.has(blankToken)}
                  disabled={!selected.has(blankToken) && atLimit}
                  label={
                    <Text size="xs">
                      (값 없음){' '}
                      <Text span size="xs" c="dimmed">
                        ({data.blank_count.toLocaleString()})
                      </Text>
                    </Text>
                  }
                  onChange={(event) => toggleValue(blankToken, event.currentTarget.checked)}
                />
              </>
            )}

            {/* 바로 위에 "(값 없음) N건" 체크박스가 떠 있는데 "표시할 값이 없습니다"를
                함께 찍으면 서로 모순된다(Phase 2 진행 중이라 감사인·감사의견이 대부분
                NULL인 흔한 상황) — 값이 채워진 회사가 없다는 뜻으로 바꿔 쓴다. */}
            {data.values.length === 0 && (
              <Text size="xs" c="dimmed">
                {query.trim()
                  ? '검색과 일치하는 값이 없습니다.'
                  : data.blank_count > 0
                    ? '값이 채워진 회사가 없습니다.'
                    : '표시할 값이 없습니다.'}
              </Text>
            )}
          </Stack>
        </ScrollArea.Autosize>
      )}

      {/* 잘린 목록에서 "이 목록 전체 선택"을 누르면 화면에 안 보이는 값이 조용히 빠진다 —
          그 상태를 반드시 알려야 한다(백엔드 `truncated` 계약의 취지).
          `c="orange"`(orange.6 #fd7e14)는 흰 팝오버 위에서 대비가 2.57:1뿐이라 이 경고가
          사실상 안 보였다 — 위 에러와 같은 Alert(light)로 바꿔 본문을 기본 텍스트색(#000)
          으로 찍는다(orange.1 #ffe8cc 배경 위 17.7:1). */}
      {data?.truncated && (
        <Alert color="orange" p="xs">
          <Text size="xs">
            값이 많아 {data.values.length.toLocaleString()}개만 보여주고 있습니다(전체{' '}
            {data.total_distinct.toLocaleString()}종) — 위 검색으로 좁혀서 고르세요.
          </Text>
        </Alert>
      )}

      {atLimit && (
        <Text size="xs" c="red">
          한 번에 고를 수 있는 값은 {MAX_VALUE_FILTER_ITEMS.toLocaleString()}개까지입니다.
        </Text>
      )}

      <Text size="xs" c="dimmed">
        {filter.mode === 'INCLUDE'
          ? '고른 값과 정확히 일치하는 회사만 남깁니다. 값이 없는 회사는 "(값 없음)"을 함께 골라야 남습니다.'
          : '고른 값과 정확히 일치하는 회사만 뺍니다. 값이 없는 회사는 "(값 없음)"을 고르지 않는 한 그대로 남습니다.'}
      </Text>

      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}

      <Divider />
      <Group gap="xs">
        {/* 상한 때문에 못 누르는 동안에는 이유를 붙인다. 비활성 버튼에는 포인터 이벤트가
            안 가서 Tooltip이 뜨지 않으므로, Mantine 권장대로 `disabled` 대신
            `data-disabled`로 같은 비활성 스타일만 입힌다(`false`를 넘기면 속성이
            그대로 붙어 버려 `undefined`로 지운다). */}
        <Tooltip
          label={`한 번에 ${MAX_VALUE_FILTER_ITEMS.toLocaleString()}개까지만 고를 수 있어요 — 위 검색으로 목록을 좁힌 뒤 눌러 주세요.`}
          disabled={!selectListedOverLimit}
          multiline
          w={240}
          withArrow
          events={{ hover: true, focus: true, touch: true }}
        >
          <Button
            size="compact-xs"
            variant="subtle"
            data-disabled={canSelectListed ? undefined : true}
            aria-disabled={!canSelectListed}
            onClick={(event) => {
              if (!canSelectListed) {
                event.preventDefault()
                return
              }
              selectListed()
            }}
          >
            이 목록 전체 선택
          </Button>
        </Tooltip>
        <Button
          size="compact-xs"
          variant="subtle"
          color="gray"
          disabled={filter.values.length === 0}
          onClick={() => onChange({ ...filter, values: [] })}
        >
          선택 해제
          {filter.values.length > 0 ? ` (${filter.values.length.toLocaleString()})` : ''}
        </Button>
      </Group>
    </Stack>
  )
}
