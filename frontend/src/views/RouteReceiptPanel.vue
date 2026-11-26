<template>
  <section class="receipt-panel" data-testid="receipt-panel">
    <header class="decode-head">
      <div>
        <h3>路由详情 · 服务端回执</h3>
        <p class="page-desc">
          回执由路由服务在送测成功后写入，记录实际发送的类型、数值与字节序，
          用于核对“预览看到的”与“真正发出的”是否一致。
        </p>
      </div>
      <button class="btn" data-testid="receipt-refresh" type="button" :disabled="busy" @click="reload">
        刷新回执
      </button>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="receipt-error">{{ errorMessage }}</p>
    <p v-else-if="!receipts.length" class="page-desc" data-testid="receipt-empty">
      还没有路由回执：先在报文解码页确认解释并送测。
    </p>

    <table v-else class="data-table">
      <thead>
        <tr>
          <th>流水号</th>
          <th>凭据编号</th>
          <th>源Topic → 目标Topic</th>
          <th>发送类型</th>
          <th>发送值</th>
          <th>字节序</th>
          <th>载荷</th>
          <th>流水数</th>
          <th>回执时间</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="receipt in receipts"
          :key="receipt.流水号"
          :data-testid="`receipt-row-${receipt.凭据编号}`"
          :data-sent-type="receipt.sent_type"
          :data-sent-value="String(receipt.sent_value)"
        >
          <td>{{ receipt.流水号 }}</td>
          <td>{{ receipt.凭据编号 }}</td>
          <td>{{ receipt.source_topic }} → {{ receipt.target_topic }}</td>
          <td>{{ receipt.sent_type }}（{{ receipt.sent_value_type }}）</td>
          <td class="receipt-value">{{ receipt.sent_value_text }}</td>
          <td>{{ receipt.sent_byte_order }}</td>
          <td>
            {{ receipt.payload_kind }} · 十六进制文本：{{ receipt.contains_hex_text ? '是' : '否' }}
          </td>
          <td>{{ receipt.route_count }}</td>
          <td>{{ receipt.routed_at }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import type { RouteReceipt } from '@/api/backend-service'

const receipts = ref<RouteReceipt[]>([])
const errorMessage = ref('')
const busy = ref(false)

async function reload() {
  busy.value = true
  errorMessage.value = ''
  try {
    const payload = await fetchJson<{ data: RouteReceipt[] }>('/api/cross/flow2/receipts')
    receipts.value = payload.data ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路由回执读取失败'
  } finally {
    busy.value = false
  }
}

onMounted(reload)
</script>
