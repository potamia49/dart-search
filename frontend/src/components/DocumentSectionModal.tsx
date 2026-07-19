import { useEffect, useState } from 'react'
import { Alert, Loader, Modal, SegmentedControl, Stack, Text } from '@mantine/core'
import type { DocumentSection, DocumentSectionResponse } from '../types'
import { getDocumentSection } from '../api/results'

const SECTION_LABELS: { value: DocumentSection; label: string }[] = [
  { value: 'bs', label: '재무상태표' },
  { value: 'is', label: '손익계산서' },
  { value: 'cf', label: '현금흐름표' },
  { value: 'notes', label: '주석' },
]

export interface DocumentSectionTarget {
  section: DocumentSection
  /** 다년치 이력의 특정 연도 공시를 열 때만 지정(미지정 시 결과의 최신 감사보고서). */
  rceptNo?: string
  /** 화면 표시용 연도 라벨(있으면 제목에 함께 노출). */
  yearLabel?: string
}

interface DocumentSectionModalProps {
  jobId: number
  resultId: number
  corpName: string | null
  target: DocumentSectionTarget | null
  onClose: () => void
}

/** §4-8 원문 섹션 열람 — 로컬 문서 캐시에서 on-demand로 잘라낸 서버 조립 HTML을
 * 렌더링한다. HTML은 백엔드가 텍스트를 전부 이스케이프하고 COLSPAN/ROWSPAN만
 * 통과시켜 조립하므로(원문 마크업 미통과) dangerouslySetInnerHTML이 안전하다. */
export default function DocumentSectionModal({
  jobId,
  resultId,
  corpName,
  target,
  onClose,
}: DocumentSectionModalProps) {
  const [section, setSection] = useState<DocumentSection>('bs')
  const [data, setData] = useState<DocumentSectionResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 모달을 새로 열 때(target 변경) 클릭한 섹션으로 초기화한다.
  useEffect(() => {
    if (target) setSection(target.section)
  }, [target])

  const rceptNo = target?.rceptNo

  useEffect(() => {
    if (!target) return
    let cancelled = false
    setData(null)
    setError(null)
    setLoading(true)
    getDocumentSection(jobId, resultId, section, rceptNo)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((err) => {
        if (!cancelled) {
          const status = err?.response?.status
          setError(
            status === 404
              ? '원문 캐시가 없습니다 — 재수집이 필요합니다.'
              : '원문을 불러오지 못했습니다.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [jobId, resultId, section, rceptNo, target])

  const yearSuffix = target?.yearLabel ? ` · ${target.yearLabel}년` : ''

  return (
    <Modal
      opened={target !== null}
      onClose={onClose}
      size="xl"
      title={`${corpName ?? '(회사명 없음)'} 원문 보기${yearSuffix}`}
    >
      <Stack>
        <SegmentedControl
          value={section}
          onChange={(v) => setSection(v as DocumentSection)}
          data={SECTION_LABELS}
          fullWidth
        />

        {loading && <Loader size="sm" />}
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        {!loading && !error && data && !data.available && (
          <Alert color="yellow" variant="light">
            {data.notice ?? '해당 섹션을 원문에서 찾을 수 없습니다.'}
          </Alert>
        )}
        {!loading && !error && data && data.available && (
          <>
            {data.notice && (
              <Text size="xs" c="dimmed">
                {data.notice}
              </Text>
            )}
            <div
              style={{ overflowX: 'auto', fontSize: 13, lineHeight: 1.6 }}
              className="doc-section-html"
              // eslint-disable-next-line react/no-danger -- 백엔드가 이스케이프+화이트리스트로 조립한 안전 HTML(§4-8)
              dangerouslySetInnerHTML={{ __html: data.html }}
            />
          </>
        )}
      </Stack>
    </Modal>
  )
}
