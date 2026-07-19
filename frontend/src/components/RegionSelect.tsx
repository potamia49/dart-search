import {
  CheckIcon,
  Checkbox,
  Combobox,
  Group,
  Pill,
  PillsInput,
  ScrollArea,
  Stack,
  Text,
  useCombobox,
} from '@mantine/core'
import { useState } from 'react'
import type { RegionMeta } from '../types'

interface RegionSelectProps {
  regions: RegionMeta[]
  sido: string[]
  /** 시도별 시군구 선택. 키가 없거나 빈 배열이면 그 시도 전체를 의미한다. */
  sigunguBySido: Record<string, string[]>
  onSidoChange: (sido: string[]) => void
  onSigunguBySidoChange: (next: Record<string, string[]>) => void
}

/**
 * 지역 선택 — 업종 선택(IndustryTreeSelect)과 완전히 동일한 구조·상호작용.
 * 시도를 고르는 순간 드롭다운이 닫혀(`combobox.closeDropdown()`) 그 아래
 * "전체 선택 + 시군구 체크박스" 섹션이 즉시 드러난다(MultiSelect처럼 드롭다운이
 * 열린 채 하위 체크박스를 가리는 문제 방지). 시군구가 시도별로 그룹화되므로
 * 여러 시도를 골라도 "중구"처럼 시도 간 시군구명이 충돌하지 않고, 어떤 시도의
 * 시군구를 하나도 체크하지 않으면 그 시도 전체를 검색한다.
 */
export default function RegionSelect({
  regions,
  sido,
  sigunguBySido,
  onSidoChange,
  onSigunguBySidoChange,
}: RegionSelectProps) {
  const [search, setSearch] = useState('')
  const combobox = useCombobox({
    onDropdownClose: () => {
      combobox.resetSelectedOption()
      setSearch('')
    },
  })

  const selectedRegions = sido
    .map((name) => regions.find((r) => r.sido === name))
    .filter((r): r is RegionMeta => Boolean(r))

  function setSido(next: string[]) {
    onSidoChange(next)
    // 선택 해제된 시도의 시군구 선택은 버린다.
    const pruned: Record<string, string[]> = {}
    for (const name of next) {
      if (sigunguBySido[name]) pruned[name] = sigunguBySido[name]
    }
    onSigunguBySidoChange(pruned)
  }

  function toggleSido(name: string) {
    setSido(sido.includes(name) ? sido.filter((s) => s !== name) : [...sido, name])
  }

  function setSidoSigungu(sidoName: string, values: string[]) {
    const next = { ...sigunguBySido }
    if (values.length === 0) delete next[sidoName]
    else next[sidoName] = values
    onSigunguBySidoChange(next)
  }

  function toggleAll(region: RegionMeta, checked: boolean) {
    // "전체 선택" 체크 시 모든 시군구를 담고, 해제 시 비운다(비면 그 시도 전체 = 동일 의미).
    setSidoSigungu(region.sido, checked ? [...region.sigungu] : [])
  }

  function toggleSigungu(region: RegionMeta, name: string, checked: boolean) {
    const current = new Set(sigunguBySido[region.sido] ?? [])
    if (checked) current.add(name)
    else current.delete(name)
    setSidoSigungu(region.sido, Array.from(current))
  }

  const query = search.trim().toLowerCase()
  const options = regions
    .filter((r) => r.sido.toLowerCase().includes(query))
    .map((r) => {
      const active = sido.includes(r.sido)
      return (
        <Combobox.Option value={r.sido} key={r.sido} active={active}>
          <Group gap="xs" wrap="nowrap">
            {active ? <CheckIcon size={12} /> : <span style={{ width: 12 }} />}
            <span>{r.sido}</span>
          </Group>
        </Combobox.Option>
      )
    })

  const pills = sido.map((name) => (
    <Pill key={name} withRemoveButton onRemove={() => toggleSido(name)}>
      {name}
    </Pill>
  ))

  return (
    <Stack gap="sm">
      <Combobox
        store={combobox}
        onOptionSubmit={(name) => {
          // 시도를 고르는 순간 드롭다운을 닫아 시군구 섹션이 바로 드러나게 한다.
          toggleSido(name)
          setSearch('')
          combobox.closeDropdown()
        }}
      >
        <Combobox.DropdownTarget>
          <PillsInput label="시도 (여러 곳 선택 가능)" onClick={() => combobox.openDropdown()}>
            <Pill.Group>
              {pills}
              <Combobox.EventsTarget>
                <PillsInput.Field
                  placeholder={sido.length > 0 ? '시도 추가' : '전체 (시도 미선택 시 전국)'}
                  value={search}
                  onFocus={() => combobox.openDropdown()}
                  onBlur={() => combobox.closeDropdown()}
                  onChange={(e) => {
                    combobox.openDropdown()
                    combobox.updateSelectedOptionIndex()
                    setSearch(e.currentTarget.value)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Backspace' && search.length === 0 && sido.length > 0) {
                      e.preventDefault()
                      toggleSido(sido[sido.length - 1])
                    }
                  }}
                />
              </Combobox.EventsTarget>
            </Pill.Group>
          </PillsInput>
        </Combobox.DropdownTarget>

        <Combobox.Dropdown>
          <Combobox.Options mah={220} style={{ overflowY: 'auto' }}>
            {options.length > 0 ? (
              options
            ) : (
              <Combobox.Empty>일치하는 시도가 없습니다</Combobox.Empty>
            )}
          </Combobox.Options>
        </Combobox.Dropdown>
      </Combobox>

      {selectedRegions.length > 0 && (
        <ScrollArea.Autosize mah={300} type="auto" offsetScrollbars>
          <Stack gap="md">
            {selectedRegions.map((region) => {
              const selected = new Set(sigunguBySido[region.sido] ?? [])
              const allChecked =
                region.sigungu.length > 0 && region.sigungu.every((n) => selected.has(n))
              return (
                <div key={region.sido}>
                  <Checkbox
                    checked={allChecked}
                    indeterminate={selected.size > 0 && !allChecked}
                    label={
                      <Text fw={600}>
                        {region.sido} 전체 선택{' '}
                        <Text span size="xs" c="dimmed">
                          (미선택 시 전체)
                        </Text>
                      </Text>
                    }
                    disabled={region.sigungu.length === 0}
                    onChange={(e) => toggleAll(region, e.currentTarget.checked)}
                  />
                  {region.sigungu.length > 0 ? (
                    <Group gap="xs" pl="lg" mt={4}>
                      {region.sigungu.map((name) => (
                        <Checkbox
                          key={name}
                          size="sm"
                          label={name}
                          checked={selected.has(name)}
                          onChange={(e) => toggleSigungu(region, name, e.currentTarget.checked)}
                        />
                      ))}
                    </Group>
                  ) : (
                    <Text size="xs" c="dimmed" pl="lg" mt={4}>
                      하위 시군구가 없는 지역입니다 ({region.sido} 전체 검색).
                    </Text>
                  )}
                </div>
              )
            })}
          </Stack>
        </ScrollArea.Autosize>
      )}
    </Stack>
  )
}
