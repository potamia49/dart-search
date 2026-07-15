import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  NumberInput,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import RegionSelect from '../components/RegionSelect'
import IndustryTreeSelect from '../components/IndustryTreeSelect'
import FscIndexStatusNote from '../components/FscIndexStatusNote'
import { getFscIndexStatus, getIndustries, getRegions } from '../api/meta'
import { createJob } from '../api/jobs'
import type { FscIndexStatus, IndustryMeta, RegionMeta } from '../types'

const EOK = 100_000_000 // 1억원 = 100,000,000원

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

  async function handleSubmit() {
    setSubmitting(true)
    try {
      const job = await createJob({
        name: name.trim() ? name.trim() : null,
        region: { sido, sigungu },
        revenue: {
          min_krw: minRevenueEok === '' ? null : Math.round(minRevenueEok * EOK),
          max_krw: maxRevenueEok === '' ? null : Math.round(maxRevenueEok * EOK),
        },
        total_assets: {
          min_krw: minAssetsEok === '' ? null : Math.round(minAssetsEok * EOK),
          max_krw: maxAssetsEok === '' ? null : Math.round(maxAssetsEok * EOK),
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
    <Stack maw={860} mx="auto">
      <Title order={2}>검색 조건</Title>
      <Text size="sm" c="dimmed">
        지역/매출액/총자산/업종 조건으로 후보 회사를 먼저 확정합니다(Phase 1). 재무제표
        다년치 수집(Phase 2)은 후보 목록을 확인한 뒤 별도로 시작합니다.
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
          총자산 범위 (억원, 미입력 시 무제한)
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

      <Group justify="flex-end">
        <Button size="md" loading={submitting} onClick={handleSubmit}>
          후보 확정 시작
        </Button>
      </Group>
    </Stack>
  )
}
