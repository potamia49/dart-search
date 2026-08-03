import { Alert, Button, Code, CopyButton, Group, List, Modal, Spoiler, Stack, Text } from '@mantine/core'
import type { GenerateReportResponse } from '../types'

interface ReportResultModalProps {
  result: GenerateReportResponse | null
  onClose: () => void
}

/** §4-12 선택 항목 보고서 생성 결과 안내.
 *
 * 이 기능은 파일을 브라우저로 내려주지 않고 **이 프로그램이 실행 중인 PC의 로컬
 * 폴더**에 저장한다 — 그래서 사용자가 할 수 있는 유일한 다음 행동은 "그 폴더를 여는
 * 것"이고, 화면이 해야 할 일도 **경로를 복사 가능한 형태로 보여주는 것**이 전부다
 * (브라우저는 로컬 탐색기를 열 수 없다).
 *
 * 백엔드 응답의 세 갈래를 **절대 섞지 않는다**:
 * - `skipped` — 재무 이력이 없어(또는 전부 FAILED) **파일 자체가 안 만들어진** 회사.
 *   `generated_count`에 포함되지 않으므로 "부실한 보고서"가 아니라 "미생성"으로 알린다.
 * - `warnings` 중 `result_id != null` — 파일은 있으나 일부 연도가 결측/PARTIAL인 회사.
 *   한 회사가 경고를 여러 개 낼 수 있어 **건수는 고유 회사 수**로 센다.
 * - `warnings` 중 `result_id === null` — 회사와 무관한 요청 단위 안내(사무소 연락처
 *   미설정 등). 회사 목록에 섞이면 "문제 있는 회사"로 오인되므로 따로 뺀다. */
export default function ReportResultModal({ result, onClose }: ReportResultModalProps) {
  const skipped = result?.skipped ?? []
  const warnings = result?.warnings ?? []
  const companyWarnings = warnings.filter((warning) => warning.result_id != null)
  const generalWarnings = warnings.filter((warning) => warning.result_id == null)
  const companyWarningCount = new Set(companyWarnings.map((warning) => warning.result_id)).size
  const nothingGenerated = result !== null && result.generated_count === 0

  return (
    <Modal
      opened={result !== null}
      onClose={onClose}
      title={nothingGenerated ? '생성된 보고서가 없습니다' : '보고서 생성 완료'}
      size="lg"
    >
      {result && (
        <Stack>
          {nothingGenerated ? (
            <Alert color="yellow" title="보고서가 한 건도 만들어지지 않았습니다">
              <Text size="sm">
                선택한 회사가 모두 생성 대상에서 제외되어 저장된 보고서 파일이 없습니다.
                아래 사유를 확인한 뒤, 재무 이력이 있는 회사를 다시 선택해 주세요.
              </Text>
            </Alert>
          ) : (
            <>
              <Text size="sm">
                보고서 <b>{result.generated_count.toLocaleString()}건</b>을 아래 폴더에
                저장했습니다. 같은 폴더에 우편 발송용 <Code>{result.label_file}</Code>도 함께
                만들었습니다.
              </Text>

              <Stack gap="xs">
                <Code block>{result.output_dir}</Code>
                <Group gap="xs">
                  <CopyButton value={result.output_dir} timeout={2000}>
                    {({ copied, copy }) => (
                      <Button size="xs" variant={copied ? 'filled' : 'default'} color={copied ? 'green' : undefined} onClick={copy}>
                        {copied ? '경로를 복사했습니다' : '폴더 경로 복사'}
                      </Button>
                    )}
                  </CopyButton>
                  <Text size="xs" c="dimmed">
                    복사한 경로를 파일 탐색기 주소창에 붙여넣으면 폴더가 열립니다.
                  </Text>
                </Group>
              </Stack>

              <Text size="xs" c="dimmed">
                이 폴더는 <b>지금 이 프로그램이 실행 중인 PC</b>에 있습니다(브라우저 다운로드
                폴더가 아닙니다). 같은 날 다시 생성해도 기존 폴더를 덮어쓰지 않고 새 폴더가
                만들어집니다.
              </Text>
            </>
          )}

          {skipped.length > 0 && (
            <Alert color="orange" title={`생성하지 않은 회사 ${skipped.length.toLocaleString()}건`}>
              <Text size="sm">
                아래 회사는 재무 이력이 없어 <b>보고서 파일이 만들어지지 않았습니다</b>.
                재무정보 수집·파싱 결과를 확인한 뒤 다시 시도하세요.
              </Text>
              <Spoiler maxHeight={96} showLabel="전체 보기" hideLabel="접기" mt="xs">
                <List size="sm" spacing={4}>
                  {skipped.map((item, index) => (
                    <List.Item key={`${item.result_id}-${index}`}>
                      <b>{item.corp_name || `결과 #${item.result_id}`}</b> — {item.reason}
                    </List.Item>
                  ))}
                </List>
              </Spoiler>
            </Alert>
          )}

          {companyWarnings.length > 0 && (
            <Alert color="yellow" title={`내용이 부실한 보고서 ${companyWarningCount.toLocaleString()}건`}>
              <Text size="sm">
                아래 회사는 보고서 파일은 만들어졌지만, 일부 연도의 재무 정보가 없거나 파싱이
                온전하지 않아 재무 분석·차트가 비어 있을 수 있습니다. 발송 전에 내용을
                확인하세요.
              </Text>
              <Spoiler maxHeight={96} showLabel="전체 보기" hideLabel="접기" mt="xs">
                <List size="sm" spacing={4}>
                  {companyWarnings.map((warning, index) => (
                    <List.Item key={`${warning.result_id ?? 'unknown'}-${index}`}>
                      <b>{warning.corp_name ?? `결과 #${warning.result_id ?? '-'}`}</b> —{' '}
                      {warning.message}
                    </List.Item>
                  ))}
                </List>
              </Spoiler>
            </Alert>
          )}

          {generalWarnings.length > 0 && (
            <Alert color="blue" title="안내">
              <List size="sm" spacing={4}>
                {generalWarnings.map((warning, index) => (
                  <List.Item key={`general-${index}`}>{warning.message}</List.Item>
                ))}
              </List>
            </Alert>
          )}

          <Group justify="flex-end">
            <Button onClick={onClose}>확인</Button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}
