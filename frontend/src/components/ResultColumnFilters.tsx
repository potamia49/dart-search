import {
  ActionIcon,
  Button,
  Checkbox,
  Divider,
  NumberInput,
  Popover,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
} from '@mantine/core'
import { IconFilter, IconFilterFilled } from '@tabler/icons-react'
import type { ReactNode } from 'react'
import {
  AUDITOR_CHANGED_LABELS,
  MAX_TEXT_FILTER_LEN,
  PARSE_STATUS_LABELS,
  isAssetsFilterActive,
  isAuditorChangedFilterActive,
  isParseStatusFilterActive,
  isRangeInverted,
  isRevenueFilterActive,
  isStaleFilterActive,
  isTextFilterActive,
  isValueFilterActive,
} from '../util/resultFilters'
import type {
  EokInput,
  FilterMode,
  ResultColumnFilterState,
  TextFilterState,
  ValueListFilterState,
} from '../util/resultFilters'
import { ValueListFilterBody } from './ResultValueListFilter'

/**
 * 결과 화면(§4-13-C) 컬럼 헤더 필터의 **UI**. 상태 정의·쿼리 변환 규칙은
 * `util/resultFilters.ts`에 있다(파일이 갈린 이유는 fast-refresh 규칙 —
 * 컴포넌트 파일에서 상수/헬퍼를 함께 내보내면 HMR이 깨진다).
 */

/** 컬럼 헤더에 붙는 깔때기 아이콘 + 팝오버 껍데기. 필터가 걸린 컬럼은 아이콘을
 * 채워진 파란 버튼으로 바꿔 "지금 뭐가 걸려 있는지"를 헤더에서 바로 알 수 있게 한다. */
function ColumnFilterPopover({
  label,
  active,
  width = 260,
  children,
}: {
  label: string
  active: boolean
  width?: number
  children: ReactNode
}) {
  return (
    <Popover width={width} position="bottom-start" withArrow shadow="md" trapFocus>
      <Popover.Target>
        {/* 비활성 색이 `gray`(#212529)면 딥 네이비 헤더(index.css의 `.mantine-Table-th`,
            #0a192f) 위에서 대비 1.14:1로 **사실상 보이지 않는다**. 탭이 폐지된 지금 이
            아이콘이 파싱상태/감사인변동/금액 필터의 유일한 진입점이라, 헤더 글자색(#fff)에
            가까운 밝은 회색(gray.3 = #dee2e6, 대비 약 13:1)으로 둔다. 이 컴포넌트는 표
            헤더 안에서만 쓰인다(밝은 배경에 놓지 말 것). */}
        <ActionIcon
          size="sm"
          variant={active ? 'filled' : 'subtle'}
          color={active ? 'blue' : 'gray.3'}
          aria-label={`${label} 필터${active ? ' (적용 중)' : ''}`}
        >
          {active ? <IconFilterFilled size={14} /> : <IconFilter size={14} />}
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text size="sm" fw={600}>
            {label} 필터
          </Text>
          {children}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}

/** 체크박스 목록 — **최소 1개는 반드시 켜져 있어야 한다**(마지막 하나는 해제 불가).
 * 전부 끄면 백엔드가 정직하게 0건을 주는데, 화면에는 "필터를 만졌더니 아무것도 없다"로만
 * 보여 사용자를 혼란시키기 때문이다(§4-13-C). */
function CheckboxFilter<T extends string>({
  options,
  value,
  onChange,
  note,
}: {
  options: { value: T; label: string; hint?: string }[]
  value: T[]
  onChange: (next: T[]) => void
  note?: string
}) {
  const lastOne = value.length <= 1
  const allSelected = value.length === options.length
  return (
    <Stack gap={8}>
      {options.map((opt) => {
        const checked = value.includes(opt.value)
        return (
          <Checkbox
            key={opt.value}
            size="sm"
            label={opt.label}
            description={opt.hint}
            checked={checked}
            disabled={checked && lastOne}
            onChange={(event) => {
              // 선택 순서가 아니라 **options 선언 순서**를 유지한다 — 상단 요약
              // 문구("파싱상태 부분 성공·검수 필요")가 체크한 순서에 따라 뒤바뀌면
              // 같은 필터인데 다르게 읽힌다.
              const next = event.currentTarget.checked
                ? options.map((o) => o.value).filter((v) => v === opt.value || value.includes(v))
                : value.filter((v) => v !== opt.value)
              onChange(next)
            }}
          />
        )
      })}
      {note && (
        <Text size="xs" c="dimmed">
          {note}
        </Text>
      )}
      <Button
        size="compact-xs"
        variant="subtle"
        disabled={allSelected}
        onClick={() => onChange(options.map((o) => o.value))}
      >
        전체 선택
      </Button>
    </Stack>
  )
}

/** 금액 범위(억원) 입력 — 억원으로 받아 백엔드에는 원 단위로 보낸다.
 * 매출액이 음수인 회사가 실재하므로(실측 -5,409백만원) 하한을 0으로 막지 않는다.
 *
 * 상·하한(±1,000만억원 = ±10^15원)은 실측 최대값보다 넉넉하면서도 오타로 터무니없는
 * 값이 백엔드로 나가 400/422가 나는 것을 막는 선이다. */
const AMOUNT_LIMIT_EOK = 10_000_000

function AmountRangeFilter({
  label,
  min,
  max,
  onChange,
}: {
  label: string
  min: EokInput
  max: EokInput
  onChange: (min: EokInput, max: EokInput) => void
}) {
  const toEok = (raw: string | number): EokInput => {
    if (raw === '' || raw === '-') return ''
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : ''
  }
  // 최소>최대는 조용히 "값 없는 회사만 남은 목록"을 만든다 — 두 입력을 모두 error로
  // 표시하고, 이 상태에서는 `resultFiltersToParams()`가 이 컬럼 범위를 아예 보내지 않는다.
  const inverted = isRangeInverted(min, max)
  return (
    <Stack gap="xs">
      <NumberInput
        size="xs"
        label="최소 (억원)"
        placeholder="제한 없음"
        thousandSeparator=","
        suffix=" 억원"
        min={-AMOUNT_LIMIT_EOK}
        max={AMOUNT_LIMIT_EOK}
        value={min}
        error={inverted}
        onChange={(v) => onChange(toEok(v), max)}
      />
      <NumberInput
        size="xs"
        label="최대 (억원)"
        placeholder="제한 없음"
        thousandSeparator=","
        suffix=" 억원"
        min={-AMOUNT_LIMIT_EOK}
        max={AMOUNT_LIMIT_EOK}
        value={max}
        error={inverted ? '최소값이 최대값보다 큽니다 — 이 범위는 적용하지 않았습니다.' : false}
        onChange={(v) => onChange(min, toEok(v))}
      />
      <Text size="xs" c="dimmed">
        단위는 <b>억원</b>입니다 — 표에 보이는 금액은 <b>원</b> 단위라 그대로 옮겨 적으면 안
        됩니다(예: 표의 5,000,000,000원 → 여기에는 50).
      </Text>
      <Text size="xs" c="dimmed">
        감사보고서 원문에서 <b>실제로 파싱한 {label}</b>(당기) 기준입니다. {label} 값이 없는
        회사(파싱 실패·감사보고서 없음 등)는 범위를 걸어도 <b>계속 목록에 남습니다</b> — 검수
        대상이 조용히 사라지지 않게 한 것입니다.
      </Text>
      <Divider />
      <Button
        size="compact-xs"
        variant="subtle"
        disabled={min === '' && max === ''}
        onClick={() => onChange('', '')}
      >
        범위 지우기
      </Button>
    </Stack>
  )
}

/** §4-14 회사명/주소 — 검색어 1개 + 포함/제외(부분일치).
 *
 * 값 목록 방식이 아닌 이유는 고유값이 행 수와 사실상 같아 목록에서 고를 수 없기
 * 때문이다(백엔드도 `distinct-values`에서 이 두 컬럼을 일부러 뺐다). */
function TextFilter({
  label,
  placeholder,
  filter,
  onChange,
  hint,
}: {
  label: string
  placeholder: string
  filter: TextFilterState
  onChange: (next: TextFilterState) => void
  hint?: string
}) {
  return (
    <Stack gap="xs">
      <SegmentedControl
        size="xs"
        fullWidth
        value={filter.mode}
        onChange={(value) => onChange({ ...filter, mode: value as FilterMode })}
        data={[
          { value: 'INCLUDE', label: '이 글자 포함' },
          { value: 'EXCLUDE', label: '이 글자 제외' },
        ]}
      />
      <TextInput
        size="xs"
        placeholder={placeholder}
        value={filter.keyword}
        // 상한은 백엔드와 같은 200자다 — 넘겨 보내면 400이라 목록이 통째로 안 뜬다.
        maxLength={MAX_TEXT_FILTER_LEN}
        onChange={(event) => onChange({ ...filter, keyword: event.currentTarget.value })}
      />
      <Text size="xs" c="dimmed">
        {label}에 이 글자가 <b>들어가는지</b>로 거릅니다(대소문자 구분 없음, 완전일치가 아님).
        {filter.mode === 'INCLUDE'
          ? ` ${label} 값이 없는 회사는 빠집니다.`
          : ` ${label} 값이 없는 회사는 그대로 남습니다.`}
      </Text>
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
      <Divider />
      <Button
        size="compact-xs"
        variant="subtle"
        disabled={filter.keyword === ''}
        onClick={() => onChange({ ...filter, keyword: '' })}
      >
        검색어 지우기
      </Button>
    </Stack>
  )
}

/** 결과 테이블 헤더에 붙일 필터 컨트롤 — 필터가 있는 컬럼이면 아이콘을, 없으면 null.
 *
 * 업종/감사인/감사의견의 "지금 데이터의 값 목록"은 §4-14(2026-08-05)에서 백엔드
 * `distinct-values` 조회가 생겨 지원한다 — 목록 조회는 팝오버가 열릴 때만 일어난다
 * (`ValueListFilterBody`). 그래서 이 컨트롤은 `jobId`를 받는다. */
export function ResultColumnFilterControl({
  jobId,
  columnKey,
  filters,
  onChange,
}: {
  jobId: number
  columnKey: string
  filters: ResultColumnFilterState
  onChange: (patch: Partial<ResultColumnFilterState>) => void
}) {
  /** 값 목록 필터 3종은 껍데기가 같아 한 곳에서 만든다. */
  function valueListPopover(
    label: string,
    field: 'induty_name' | 'auditor_name' | 'audit_opinion',
    filter: ValueListFilterState,
    apply: (next: ValueListFilterState) => Partial<ResultColumnFilterState>,
    hint?: string,
  ) {
    return (
      <ColumnFilterPopover label={label} active={isValueFilterActive(filter)} width={320}>
        <ValueListFilterBody
          jobId={jobId}
          field={field}
          label={label}
          filter={filter}
          hint={hint}
          onChange={(next) => onChange(apply(next))}
        />
      </ColumnFilterPopover>
    )
  }

  if (columnKey === 'corp_name') {
    return (
      <ColumnFilterPopover label="회사명" active={isTextFilterActive(filters.corpName)} width={300}>
        <TextFilter
          label="회사명"
          placeholder="예: 산업"
          filter={filters.corpName}
          onChange={(corpName) => onChange({ corpName })}
        />
      </ColumnFilterPopover>
    )
  }
  if (columnKey === 'address') {
    return (
      <ColumnFilterPopover label="주소" active={isTextFilterActive(filters.address)} width={300}>
        <TextFilter
          label="주소"
          placeholder="예: 창원시 성산구"
          filter={filters.address}
          onChange={(address) => onChange({ address })}
          hint="주소는 DART 기업개황 원본 표기 그대로입니다 — 같은 지역이라도 표기가 다를 수 있으니 짧은 단위(시·군·구, 읍·면·동)로 넣는 편이 잘 잡힙니다."
        />
      </ColumnFilterPopover>
    )
  }
  if (columnKey === 'induty_name') {
    return valueListPopover('업종', 'induty_name', filters.indutyName, (indutyName) => ({
      indutyName,
    }))
  }
  if (columnKey === 'auditor_name') {
    return valueListPopover(
      '감사인',
      'auditor_name',
      filters.auditorName,
      (auditorName) => ({ auditorName }),
      // 목록 셀은 "안경회계법인(경상남도 창원시)"처럼 사무소 주소를 붙여 보여주지만,
      // 필터가 거르는 값은 **법인명뿐**이다(주소는 별개 컬럼) — 아래 값 목록에 주소가
      // 안 보이는 이유를 그 자리에서 설명해 둔다.
      '표에는 "회계법인명(사무소 지역)"으로 함께 보이지만, 이 목록은 감사인 이름만으로 고릅니다.',
    )
  }
  if (columnKey === 'audit_opinion') {
    return valueListPopover('감사의견', 'audit_opinion', filters.auditOpinion, (auditOpinion) => ({
      auditOpinion,
    }))
  }
  if (columnKey === 'revenue_cur') {
    return (
      <ColumnFilterPopover label="매출액" active={isRevenueFilterActive(filters)} width={280}>
        <AmountRangeFilter
          label="매출액"
          min={filters.revenueMinEok}
          max={filters.revenueMaxEok}
          onChange={(revenueMinEok, revenueMaxEok) => onChange({ revenueMinEok, revenueMaxEok })}
        />
      </ColumnFilterPopover>
    )
  }
  if (columnKey === 'total_assets_cur') {
    return (
      <ColumnFilterPopover label="총자산" active={isAssetsFilterActive(filters)} width={280}>
        <AmountRangeFilter
          label="총자산"
          min={filters.assetsMinEok}
          max={filters.assetsMaxEok}
          onChange={(assetsMinEok, assetsMaxEok) => onChange({ assetsMinEok, assetsMaxEok })}
        />
      </ColumnFilterPopover>
    )
  }
  if (columnKey === 'parse_status') {
    return (
      <ColumnFilterPopover label="파싱상태" active={isParseStatusFilterActive(filters)} width={300}>
        <CheckboxFilter
          options={[
            { value: 'OK', label: PARSE_STATUS_LABELS.OK },
            { value: 'PARTIAL', label: PARSE_STATUS_LABELS.PARTIAL },
            {
              value: 'FAILED_REVIEW',
              label: PARSE_STATUS_LABELS.FAILED_REVIEW,
              hint: '원문은 있는데 재무 항목을 뽑지 못한 회사 — 사람이 확인해야 합니다.',
            },
            {
              value: 'NO_DISCLOSURE',
              label: PARSE_STATUS_LABELS.NO_DISCLOSURE,
              hint: 'DART에 감사보고서 공시 자체가 없어 열어볼 원문이 없습니다 — 파싱 실패가 아니라 검수 대상이 아닙니다.',
            },
          ]}
          value={filters.parseStatus}
          onChange={(parseStatus) => onChange({ parseStatus })}
          note="최소 한 가지는 선택돼 있어야 합니다."
        />
      </ColumnFilterPopover>
    )
  }
  if (columnKey === 'auditor_changed') {
    return (
      <ColumnFilterPopover
        label="감사인변동"
        active={isAuditorChangedFilterActive(filters)}
        width={300}
      >
        <CheckboxFilter
          options={[
            {
              value: 'CHANGED',
              label: AUDITOR_CHANGED_LABELS.CHANGED,
              hint: '수집한 재무 이력 안에서 감사인(회계법인·감사반)이 한 번 이상 바뀐 회사입니다. 어느 해에 바뀌었는지는 행을 클릭해 상세의 "감사인" 행에서 확인하세요.',
            },
            { value: 'UNCHANGED', label: AUDITOR_CHANGED_LABELS.UNCHANGED },
            {
              value: 'UNKNOWN',
              label: AUDITOR_CHANGED_LABELS.UNKNOWN,
              hint: '감사인을 확인한 연도가 1개 이하라 판정할 수 없습니다(이 기능 추가 전에 수집된 작업은 전부 여기에 들어갑니다 — 다만 상세의 "감사인" 행은 원문을 그때그때 읽어 채웁니다).',
            },
          ]}
          value={filters.auditorChanged}
          onChange={(auditorChanged) => onChange({ auditorChanged })}
          note="최소 한 가지는 선택돼 있어야 합니다."
        />
      </ColumnFilterPopover>
    )
  }
  return null
}

/** 휴면·폐업 추정 필터 — 전용 컬럼이 없어(판정 근거인 "최근 공시일자"는 기본 숨김
 * 컬럼이라 거기 달면 필터가 보이지 않는다) 상단 필터바에 버튼으로 둔다. 탭이 아니라
 * 다른 컬럼 필터와 같은 체크박스 목록이고, 기본값은 예전 기본 숨김 그대로 "아니오"만이다. */
export function StaleDisclosureFilterButton({
  filters,
  onChange,
}: {
  filters: ResultColumnFilterState
  onChange: (patch: Partial<ResultColumnFilterState>) => void
}) {
  const active = isStaleFilterActive(filters)
  const summary =
    filters.stale.length === 2 ? '포함' : filters.stale[0] === 'STALE' ? '이것만 보기' : '제외'
  return (
    <Popover width={300} position="bottom-start" withArrow shadow="md" trapFocus>
      <Popover.Target>
        <Button
          variant={active ? 'light' : 'default'}
          color={active ? 'blue' : undefined}
          leftSection={active ? <IconFilterFilled size={14} /> : <IconFilter size={14} />}
        >
          휴면·폐업 추정: {summary}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text size="sm" fw={600}>
            휴면·폐업 추정 필터
          </Text>
          <CheckboxFilter
            options={[
              {
                value: 'STALE',
                label: '예 (최근 1년 이내 DART 공시 없음)',
                hint: '폐업·휴면 상태일 가능성이 있어 기본적으로 숨깁니다. 실제 영업 여부는 이 목록만으로 단정할 수 없습니다.',
              },
              { value: 'ACTIVE', label: '아니오 (최근 1년 이내 공시 있음)' },
            ]}
            value={filters.stale}
            onChange={(stale) => onChange({ stale })}
            note="최소 한 가지는 선택돼 있어야 합니다."
          />
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}
