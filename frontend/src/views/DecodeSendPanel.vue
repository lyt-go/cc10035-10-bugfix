<template>
  <section class="decode-panel" data-testid="decode-panel">
    <header class="decode-head">
      <div>
        <h3>报文解码 · 送测确认</h3>
        <p class="page-desc">
          先用解码规则预览报文，再由操作人确认一种合法解释生成送测凭据；
          路由服务只消费这份确认结果，不再自行把字段拼成十六进制文本。
        </p>
      </div>
      <span v-if="preview?.rule_version" class="decode-version" data-testid="decode-rule-version">
        规则版本 {{ preview.rule_version }}
      </span>
    </header>

    <form class="decode-form" @submit.prevent="loadPreview">
      <label>
        <span>解码规则</span>
        <select v-model="selectedRuleId" data-testid="decode-rule-select">
          <option v-for="rule in decodableRules" :key="String(rule.id)" :value="String(rule.规则编号)">
            {{ rule.规则编号 }} · {{ rule.数据类型 }} / {{ rule.字节序 }}
          </option>
        </select>
      </label>
      <label>
        <span>原始报文 (hex)</span>
        <input v-model="rawHex" data-testid="decode-raw-hex" placeholder="例如 FEFFFFFF" />
      </label>
      <label>
        <span>送测源 Topic</span>
        <input v-model="topic" data-testid="decode-topic" placeholder="iot/telemetry/temp" />
      </label>
      <button class="btn primary" data-testid="decode-preview" type="submit" :disabled="busy">
        预览解析
      </button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="decode-error">{{ errorMessage }}</p>
    <p v-if="infoMessage" class="decode-info" data-testid="decode-info">{{ infoMessage }}</p>

    <template v-if="preview">
      <div v-if="!preview.ok" class="decode-error-card" data-testid="decode-preview-error">
        报文未能解码：{{ preview.message }}
      </div>

      <div v-else class="decode-preview-card" data-testid="decode-preview-card">
        <div class="decode-preview-main">
          <span class="decode-label">预览值</span>
          <strong class="decode-value" data-testid="decode-preview-value">{{ preview.value_text }}</strong>
          <span class="decode-type" data-testid="decode-preview-type">
            {{ preview.value_type }} · {{ preview.data_type }} · {{ preview.byte_order }}
          </span>
          <span class="decode-hex">字段字节 {{ preview.field_hex }} / 原始报文 {{ preview.raw_hex }}</span>
        </div>

        <div class="decode-interpretations">
          <span class="decode-label">同一段字节的合法解释（操作人确认后才可送测）</span>
          <label
            v-for="item in preview.interpretations"
            :key="item.data_type"
            class="decode-choice"
            :data-testid="`decode-interpretation-${item.data_type}`"
          >
            <input v-model="confirmedType" type="radio" :value="item.data_type" />
            <span class="decode-choice-value">{{ item.label }}</span>
            <span class="decode-choice-meta">
              {{ item.value_type }} · {{ item.byte_order }} · {{ item.field_hex }}
            </span>
          </label>
        </div>

        <div class="decode-actions">
          <button
            class="btn primary"
            data-testid="decode-confirm"
            type="button"
            :disabled="busy || !confirmedType"
            @click="confirmCredential"
          >
            确认所选解释并生成凭据
          </button>
          <button
            class="btn ghost"
            data-testid="decode-simulate-conflict"
            type="button"
            :disabled="busy || !selectedRuleId"
            @click="simulateOtherWindow"
          >
            模拟另一窗口把规则改为 uint32
          </button>
        </div>
      </div>
    </template>

    <form v-if="selectedRuleId" class="decode-rule-editor" @submit.prevent="applyRuleUpdate">
      <span class="decode-label">规则维护（另一窗口也可以从这里改类型）</span>
      <label>
        <span>数据类型</span>
        <select v-model="ruleDraft.data_type" data-testid="decode-rule-type">
          <option value="int32">int32</option>
          <option value="uint32">uint32</option>
          <option value="float64">float64</option>
          <option value="hex">hex</option>
        </select>
      </label>
      <label>
        <span>字节序</span>
        <select v-model="ruleDraft.byte_order" data-testid="decode-rule-byte-order">
          <option value="little">little</option>
          <option value="big">big</option>
        </select>
      </label>
      <label>
        <span>字段偏移</span>
        <input v-model="ruleDraft.offset" data-testid="decode-rule-offset" />
      </label>
      <label>
        <span>字段长度</span>
        <input v-model="ruleDraft.length" data-testid="decode-rule-length" />
      </label>
      <button class="btn" data-testid="decode-rule-apply" type="submit" :disabled="busy">
        保存规则
      </button>
    </form>

    <article v-if="submission" class="decode-credential" data-testid="decode-credential">
      <header class="decode-credential-head">
        <strong>送测凭据 {{ submission.凭据编号 }}</strong>
        <span class="decode-tag" :data-status="submission.status">{{ submission.status }}</span>
      </header>

      <dl class="decode-grid">
        <dt>确认值</dt>
        <dd data-testid="decode-confirmed-label">{{ submission.confirmed.label }}</dd>
        <dt>确认类型</dt>
        <dd data-testid="decode-confirmed-type">{{ submission.confirmed_type }}</dd>
        <dt>确认时版本</dt>
        <dd>{{ submission.confirmed_version }}</dd>
        <dt>当前规则版本</dt>
        <dd>{{ submission.current_version }}</dd>
        <dt>当前规则解释</dt>
        <dd data-testid="decode-current-label">
          {{ submission.current_interpretation?.label ?? '当前规则无法解释该字段' }}
        </dd>
        <dt>报文 / Topic</dt>
        <dd>{{ submission.raw_hex }} → {{ submission.topic }}</dd>
      </dl>

      <p v-if="submission.conflict" class="decode-conflict" data-testid="decode-conflict">
        规则已被其他窗口修改：{{ submission.conflict_reason }}。
        本次申请 {{ submission.凭据编号 }} 仍然保留，确认过的
        {{ submission.confirmed.label }} 依然可用；当前规则也能给出
        {{ submission.current_interpretation?.label ?? '其他解释' }}。
      </p>

      <p v-if="submission.issues.length" class="decode-issue" data-testid="decode-issues">
        校验未通过：{{ submission.issues.join('；') }}（报文留在本页，可修正后重试）
      </p>

      <div class="decode-actions">
        <button
          class="btn primary"
          data-testid="decode-send"
          type="button"
          :disabled="busy"
          @click="sendConfirmed()"
        >
          按已确认 {{ submission.confirmed.label }} 送测
        </button>
        <button
          v-if="submission.current_interpretation
            && submission.current_interpretation.data_type !== submission.confirmed_type"
          class="btn"
          :data-testid="`decode-send-as-${submission.current_interpretation.data_type}`"
          type="button"
          :disabled="busy"
          @click="sendConfirmed(submission.current_interpretation!.data_type)"
        >
          按新确认 {{ submission.current_interpretation.label }} 送测
        </button>
        <button
          class="btn ghost"
          data-testid="decode-refresh-receipt"
          type="button"
          :disabled="busy || !submission.route_receipt"
          @click="loadReceipt(submission.凭据编号)"
        >
          读取服务端回执
        </button>
      </div>

      <p v-if="submission.route_receipt" class="decode-sent" data-testid="decode-send-message">
        {{ sendMessage }}
      </p>
    </article>

    <article v-if="receipt" class="decode-receipt" data-testid="decode-receipt">
      <header class="decode-credential-head">
        <strong>路由详情 · 服务端回执 {{ receipt.回执编号 }}</strong>
        <span class="decode-tag" data-status="已送测">
          {{ receipt.source_topic }} → {{ receipt.target_topic }}
        </span>
      </header>
      <dl class="decode-grid">
        <dt>流水号</dt>
        <dd data-testid="decode-receipt-flow">{{ receipt.流水号 }}</dd>
        <dt>发送类型</dt>
        <dd data-testid="decode-receipt-type">{{ receipt.sent_type }}（{{ receipt.sent_value_type }}）</dd>
        <dt>发送值</dt>
        <dd data-testid="decode-receipt-value">{{ receipt.sent_value_text }}</dd>
        <dt>发送字节序</dt>
        <dd>{{ receipt.sent_byte_order }}</dd>
        <dt>字段字节</dt>
        <dd>{{ receipt.sent_field_hex }}</dd>
        <dt>载荷形态</dt>
        <dd data-testid="decode-receipt-payload">
          {{ receipt.payload_kind }} · 十六进制文本：{{ receipt.contains_hex_text ? '是' : '否' }}
        </dd>
        <dt>路由流水数</dt>
        <dd data-testid="decode-receipt-count">{{ receipt.route_count }}</dd>
        <dt>回执时间</dt>
        <dd>{{ receipt.routed_at }}</dd>
      </dl>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  confirmDecode,
  fetchRouteReceipt,
  latestSubmission,
  listEntries,
  previewDecode,
  sendDecode,
  updateDecodeRule,
} from '@/api/backend-service'
import type { DecodePreview, Interpretation, RouteReceipt, Submission } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ rows: EntryRow[] }>()
const emit = defineEmits<{ refresh: [] }>()

const selectedRuleId = ref('')
const rawHex = ref('FEFFFFFF')
const topic = ref('iot/telemetry/temp')
const preview = ref<DecodePreview | null>(null)
const confirmedType = ref('')
const submission = ref<Submission | null>(null)
const receipt = ref<RouteReceipt | null>(null)
const sendMessage = ref('')
const errorMessage = ref('')
const infoMessage = ref('')
const busy = ref(false)
const ruleDraft = ref({ data_type: 'int32', byte_order: 'little', offset: '0', length: '4' })

const decodableRules = computed(() =>
  props.rows.filter((row) => String(row.status) === '已启用'),
)

const selectedRule = computed(() =>
  decodableRules.value.find((row) => String(row.规则编号) === selectedRuleId.value) ?? null,
)

watch(
  decodableRules,
  (rules) => {
    if (!selectedRuleId.value && rules.length) {
      selectedRuleId.value = String(rules[0].规则编号)
    }
  },
  { immediate: true },
)

// 规则记录变化时同步维护表单，但不会覆盖操作人已经确认的凭据。
watch(
  selectedRule,
  (rule) => {
    if (!rule) return
    ruleDraft.value = {
      data_type: String(rule.数据类型 ?? 'int32'),
      byte_order: String(rule.字节序 ?? 'little'),
      offset: String(rule.字段偏移 ?? '0'),
      length: String(rule.字段长度 ?? '4'),
    }
  },
  { immediate: true },
)

async function refreshPreview() {
  if (!selectedRuleId.value) return
  const refreshed = await previewDecode(selectedRuleId.value, rawHex.value)
  preview.value = refreshed
  syncSelection(refreshed)
}

async function applyRuleUpdate() {
  if (!selectedRuleId.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await updateDecodeRule(selectedRuleId.value, {
      数据类型: ruleDraft.value.data_type,
      字节序: ruleDraft.value.byte_order,
      字段偏移: ruleDraft.value.offset,
      字段长度: ruleDraft.value.length,
    })
    if (!result.ok) throw new Error(result.message)
    await refreshRuleRows()
    await refreshPreview()
    infoMessage.value = '规则已更新；若此前已确认过凭据，再次确认即可看到版本冲突与可用解释。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则更新失败'
  } finally {
    busy.value = false
  }
}

/** 规则列表变化后重新拉一遍模块行，让下拉里的类型/字节序跟上最新规则。 */
async function refreshRuleRows() {
  try {
    const payload = await listEntries('msg_decode')
    props.rows.splice(0, props.rows.length, ...payload.items)
    emit('refresh')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解码规则列表刷新失败'
  }
}

function applyInterpretation(item: Interpretation | undefined) {
  if (item) confirmedType.value = item.data_type
}

/** 规则刷新后仍优先保留操作人已经确认过的解释，避免版本冲突时被悄悄改选。 */
function syncSelection(result: DecodePreview) {
  const items = result.interpretations ?? []
  if (!items.length) return
  const kept = submission.value
    && submission.value.raw_hex === result.raw_hex
    && items.some((item) => item.data_type === submission.value?.confirmed_type)
  if (kept) {
    confirmedType.value = submission.value!.confirmed_type
    return
  }
  applyInterpretation(items.find((item) => item.data_type === result.data_type) ?? items[0])
}

async function loadPreview() {
  if (!selectedRuleId.value) {
    errorMessage.value = '请先选择一条已启用的解码规则'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await previewDecode(selectedRuleId.value, rawHex.value)
    preview.value = result
    if (result.interpretations?.length) {
      syncSelection(result)
      // 新报文预览后旧的送测凭据不再对应当前报文，避免混淆。
      if (submission.value && submission.value.raw_hex !== result.raw_hex) {
        submission.value = null
        receipt.value = null
        sendMessage.value = ''
      }
    }
    if (!result.ok) errorMessage.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '预览失败'
  } finally {
    busy.value = false
  }
}

async function confirmCredential() {
  if (!preview.value?.rule_version || !confirmedType.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await confirmDecode({
      ruleId: selectedRuleId.value,
      rawHex: preview.value.raw_hex ?? rawHex.value,
      dataType: confirmedType.value,
      topic: topic.value,
      expectedVersion: preview.value.rule_version,
    })
    if (!result.ok || !result.submission) throw new Error(result.message)
    submission.value = result.submission
    receipt.value = result.submission.route_receipt
    sendMessage.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认失败'
  } finally {
    busy.value = false
  }
}

async function simulateOtherWindow() {
  if (!selectedRuleId.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await updateDecodeRule(selectedRuleId.value, { 数据类型: 'uint32', 字节序: 'little' })
    if (!result.ok) throw new Error(result.message)
    // 刷新预览拿到新版本号，但不覆盖操作人已确认的凭据。
    await refreshRuleRows()
    await refreshPreview()
    infoMessage.value = '另一窗口已把规则改为 uint32；再次确认即可看到版本冲突与可用解释。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则修改失败'
  } finally {
    busy.value = false
  }
}

async function sendConfirmed(dataType?: string) {
  if (!submission.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await sendDecode(submission.value.凭据编号, dataType)
    if (result.submission) submission.value = result.submission
    if (!result.ok || !result.receipt) throw new Error(result.message)
    receipt.value = result.receipt
    sendMessage.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '送测失败'
  } finally {
    busy.value = false
  }
}

async function loadReceipt(credential: string) {
  busy.value = true
  errorMessage.value = ''
  try {
    receipt.value = await fetchRouteReceipt(credential)
    sendMessage.value = '已读取服务端回执'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执读取失败'
  } finally {
    busy.value = false
  }
}

defineExpose({ loadPreview, confirmCredential, simulateOtherWindow, sendConfirmed, loadReceipt })

onMounted(async () => {
  // 刷新页面后找回最近一份申请，凭据编号与服务端回执都还能核对。
  try {
    const latest = await latestSubmission()
    if (!latest) return
    submission.value = latest
    receipt.value = latest.route_receipt
    selectedRuleId.value = latest.规则编号
    rawHex.value = latest.raw_hex
    topic.value = latest.topic
    if (!preview.value) await refreshPreview()
  } catch {
    // 没有可找回的申请时保持默认预览流程。
  }
})
</script>
