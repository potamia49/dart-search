import { useEffect, useState } from 'react'
import { Checkbox, MultiSelect, ScrollArea, Stack, Text } from '@mantine/core'
import type { IndustryMeta } from '../types'

interface IndustryTreeSelectProps {
  industries: IndustryMeta[]
  selected: string[]
  onChange: (codes: string[]) => void
}

/**
 * KSIC 대/중분류 멀티 선택. 아무것도 선택하지 않으면 "전체"를 의미한다
 * (상세개발계획.md §7-1). RegionSelect(시도 Select → 시군구 체크박스)와 같은
 * 2단계 구조를 따른다: 대분류를 MultiSelect로 먼저 고르면, 고른 대분류마다
 * 하위 중분류 체크박스 섹션이 순서대로 나타난다(대분류 미선택 시 중분류
 * 섹션 자체가 보이지 않아 스크롤이 줄어든다). 대분류는 지역의 시도와 달리
 * 여러 개 동시에 관심 가질 수 있어 단일 Select가 아니라 MultiSelect를 쓴다.
 * 대분류 체크박스를 누르면 하위 중분류 전체가 함께 선택/해제되고, 하위가
 * 전부 선택되면 부모도 자동 체크되는 기존 로직은 그대로 유지한다.
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

  return (
    <Stack gap="sm">
      <MultiSelect
        label="대분류"
        placeholder="대분류를 선택하면 하위 중분류가 표시됩니다 (미선택 시 전체)"
        data={industries.map((parent) => ({
          value: parent.code,
          label: `${parent.code} · ${parent.name}`,
        }))}
        value={expandedCodes}
        onChange={setExpandedCodes}
        searchable
        clearable
      />
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
