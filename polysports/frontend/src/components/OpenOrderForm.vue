<script setup lang="ts">
import { computed } from 'vue'
import WalletDiagnosticSteps from './WalletDiagnosticSteps.vue'
import { formatDateTime } from '../utils/format'

const props=defineProps<{ wallets: any[]; book: any; context: any; orderSize: string; busy: boolean; estimatedNotional: string; pending: any; previousPrice?: string; marketUrl?: string; tradingLimits?: { order_limit_percent?: string | null; order_limit_floor_usd?: string; remaining_daily_usd?: string | null; resets_at?: string }; walletCheck?: {loading:boolean;data?:any;error?:string} }>()
const wallet = defineModel<string>('wallet', { required: true })
const side = defineModel<string>('side', { required: true })
const price = defineModel<string>('price', { required: true })
const size = defineModel<string>('size', { required: true })
const budget = defineModel<string>('budget', { required: true })
const inputMode = defineModel<'amount' | 'shares'>('inputMode', { required: true })
const emit = defineEmits<{ submit: []; refresh: []; checkWallet: [] }>()
type Level={price:string;size:string}
const levels=(side:'bids'|'asks') => ([...(props.book?.[side] ?? [])] as Level[])
  .filter(level=>Number(level.size)>0)
  .sort((a,b)=>side==='asks'?Number(a.price)-Number(b.price):Number(b.price)-Number(a.price))
const bids=computed(()=>levels('bids'))
const asks=computed(()=>levels('asks'))
const executionEstimate=computed(()=>{
  const limit=Number(price.value)
  if(!props.book||!(limit>0&&limit<1))return null
  const eligible=side.value==='BUY'?asks.value.filter(level=>Number(level.price)<=limit)
    :bids.value.filter(level=>Number(level.price)>=limit)
  const size=Number(props.orderSize)
  const depth=eligible.reduce((total,level)=>total+Number(level.size),0)
  if(!Number.isFinite(size)||size<=0)return {depth, filled:0, remaining:0, average:null}
  let remaining=size
  let notional=0
  for(const level of eligible){
    const take=Math.min(Number(level.size),remaining)
    notional+=take*Number(level.price)
    remaining-=take
    if(remaining<=0)break
  }
  const filled=size-remaining
  return {depth, filled, remaining, average:filled>0?notional/filled:null}
})
const remainingAfterOrder=computed(()=>{
  const remaining=props.tradingLimits?.remaining_daily_usd
  const amount=Number(price.value)*Number(props.orderSize)
  if(remaining==null||!Number.isFinite(amount)||amount<=0)return null
  return Math.max(0,Number(remaining)-amount).toLocaleString('zh-CN',{maximumFractionDigits:6})
})
const formatShares=(value:number)=>value.toLocaleString('zh-CN',{maximumFractionDigits:6})
</script>

<template>
  <section class="welcome-card detail">
    <p v-if="context">{{ context.home }} 对 {{ context.away }} · {{ context.market_question }} · 选项：{{ context.outcome_label }} · {{ formatDateTime(context.starts_at) }} UTC+8</p>
    <a v-if="marketUrl" :href="marketUrl" target="_blank" rel="noopener noreferrer">查看官方盘口与结算规则 ↗</a>
    <p v-if="context" class="id-text">资产 ID：{{ context.asset_id }} · 协议：{{ context.protocol_version }}</p>
    <p v-if="book">价格增量 {{ book.tick_size }} · 最小份额 {{ book.min_order_size }} · 盘口时间 {{ formatDateTime(book.observed_at) }} UTC+8</p>
    <p v-else>真实盘口不可用。</p>
    <div class="order-book-summary">
      <span v-if="book">买一 {{ bids[0]?.price ?? '无' }} · 卖一 {{ asks[0]?.price ?? '无' }}</span>
      <button type="button" class="text-button" :disabled="busy" @click="emit('refresh')">刷新盘口</button>
    </div>
    <p v-if="previousPrice">上次提交限价 {{ previousPrice }}。请核对最新盘口后再提交。</p>
    <details v-if="book">
      <summary>订单簿前五档（价格 · 份额）</summary>
      <div class="order-book-levels">
        <div><strong>买盘</strong><p v-for="(level,index) in bids.slice(0,5)" :key="index">{{ level.price }} · {{ level.size }}</p></div>
        <div><strong>卖盘</strong><p v-for="(level,index) in asks.slice(0,5)" :key="index">{{ level.price }} · {{ level.size }}</p></div>
      </div>
    </details>
    <form @submit.prevent="emit('submit')">
      <label for="wallet">钱包</label>
      <select id="wallet" v-model="wallet" required><option v-for="w in wallets" :key="w.id" :value="w.id">{{ w.label }} · {{ w.wallet_address }}</option></select>
      <div class="order-book-summary">
        <button type="button" class="text-button" :disabled="!wallet || walletCheck?.loading" @click="emit('checkWallet')">检查钱包余额与授权</button>
      </div>
      <p v-if="walletCheck" role="status">
        <template v-if="walletCheck.loading">正在检查钱包…</template>
        <template v-else-if="walletCheck.error">{{ walletCheck.error }}</template>
        <template v-else-if="walletCheck.data">
          可用余额 {{ walletCheck.data.collateral_balance ?? '未知' }} pUSD · 授权 {{ walletCheck.data.allowance_status === 'unknown' ? '未检查' : walletCheck.data.allowance_status }}
          <span v-if="walletCheck.data.checked_at"> · {{ formatDateTime(walletCheck.data.checked_at) }} UTC+8</span>
          <span v-if="walletCheck.data.diagnostic"> · {{ walletCheck.data.diagnostic.message }}</span>
        </template>
      </p>
      <WalletDiagnosticSteps :steps="walletCheck?.data?.steps" />
      <label for="side">方向</label>
      <select id="side" v-model="side"><option value="BUY">买入</option><option value="SELL">卖出</option></select>
      <label for="price">限价</label>
      <input id="price" v-model="price" inputmode="decimal" required>
      <template v-if="side === 'BUY'">
        <label for="input-mode">输入方式</label>
        <select id="input-mode" v-model="inputMode"><option value="amount">购买金额（pUSD）</option><option value="shares">购买份额</option></select>
      </template>
      <template v-if="side === 'BUY' && inputMode === 'amount'">
        <label for="budget">购买金额上限（pUSD）</label>
        <input id="budget" v-model="budget" inputmode="decimal" required>
        <p>按限价计算的份额：{{ orderSize || '请填写有效金额与限价' }}；份额向下取两位小数，手续费另计。</p>
      </template>
      <template v-else>
        <label for="size">份额</label>
        <input id="size" v-model="size" inputmode="decimal" required>
      </template>
      <p v-if="executionEstimate">限价内可见深度 {{ formatShares(executionEstimate.depth) }} 份<span v-if="executionEstimate.remaining>0"> · 当前深度低于所填份额</span></p>
      <p v-if="executionEstimate && Number(orderSize)>0">
        预计立即成交 {{ formatShares(executionEstimate.filled) }} 份
        <span v-if="executionEstimate.average!==null"> · 预估均价 {{ executionEstimate.average.toFixed(6) }} pUSD/份</span>
        <span v-if="executionEstimate.remaining>0"> · 预计剩余挂单 {{ formatShares(executionEstimate.remaining) }} 份</span>
      </p>
      <p v-if="side==='BUY' && tradingLimits?.order_limit_percent">
        单笔买入上限为可用余额的 {{ tradingLimits.order_limit_percent }}%，最低按 {{ tradingLimits.order_limit_floor_usd }} pUSD 计算，以实际可用余额为限。
      </p>
      <p v-if="side==='BUY' && tradingLimits?.remaining_daily_usd!=null">
        今日买入剩额 {{ tradingLimits.remaining_daily_usd }} pUSD
        <span v-if="remainingAfterOrder!==null"> · 提交后预计剩余 {{ remainingAfterOrder }} pUSD</span>
        <span v-if="tradingLimits.resets_at"> · 重置 {{ formatDateTime(tradingLimits.resets_at) }} UTC+8</span>
      </p>
      <p v-if="side==='BUY' && Number(orderSize)>0">命中时预计总返还 {{ orderSize }} pUSD（按全部成交、每份 1 pUSD 估算）</p>
      <p v-if="estimatedNotional">预计限价金额 {{ estimatedNotional }} pUSD；手续费未知，实际成交以交易所记录为准。</p>
      <p>点击「按当前价直接下单」将按当前盘口立即提交真实资金订单，不设二次确认；执行中不代表成交。</p>
      <button class="text-button" :disabled="busy || !!pending || !wallet || !book || !orderSize">按当前价直接下单</button>
    </form>
  </section>
</template>
