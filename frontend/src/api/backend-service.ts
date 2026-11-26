import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/** 报文解码页：预览、送测凭据与服务端路由回执。 */
export type Interpretation = {
  data_type: string
  value: number | string
  value_text: string
  value_type: string
  byte_order: string
  field_offset: number
  field_length: number
  field_hex: string
  label: string
}

export type DecodePreview = {
  ok: boolean
  message: string
  rule_id?: string
  protocol?: string
  rule_version?: string
  data_type?: string
  byte_order?: string
  raw_hex?: string
  field_hex?: string
  parsed_value?: number | string | null
  value_text?: string
  value_type?: string
  interpretations?: Interpretation[]
  checksum_valid?: boolean
}

export type RouteReceipt = {
  id: number
  回执编号: string
  流水号: string
  凭据编号: string
  decode_rule: string
  decode_version: string
  raw_hex: string
  source_topic: string
  target_topic: string
  router_rule: string
  forward_method: string
  sent_type: string
  sent_value: number | string
  sent_value_text: string
  sent_value_type: string
  sent_byte_order: string
  sent_field_hex: string
  payload_kind: string
  contains_hex_text: boolean
  sent_as_hex_text: boolean
  server_receipt: boolean
  route_count: number
  routed_at: string
}

export type Submission = {
  id: number
  凭据编号: string
  规则编号: string
  协议类型: string
  raw_hex: string
  topic: string
  confirmed: Interpretation
  confirmed_type: string
  confirmed_value: number | string
  confirmed_version: string
  current_version: string
  current_interpretation: Interpretation | null
  available_interpretations: Interpretation[]
  conflict: boolean
  conflict_reason: string
  status: string
  issues: string[]
  route_receipt: RouteReceipt | null
  created_at: string
  updated_at: string
}

export type ConfirmResult = {
  ok: boolean
  message: string
  submission: Submission | null
}

export type SendResult = {
  ok: boolean
  message: string
  receipt: RouteReceipt | null
  submission: Submission | null
}

function decodeQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, value)
  }
  return search.toString()
}

export async function previewDecode(
  ruleId: string,
  rawHex: string,
): Promise<DecodePreview> {
  const response = await request(
    `/api/cross/flow2/preview?${decodeQuery({ rule_id: ruleId, raw_hex: rawHex })}`,
    { method: 'POST' },
  )
  return await response.json()
}

export async function confirmDecode(params: {
  ruleId: string
  rawHex: string
  dataType: string
  topic: string
  expectedVersion?: string
}): Promise<ConfirmResult> {
  const response = await request(
    `/api/cross/flow2/confirm?${decodeQuery({
      rule_id: params.ruleId,
      raw_hex: params.rawHex,
      data_type: params.dataType,
      topic: params.topic,
      expected_version: params.expectedVersion,
    })}`,
    { method: 'POST' },
  )
  return await response.json()
}

export async function sendDecode(
  credential: string,
  dataType?: string,
): Promise<SendResult> {
  const response = await request(
    `/api/cross/flow2/send?${decodeQuery({ credential, data_type: dataType })}`,
    { method: 'POST' },
  )
  return await response.json()
}

export async function fetchRouteReceipt(credential: string): Promise<RouteReceipt> {
  const response = await request(`/api/cross/flow2/receipts/${encodeURIComponent(credential)}`)
  if (response.status === 404) throw new Error('该凭据还没有服务端路由回执')
  if (!response.ok) throw new Error('路由回执读取失败')
  return await response.json()
}

/** 送测最新一份凭据：刷新页面后还能找回刚才的申请与回执。 */
export async function latestSubmission(): Promise<Submission | null> {
  const response = await request('/api/cross/flow2/submissions')
  if (!response.ok) return null
  const payload = await response.json()
  const items = (payload.data ?? []) as Submission[]
  return items[0] ?? null
}

/** 模拟另一个窗口修改解码规则，用于观察版本冲突。 */
export async function updateDecodeRule(
  ruleId: string,
  values: Record<string, string>,
): Promise<{ ok: boolean; message: string }> {
  const response = await request(`/api/cross/flow2/rules/${encodeURIComponent(ruleId)}`, {
    method: 'PATCH',
    body: JSON.stringify(values),
  })
  return await response.json()
}
