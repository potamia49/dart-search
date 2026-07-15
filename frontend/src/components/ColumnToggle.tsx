import { Button, Checkbox, Popover, ScrollArea, Stack, Text } from '@mantine/core'
import type { ResultColumn } from '../util/resultColumns'
import type { ResultResponse } from '../types'

interface ColumnToggleProps {
  allColumns: ResultColumn[]
  visibleKeys: Set<keyof ResultResponse>
  onToggle: (key: keyof ResultResponse, visible: boolean) => void
}

/** 결과 테이블에 표시할 컬럼을 켜고 끄는 토글 (상세개발계획.md §7-3 "컬럼 표시/숨김 토글"). */
export default function ColumnToggle({ allColumns, visibleKeys, onToggle }: ColumnToggleProps) {
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
            {allColumns.map((col) => (
              <Checkbox
                key={col.key}
                size="sm"
                label={col.label}
                checked={visibleKeys.has(col.key)}
                onChange={(e) => onToggle(col.key, e.currentTarget.checked)}
              />
            ))}
          </Stack>
        </ScrollArea>
      </Popover.Dropdown>
    </Popover>
  )
}
