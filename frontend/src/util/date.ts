/** "YYYY-MM-DD" (DateInput 값 형식) → "YYYYMMDD" (DART list.json bgn_de/end_de 형식). */
export function toYyyymmdd(isoDate: string): string {
  return isoDate.replaceAll('-', '')
}

/** 오늘 날짜를 "YYYY-MM-DD"로. */
export function todayIso(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 1년 전 날짜를 "YYYY-MM-DD"로. */
export function oneYearAgoIso(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
