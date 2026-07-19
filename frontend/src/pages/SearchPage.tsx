import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  NumberInput,
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
import FscIndexStatusNote from '../components/FscIndexStatusNote'
import { getCandidatesPreview, getFscIndexStatus, getIndustries, getRegions } from '../api/meta'
import { createJob } from '../api/jobs'
import type { CandidatesPreviewResponse, FscIndexStatus, IndustryMeta, RegionMeta } from '../types'

const EOK = 100_000_000 // 1억원 = 100,000,000원
const MAX_EOK = 1_000_000 // 최대 미입력 시 기본 상한: 1,000,000억원(=100조원)

export default function SearchPage() {
  const navigate = useNavigate()

  const [regions, setRegions] = useState<RegionMeta[]>([])
  const [industries, setIndustries] = useState<IndustryMeta[]>([])
  const [fscIndexStatus, setFscIndexStatus] = useState<FscIndexStatus | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [sido, setSido] = useState<string | null>(null)
  const [sigungu, setSigungu] = useState<string[]>([])
  const [minRevenueEok, setMinRevenueEok] = useState<number | ''>('')
  const [maxRevenueEok, setMaxRevenueEok] = useState<number | ''>('')
  const [minAssetsEok, setMinAssetsEok] = useState<number | ''>('')
  const [maxAssetsEok, setMaxAssetsEok] = useState<number | ''>('')
  const [industryCodes, setIndustryCodes] = useState<string[]>([])

  const [submitting, setSubmitting] = useState(false)

  const [preview, setPreview] = useState<CandidatesPreviewResponse | null>(null)
  const [debouncedSido] = useDebouncedValue(sido, 400)
  const [debouncedSigungu] = useDebouncedValue(sigungu, 400)
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
    async function loadFscIndexStatus() {
      try {
        const data = await getFscIndexStatus()
        if (!cancelled) setFscIndexStatus(data)
      } catch {
        // FSC 인덱스 상태는 참고용 표시일 뿐이라 실패해도 검색 폼 자체는 그대로 쓸 수 있게 조용히 무시한다.
      }
    }
    loadMeta()
    loadFscIndexStatus()
    return () => {
      cancelled = true
    }
  }, [])

  // 시도를 고르기 전에는 지역 조건이 없어 fsc_corp_index 전체(약 63만 건)를
  // 훑게 되므로 호출하지 않는다 — sido가 있어야 백엔드가 SQL WHERE로 먼저
  // 좁힌다(app/core/fsc_index.py::filter_local_candidates).
  useEffect(() => {
    if (!debouncedSido) {
      setPreview(null)
      return
    }
    let cancelled = false
    getCandidatesPreview({
      region: { sido: debouncedSido, sigungu: debouncedSigungu },
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
  }, [debouncedSido, debouncedSigungu, debouncedIndustryCodes])

  async function handleSubmit() {
    setSubmitting(true)
    try {
      const job = await createJob({
        name: name.trim() ? name.trim() : null,
        region: { sido, sigungu },
        revenue: {
          min_krw: minRevenueEok === '' ? 0 : Math.round(minRevenueEok * EOK),
          max_krw: maxRevenueEok === '' ? MAX_EOK * EOK : Math.round(maxRevenueEok * EOK),
        },
        total_assets: {
          min_krw: minAssetsEok === '' ? 0 : Math.round(minAssetsEok * EOK),
          max_krw: maxAssetsEok === '' ? MAX_EOK * EOK : Math.round(maxAssetsEok * EOK),
        },
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
        1단계: 먼저 지역·매출액·총자산·업종 조건으로 후보 회사를 찾습니다.
        <br />
        2단계: 후보 목록을 확인한 뒤, DART 재무제표(다년치)를 수집합니다.
      </Text>
      {metaError && (
        <Alert color="red" title="목록 로딩 실패">
          {metaError}
        </Alert>
      )}
      <FscIndexStatusNote status={fscIndexStatus} />

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
            sigungu={sigungu}
            onSidoChange={setSido}
            onSigunguChange={setSigungu}
          />
        </Paper>

        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            업종 (미선택 시 전체)
          </Title>
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

        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            매출액 범위 (억원)
          </Title>
          <Group grow>
            <NumberInput
              label="최소"
              placeholder="예: 60"
              min={0}
              value={minRevenueEok}
              onChange={(v) => setMinRevenueEok(v === '' ? '' : Number(v))}
            />
            <NumberInput
              label="최대"
              placeholder="예: 150"
              min={0}
              value={maxRevenueEok}
              onChange={(v) => setMaxRevenueEok(v === '' ? '' : Number(v))}
            />
          </Group>
        </Paper>

        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            총자산 범위 (억원)
          </Title>
          <Group grow>
            <NumberInput
              label="최소"
              placeholder="예: 30"
              min={0}
              value={minAssetsEok}
              onChange={(v) => setMinAssetsEok(v === '' ? '' : Number(v))}
            />
            <NumberInput
              label="최대"
              placeholder="예: 300"
              min={0}
              value={maxAssetsEok}
              onChange={(v) => setMaxAssetsEok(v === '' ? '' : Number(v))}
            />
          </Group>
        </Paper>
      </SimpleGrid>

      {preview && (
        <Alert color={preview.exceeds_daily_quota ? 'yellow' : 'blue'} variant="light">
          예상 후보 수: 약 {preview.candidate_count.toLocaleString()}개사
          {preview.exceeds_daily_quota
            ? ` — data.go.kr 일일 조회 한도(약 ${preview.daily_quota_assumed.toLocaleString()}건)를 넘어 매출액·총자산 사전 확인이 약 ${preview.estimated_days}일에 걸쳐 나눠 진행될 수 있습니다. 최종 결과 정확도에는 영향이 없습니다(재무제표 원문으로 항상 다시 확인합니다).`
            : ' — 하루 안에 매출액·총자산 사전 확인까지 끝낼 수 있는 규모입니다.'}
        </Alert>
      )}

      <Group justify="flex-end">
        <Button
          size="md"
          loading={submitting}
          disabled={fscIndexStatus?.row_count === 0}
          onClick={handleSubmit}
        >
          후보 확정 시작
        </Button>
      </Group>
    </Stack>
  )
}
