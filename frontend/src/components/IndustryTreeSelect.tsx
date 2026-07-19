import { useEffect, useState } from 'react'
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
import type { IndustryMeta } from '../types'

interface IndustryTreeSelectProps {
  industries: IndustryMeta[]
  selected: string[]
  onChange: (codes: string[]) => void
}

/**
 * KSIC 대/중분류 멀티 선택. 아무것도 선택하지 않으면 "전체"를 의미한다
 * (상세개발계획.md §7-1). RegionSelect(시도 Select → 시군구 체크박스)와 같은
 * 2단계 구조를 따른다: 대분류를 먼저 고르면, 고른 대분류마다 하위 중분류
 * 체크박스 섹션이 순서대로 나타난다(대분류 미선택 시 중분류 섹션 자체가
 * 보이지 않아 스크롤이 줄어든다).
 *
 * 대분류 선택기는 Mantine `MultiSelect` 대신 `Combobox` 프리미티브로 직접
 * 구성한다 — MultiSelect는 선택 후에도 드롭다운을 열어둔 채라 그 아래에
 * 새로 나타나는 하위 중분류 체크박스를 가려 "선택했는데 안 보인다"는 오해를
 * 준다. 여기서는 대분류를 하나 고르는 순간 `combobox.closeDropdown()`으로
 * 드롭다운을 닫아, 하위 체크박스가 즉시 드러나게 한다. 제거 가능한 pill·검색·
 * 다중 선택 UX는 그대로 유지한다.
 */
export default function IndustryTreeSelect({
  industries,
  selected,
  onChange,
}: IndustryTreeSelectProps) {
  const selectedSet = new Set(selected)

  // 중분류 섹션을 펼쳐 보여줄 대분류 코드 목록. 실제 검색 필터(selected)와는
  // 별개의 UI 상태 — RegionSelect의 "시도"가 시군구 섹션 노출 여부만
  // 결정하는 것과 같은 역할이다. 접어도 이미 체크한 하위 선택은 유지된다.
  const [expandedCodes, setExpandedCodes] = useState<string[]>([])
  const [search, setSearch] = useState('')

  const combobox = useCombobox({
    onDropdownClose: () => {
      combobox.resetSelectedOption()
      setSearch('')
    },
  })

  // industries가 비동기로 로드된 뒤, 이미 selected에 포함된 코드가 속한
  // 대분류는 기본으로 펼쳐 보여준다(사용자가 아직 직접 조작하지 않았을 때만).
  useEffect(() => {
    if (industries.length === 0) return
    setExpandedCodes((prev) => {
      if (prev.length > 0) return prev
      return industries
        .filter(
          (parent) =>
            selectedSet.has(parent.code) ||
            (parent.children ?? []).some((child) => selectedSet.has(child.code)),
        )
        .map((parent) => parent.code)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industries])

  const expandedParents = industries.filter((parent) => expandedCodes.includes(parent.code))

  function toggleExpanded(code: string) {
    setExpandedCodes((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    )
  }

  function toggleParent(parent: IndustryMeta, checked: boolean) {
    const childCodes = (parent.children ?? []).map((c) => c.code)
    const codesToToggle = [parent.code, ...childCodes]
    const next = new Set(selectedSet)
    for (const code of codesToToggle) {
      if (checked) next.add(code)
      else next.delete(code)
    }
    onChange(Array.from(next))
  }

  function toggleChild(parent: IndustryMeta, child: IndustryMeta, checked: boolean) {
    const next = new Set(selectedSet)
    if (checked) next.add(child.code)
    else next.delete(child.code)

    // 하위가 전부 선택되면 부모도 선택 표시, 하나라도 빠지면 부모 선택 해제
    const childCodes = (parent.children ?? []).map((c) => c.code)
    const allChildrenSelected = childCodes.every((c) => next.has(c))
    if (allChildrenSelected) next.add(parent.code)
    else next.delete(parent.code)

    onChange(Array.from(next))
  }

  const query = search.trim().toLowerCase()
  const options = industries
    .filter((parent) => `${parent.code} · ${parent.name}`.toLowerCase().includes(query))
    .map((parent) => {
      const active = expandedCodes.includes(parent.code)
      return (
        <Combobox.Option value={parent.code} key={parent.code} active={active}>
          <Group gap="xs" wrap="nowrap">
            {active ? <CheckIcon size={12} /> : <span style={{ width: 12 }} />}
            <span>
              {parent.code} · {parent.name}
            </span>
          </Group>
        </Combobox.Option>
      )
    })

  const pills = expandedCodes.map((code) => {
    const parent = industries.find((p) => p.code === code)
    return (
      <Pill key={code} withRemoveButton onRemove={() => toggleExpanded(code)}>
        {parent ? `${parent.code} · ${parent.name}` : code}
      </Pill>
    )
  })

  return (
    <Stack gap="sm">
      <Combobox
        store={combobox}
        onOptionSubmit={(code) => {
          // 대분류를 고르는 순간 드롭다운을 닫아 하위 체크박스가 바로 드러나게 한다.
          toggleExpanded(code)
          setSearch('')
          combobox.closeDropdown()
        }}
      >
        <Combobox.DropdownTarget>
          <PillsInput label="대분류" onClick={() => combobox.openDropdown()}>
            <Pill.Group>
              {pills}
              <Combobox.EventsTarget>
                <PillsInput.Field
                  placeholder={
                    expandedCodes.length > 0
                      ? '대분류 추가'
                      : '대분류를 선택하면 하위 중분류가 표시됩니다 (미선택 시 전체)'
                  }
                  value={search}
                  onFocus={() => combobox.openDropdown()}
                  onBlur={() => combobox.closeDropdown()}
                  onChange={(e) => {
                    combobox.openDropdown()
                    combobox.updateSelectedOptionIndex()
                    setSearch(e.currentTarget.value)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Backspace' && search.length === 0 && expandedCodes.length > 0) {
                      e.preventDefault()
                      toggleExpanded(expandedCodes[expandedCodes.length - 1])
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
              <Combobox.Empty>일치하는 대분류가 없습니다</Combobox.Empty>
            )}
          </Combobox.Options>
        </Combobox.Dropdown>
      </Combobox>

      {expandedParents.length > 0 && (
        <ScrollArea h={260} type="auto" offsetScrollbars>
          <Stack gap="md">
            {expandedParents.map((parent) => (
              <div key={parent.code}>
                <Checkbox
                  checked={selectedSet.has(parent.code)}
                  label={
                    <Text fw={600}>
                      {parent.code} · {parent.name} (전체 선택)
                    </Text>
                  }
                  onChange={(e) => toggleParent(parent, e.currentTarget.checked)}
                />
                {parent.children && parent.children.length > 0 && (
                  <Stack gap={4} pl="lg" mt={4}>
                    {parent.children.map((child) => (
                      <Checkbox
                        key={child.code}
                        size="sm"
                        checked={selectedSet.has(child.code)}
                        label={`${child.code} · ${child.name}`}
                        onChange={(e) => toggleChild(parent, child, e.currentTarget.checked)}
                      />
                    ))}
                  </Stack>
                )}
              </div>
            ))}
          </Stack>
        </ScrollArea>
      )}
    </Stack>
  )
}
