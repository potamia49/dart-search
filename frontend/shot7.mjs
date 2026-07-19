import { chromium } from 'playwright'
const out = 'C:/Users/potamia/AppData/Local/Temp/claude/c--claude-dart-search/5b12bb31-6850-4199-90b7-cbc0ea81d3fb/scratchpad'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1280, height: 1000 } })
const errs = []
p.on('console', m => { if (m.type()==='error') errs.push(m.text()) })
p.on('pageerror', e => errs.push(String(e)))
await p.goto('http://localhost:5173/search', { waitUntil: 'networkidle' })
await p.waitForTimeout(1000)
// 대분류 입력 클릭 → 드롭다운 열림
await p.getByPlaceholder('대분류를 선택하면 하위 중분류가 표시됩니다 (미선택 시 전체)').click()
await p.waitForTimeout(400)
// 제조업 선택 (선택 즉시 닫혀야 함)
await p.getByRole('option', { name: /제조업/ }).first().click()
await p.waitForTimeout(500)
// 이때 Escape 없이 바로 캡처 — 드롭다운이 스스로 닫혀 체크박스가 보여야 함
const dropdownVisible = await p.locator('.mantine-Combobox-dropdown').isVisible().catch(() => false)
const cb = await p.locator('.mantine-Checkbox-root').count()
await p.screenshot({ path: out + '/ind_autoclose.png', fullPage: true })
console.log('DROPDOWN_STILL_OPEN:', dropdownVisible)
console.log('CHECKBOXES_VISIBLE:', cb)
console.log('CONSOLE_ERRORS:', errs.length ? JSON.stringify(errs) : 'none')
await b.close()
