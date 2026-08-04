import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { notifications } from '@mantine/notifications'
import RegionSelect from '../components/RegionSelect'
import IndustryTreeSelect from '../components/IndustryTreeSelect'
import IndexStatusNote from '../components/IndexStatusNote'
import {
  getCandidatesPreview,
  getDartIndexStatus,
  getFscFinancialStatus,
  getIndustries,
  getRegions,
} from '../api/meta'
import { createJob } from '../api/jobs'
import type {
  CandidatesPreviewResponse,
  DartIndexStatus,
  FscFinancialStatus,
  IndustryMeta,
  RegionMeta,
} from '../types'

/**
 * 검색 조건 = **지역 + 업종**뿐이다(§4-13-A, 2026-08-05).
 *
 * 매출액/총자산 입력은 여기서 제거했다 — 비상장 외감법인은 감사보고서 원문을 열기
 * 전엔 그 값을 알 수 없어(§4-3) Job 생성 시점 조건으로는 후보 범위를 좁히지 못하고,
 * 실제로는 (a) Phase 2 처리 순서와 (b) STEP 7 이력 수집 대상만 정하고 있었다.
 * 지금은 결과 화면의 컬럼 필터에서 **실측 파싱값**(`revenue_cur`/`total_assets_cur`)
 * 기준으로 거른다(§4-13-C). 백엔드 `JobCreateRequest.revenue`/`total_assets`는
 * `default_factory`라 이 필드를 아예 안 보내면 무제한 조건으로 저장된다.
 */
export default function SearchPage() {
  const navigate = useNavigate()

  const [regions, setRegions] = useState<RegionMeta[]>([])
  const [industries, setIndustries] = useState<IndustryMeta[]>([])
  const [dartIndexStatus, setDartIndexStatus] = useState<DartIndexStatus | null>(null)
  const [financialStatStatus, setFinancialStatStatus] = useState<FscFinancialStatus | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [sido, setSido] = useState<string[]>([])
  const [sigunguBySido, setSigunguBySido] = useState<Record<string, string[]>>({})
  const [industryCodes, setIndustryCodes] = useState<string[]>([])

  const [submitting, setSubmitting] = useState(false)

  const [preview, setPreview] = useState<CandidatesPreviewResponse | null>(null)
  const [debouncedSido] = useDebouncedValue(sido, 400)
  const [debouncedSigunguBySido] = useDebouncedValue(sigunguBySido, 400)
  const [debouncedIndustryCodes] = useDebouncedValue(industryCodes, 400)

  useEffect(() => {
    let cancelled = false
    async function loadMeta() {
      try {
        const [regionsData, industriesData] = await Promise.all([
          getRegions(),
          getIndustries(),
        ])
        if (cancelled) return
        setRegions(regionsData)
        setIndustries(industriesData)
      } catch {
        if (!cancelled) setMetaError('지역/업종 목록을 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.')
      }
    }
    async function loadIndexStatus() {
      // 두 인덱스 상태는 서로 독립적이라 따로 잡는다 — 한쪽이 실패해도 다른
      // 쪽은 보여줘야 한다. 인덱스 상태는 참고 표시일 뿐이라 실패해도 검색 폼
      // 자체는 그대로 쓸 수 있게 조용히 무시한다.
      try {
        const data = await getDartIndexStatus()
        if (!cancelled) setDartIndexStatus(data)
      } catch {
        /* 무시 */
      }
      try {
        const data = await getFscFinancialStatus()
        if (!cancelled) setFinancialStatStatus(data)
      } catch {
        /* 무시 */
      }
    }
    loadMeta()
    loadIndexStatus()
    return () => {
      cancelled = true
    }
  }, [])

  // 시도를 고르기 전에는 지역 조건이 없어 dart_corp_index 전체(약 12만 건)를
  // 훑게 되므로 호출하지 않는다 — sido가 있어야 백엔드가 SQL WHERE로 먼저
  // 좁힌다(app/core/dart_corp_index.py::filter_local_candidates).
  useEffect(() => {
    if (debouncedSido.length === 0) {
      setPreview(null)
      return
    }
    let cancelled = false
    getCandidatesPreview({
      region: { sido: debouncedSido, sigungu_by_sido: debouncedSigunguBySido },
      industry: debouncedIndustryCodes,
    })
      .then((data) => {
        if (!cancelled) setPreview(data)
      })
      .catch(() => {
        if (!cancelled) setPreview(null)
      })
    return () => {
      cancelled = true
    }
  }, [debouncedSido, debouncedSigunguBySido, debouncedIndustryCodes])

  async function handleSubmit() {
    setSubmitting(true)
    try {
      // revenue/total_assets는 **키 자체를 보내지 않는다**(§4-13-A) — 백엔드가
      // default_factory로 무제한 조건을 채우므로 파이프라인은 그대로 동작한다.
      const job = await createJob({
        name: name.trim() ? name.trim() : null,
        region: { sido, sigungu_by_sido: sigunguBySido },
        industry: industryCodes,
        history_years: 4,
      })
      notifications.show({ color: 'green', message: `후보 확정 작업(#${job.id})을 시작했습니다.` })
      navigate('/jobs')
    } catch {
      notifications.show({ color: 'red', message: '수집 작업 생성에 실패했습니다.' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Stack maw={960} mx="auto">
      <Title order={2}>검색 조건</Title>
      <Text size="sm" c="dimmed">
        1단계: 먼저 지역·업종 조건으로 후보 회사를 찾습니다.
        <br />
        2단계: 후보 목록을 확인한 뒤, DART 재무제표(다년치)를 수집합니다.
        <br />
        매출액·총자산은 감사보고서 원문을 열기 전에는 알 수 없어 검색 조건에서 받지 않습니다 —
        수집이 끝난 뒤 <b>결과 화면의 매출액·총자산 컬럼 필터</b>에서 실제 파싱된 값으로
        범위를 지정하세요.
      </Text>
      {metaError && (
        <Alert color="red" title="목록 로딩 실패">
          {metaError}
        </Alert>
      )}
      <IndexStatusNote dartIndex={dartIndexStatus} financialStat={financialStatStatus} />

      <Paper withBorder p="md">
        <Stack>
          <TextInput
            label="작업 이름 (선택)"
            placeholder="예: 경남 제조업 2025"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
          />
        </Stack>
      </Paper>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md" verticalSpacing="md">
        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            지역
          </Title>
          <RegionSelect
            regions={regions}
            sido={sido}
            sigunguBySido={sigunguBySido}
            onSidoChange={setSido}
            onSigunguBySidoChange={setSigunguBySido}
          />
        </Paper>

        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            업종 (미선택 시 전체)
          </Title>
          {/* M8 이전에는 중분류를 골라도 실제로는 대분류 전체가 통과했다
              (금융위 업종 텍스트와 문자열 매칭했기 때문). DART가 부여한
              업종코드로 바뀌어 이제 중분류·소분류가 그대로 정확히 걸린다. */}
          <Text size="sm" c="dimmed" mb="xs">
            대분류 → 중분류 → 소분류 순으로 좁힐 수 있습니다. 상위 분류를 고르면
            그 아래 전부가 포함됩니다.
          </Text>
          <IndustryTreeSelect
            industries={industries}
            selected={industryCodes}
            onChange={setIndustryCodes}
          />
          {industryCodes.length > 0 && (
            <Text size="sm" c="dimmed" mt={4}>
              선택됨: {industryCodes.length}건
            </Text>
          )}
        </Paper>
      </SimpleGrid>

      {/* 예상 규모 안내. 후보 확정(1단계) 자체는 로컬 DB 쿼리뿐이라 규모와
          무관하게 즉시 끝난다 — 여기서 경고하는 것은 그 다음 단계인 재무정보
          수집이 DART 일일 한도에 걸려 여러 날로 나뉠 수 있다는 점이다.
          (M8 3단계 이전에는 이 경고가 data.go.kr 쿼터 기준이었다.) */}
      {preview && (
        <Alert color={preview.exceeds_daily_quota ? 'yellow' : 'blue'} variant="light">
          예상 후보 수: 약 {preview.candidate_count.toLocaleString()}개사
          {preview.exceeds_daily_quota
            ? ` — 후보 확정(1단계)은 바로 끝나지만, 이어지는 재무정보 수집(2단계)은 DART 일일 호출 한도(하루 약 ${preview.daily_quota_assumed.toLocaleString()}개사 분량)를 넘어 약 ${preview.estimated_days}일에 걸쳐 나눠 진행됩니다. 한도에 걸리면 작업이 자동으로 멈췄다가 다음 날 이어서 진행되며, 결과 정확도에는 영향이 없습니다. 수집이 끝난 회사부터 결과 화면에 쌓이고, 매출액·총자산은 결과 화면에서 실제 파싱값 기준으로 직접 좁혀 볼 수 있습니다. 더 빨리 끝내려면 시군구나 업종으로 범위를 좁히세요.`
            : ' — 재무정보 수집까지 하루 안에 끝낼 수 있는 규모입니다.'}
        </Alert>
      )}

      <Group justify="flex-end">
        <Button
          size="md"
          loading={submitting}
          disabled={dartIndexStatus?.row_count === 0}
          onClick={handleSubmit}
        >
          후보 확정 시작
        </Button>
      </Group>
    </Stack>
  )
}
