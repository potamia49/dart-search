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
  UnstyledButton,
  useCombobox,
} from '@mantine/core'
import type { IndustryMeta } from '../types'

interface IndustryTreeSelectProps {
  industries: IndustryMeta[]
  selected: string[]
  onChange: (codes: string[]) => void
}

/**
 * KSIC 대/중/소분류 멀티 선택. 아무것도 선택하지 않으면 "전체"를 의미한다
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
 *
 * M8 5단계에서 **소분류(3자리) 한 층이 추가**됐다. 중분류 체크박스 아래에
 * 접힌 채로 두고 "▸ 소분류 N개" 를 눌러야 펼쳐진다 — 제조업(중분류 25개,
 * 소분류 70여 개)처럼 큰 대분류에서 전부 펼쳐 두면 목록이 스크롤에 묻힌다.
 *
 * 상위를 고르면 하위는 따로 고를 필요가 없다 — 백엔드가 `induty_code`를
 * prefix로 매칭하므로 "25"는 25로 시작하는 모든 코드를 포함한다. 그래서
 * 상위 체크박스를 켜면 하위 체크박스를 개별 선택에서 빼고(중복 전송 방지),
 * 상위가 켜진 동안 하위는 "포함됨"으로 비활성 표시한다.
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

  /** 해당 노드와 그 아래 모든 후손 코드. 상위를 켤 때 하위를 걷어내는 데 쓴다. */
  function descendantCodes(node: IndustryMeta): string[] {
    return (node.children ?? []).flatMap((child) => [child.code, ...descendantCodes(child)])
  }

  /**
   * 노드 하나를 켜고 끈다. 켤 때는 그 아래 후손 선택을 모두 걷어낸다 —
   * prefix 매칭이라 상위 코드 하나로 이미 전부 포함되고, 남겨두면 같은 회사를
   * 가리키는 코드가 조건에 중복으로 실린다.
   */
  function toggleNode(node: IndustryMeta, checked: boolean) {
    const next = new Set(selectedSet)
    if (checked) {
      next.add(node.code)
      for (const code of descendantCodes(node)) next.delete(code)
    } else {
      next.delete(node.code)
    }
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
            {expandedParents.map((parent) => {
              const parentSelected = selectedSet.has(parent.code)
              return (
                <div key={parent.code}>
                  <Checkbox
                    checked={parentSelected}
                    label={
                      <Text fw={600}>
                        {parent.code} · {parent.name} (전체 선택)
                      </Text>
                    }
                    onChange={(e) => toggleNode(parent, e.currentTarget.checked)}
                  />
                  {parent.children && parent.children.length > 0 && (
                    <Stack gap={4} pl="lg" mt={4}>
                      {parent.children.map((mid) => (
                        <MidClassRow
                          key={mid.code}
                          mid={mid}
                          // 대분류를 켜면 하위는 이미 포함된 상태라 개별 선택을
                          // 막고 "포함됨"으로만 표시한다.
                          coveredByParent={parentSelected}
                          selectedSet={selectedSet}
                          onToggle={toggleNode}
                        />
                      ))}
                    </Stack>
                  )}
                </div>
              )
            })}
          </Stack>
        </ScrollArea>
      )}
    </Stack>
  )
}

/** 중분류 한 줄 + 접힌 소분류 목록. 소분류는 눌러야 펼쳐진다(위 주석 참고). */
function MidClassRow({
  mid,
  coveredByParent,
  selectedSet,
  onToggle,
}: {
  mid: IndustryMeta
  coveredByParent: boolean
  selectedSet: Set<string>
  onToggle: (node: IndustryMeta, checked: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const midSelected = selectedSet.has(mid.code)
  const subs = mid.children ?? []
  const selectedSubCount = subs.filter((sub) => selectedSet.has(sub.code)).length

  return (
    <div>
      <Group gap="xs" wrap="nowrap">
        <Checkbox
          size="sm"
          disabled={coveredByParent}
          checked={coveredByParent || midSelected}
          label={`${mid.code} · ${mid.name}`}
          onChange={(e) => onToggle(mid, e.currentTarget.checked)}
        />
        {subs.length > 0 && !coveredByParent && !midSelected && (
          <UnstyledButton onClick={() => setOpen((prev) => !prev)}>
            <Text size="xs" c="blue">
              {open ? '▾' : '▸'} 소분류 {subs.length}개
              {selectedSubCount > 0 ? ` (${selectedSubCount} 선택)` : ''}
            </Text>
          </UnstyledButton>
        )}
      </Group>
      {open && !coveredByParent && !midSelected && (
        <Stack gap={2} pl="lg" mt={2}>
          {subs.map((sub) => (
            <Checkbox
              key={sub.code}
              size="xs"
              checked={selectedSet.has(sub.code)}
              label={`${sub.code} · ${sub.name}`}
              onChange={(e) => onToggle(sub, e.currentTarget.checked)}
            />
          ))}
        </Stack>
      )}
    </div>
  )
}
