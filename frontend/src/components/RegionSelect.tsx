import { Checkbox, Group, ScrollArea, Select, Stack, Text } from '@mantine/core'
import type { RegionMeta } from '../types'

interface RegionSelectProps {
  regions: RegionMeta[]
  sido: string | null
  sigungu: string[]
  onSidoChange: (sido: string | null) => void
  onSigunguChange: (sigungu: string[]) => void
}

/** 시도 셀렉트 → 선택된 시도의 시군구 멀티 체크박스 (상세개발계획.md §7-1). */
export default function RegionSelect({
  regions,
  sido,
  sigungu,
  onSidoChange,
  onSigunguChange,
}: RegionSelectProps) {
  const currentRegion = regions.find((r) => r.sido === sido)
  const sigunguSet = new Set(sigungu)

  function handleSidoChange(next: string | null) {
    onSidoChange(next)
    onSigunguChange([])
  }

  function toggleSigungu(name: string, checked: boolean) {
    const next = new Set(sigunguSet)
    if (checked) next.add(name)
    else next.delete(name)
    onSigunguChange(Array.from(next))
  }

  return (
    <Stack gap="sm">
      <Select
        label="시도"
        placeholder="전체 (시도 미선택 시 전국)"
        data={regions.map((r) => r.sido)}
        value={sido}
        onChange={handleSidoChange}
        clearable
        searchable
      />
      {currentRegion && currentRegion.sigungu.length > 0 && (
        <div>
          <Text size="sm" fw={500} mb={4}>
            시군구 (미선택 시 {currentRegion.sido} 전체)
          </Text>
          <ScrollArea h={160} type="auto" offsetScrollbars>
            <Group gap="xs">
              {currentRegion.sigungu.map((name) => (
                <Checkbox
                  key={name}
                  label={name}
                  checked={sigunguSet.has(name)}
                  onChange={(e) => toggleSigungu(name, e.currentTarget.checked)}
                />
              ))}
            </Group>
          </ScrollArea>
        </div>
      )}
    </Stack>
  )
}
