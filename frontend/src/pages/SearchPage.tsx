import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { DateInput } from '@mantine/dates'
import { notifications } from '@mantine/notifications'
import RegionSelect from '../components/RegionSelect'
import IndustryTreeSelect from '../components/IndustryTreeSelect'
import { getIndustries, getRegions } from '../api/meta'
import { createJob } from '../api/jobs'
import type { HistoryYears, IndustryMeta, RegionMeta } from '../types'
import { oneYearAgoIso, todayIso, toYyyymmdd } from '../util/date'

const HISTORY_YEARS_OPTIONS: { label: string; value: HistoryYears }[] = [
  { label: '2년', value: 2 },
  { label: '4년', value: 4 },
  { label: '6년', value: 6 },
  { label: '10년', value: 10 },
]

const EOK = 100_000_000 // 1억원 = 100,000,000원

export default function SearchPage() {
  const navigate = useNavigate()

  const [regions, setRegions] = useState<RegionMeta[]>([])
  const [industries, setIndustries] = useState<IndustryMeta[]>([])
  const [metaError, setMetaError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [sido, setSido] = useState<string | null>(null)
  const [sigungu, setSigungu] = useState<string[]>([])
  const [minRevenueEok, setMinRevenueEok] = useState<number | ''>('')
  const [maxRevenueEok, setMaxRevenueEok] = useState<number | ''>('')
  const [industryCodes, setIndustryCodes] = useState<string[]>([])
  const [bgnDe, setBgnDe] = useState<string | null>(oneYearAgoIso())
  const [endDe, setEndDe] = useState<string | null>(todayIso())
  const [historyYears, setHistoryYears] = useState<HistoryYears>(4)

  const [submitting, setSubmitting] = useState(false)

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
    loadMeta()
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSubmit() {
    if (!bgnDe || !endDe) {
      notifications.show({ color: 'red', message: '공시 대상 기간을 입력해 주세요.' })
      return
    }

    setSubmitting(true)
    try {
      const job = await createJob({
        name: name.trim() ? name.trim() : null,
        region: { sido, sigungu },
        revenue: {
          min_krw: minRevenueEok === '' ? null : Math.round(minRevenueEok * EOK),
          max_krw: maxRevenueEok === '' ? null : Math.round(maxRevenueEok * EOK),
        },
        industry: industryCodes,
        period: { bgn_de: toYyyymmdd(bgnDe), end_de: toYyyymmdd(endDe) },
        history_years: historyYears,
      })
      notifications.show({ color: 'green', message: `수집 작업(#${job.id})을 시작했습니다.` })
      navigate('/jobs')
    } catch {
      notifications.show({ color: 'red', message: '수집 작업 생성에 실패했습니다.' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Stack maw={860} mx="auto">
      <Title order={2}>검색 조건</Title>
      {metaError && (
        <Alert color="red" title="목록 로딩 실패">
          {metaError}
        </Alert>
      )}

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
          매출액 범위 (억원, 미입력 시 무제한)
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
          공시 대상 기간
        </Title>
        <Group grow>
          <DateInput
            label="시작일"
            value={bgnDe}
            onChange={setBgnDe}
            valueFormat="YYYY-MM-DD"
          />
          <DateInput
            label="종료일"
            value={endDe}
            onChange={setEndDe}
            valueFormat="YYYY-MM-DD"
          />
        </Group>
      </Paper>

      <Paper withBorder p="md">
        <Title order={4} mb="sm">
          재무 이력 조회 기간
        </Title>
        <Text size="sm" c="dimmed" mb="xs">
          매출액 조건까지 통과한 최종 결과 회사에 한해, 최근 N년치 연도별 재무정보를
          추가로 수집합니다. 감사보고서가 당기·전기 비교식으로 작성되어 짝수 연수만
          선택할 수 있습니다.
        </Text>
        <SegmentedControl
          value={String(historyYears)}
          onChange={(v) => setHistoryYears(Number(v) as HistoryYears)}
          data={HISTORY_YEARS_OPTIONS.map((opt) => ({
            label: opt.label,
            value: String(opt.value),
          }))}
        />
      </Paper>

      <Group justify="flex-end">
        <Button size="md" loading={submitting} onClick={handleSubmit}>
          수집 시작
        </Button>
      </Group>
    </Stack>
  )
}
