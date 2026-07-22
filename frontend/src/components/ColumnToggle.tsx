import { Button, Checkbox, Popover, ScrollArea, Stack, Text } from '@mantine/core'
import type { ResultColumn } from '../util/resultColumns'
import type { ResultResponse } from '../types'

interface ColumnToggleProps {
  allColumns: ResultColumn[]
  visibleKeys: Set<keyof ResultResponse>
  onToggle: (key: keyof ResultResponse, visible: boolean) => void
  /** 토글 상태와 무관하게 화면이 강제로 표시 중인 컬럼(예: "휴면·폐업 추정" 탭의
   * 최근 공시일자). 체크박스를 실제 표시 상태와 일치시켜(체크됨·비활성) 시각적
   * 모순을 없앤다(2026-07-22 디자인 리뷰 반영). */
  forcedVisibleKeys?: Set<keyof ResultResponse>
}

/** 결과 테이블에 표시할 컬럼을 켜고 끄는 토글 (상세개발계획.md §7-3 "컬럼 표시/숨김 토글"). */
export default function ColumnToggle({
  allColumns,
  visibleKeys,
  onToggle,
  forcedVisibleKeys,
}: ColumnToggleProps) {
  return (
    <Popover width={280} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Button variant="default">컬럼 표시 ({visibleKeys.size})</Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Text size="sm" fw={600} mb={4}>
          표시할 컬럼
        </Text>
        <ScrollArea h={320}>
          <Stack gap={4}>
            {allColumns.map((col) => {
              const forced = forcedVisibleKeys?.has(col.key) ?? false
              return (
                <Checkbox
                  key={col.key}
                  size="sm"
                  label={forced ? `${col.label} (현재 탭에서 항상 표시)` : col.label}
                  checked={visibleKeys.has(col.key) || forced}
                  disabled={forced}
                  onChange={(e) => onToggle(col.key, e.currentTarget.checked)}
                />
              )
            })}
          </Stack>
        </ScrollArea>
      </Popover.Dropdown>
    </Popover>
  )
}
