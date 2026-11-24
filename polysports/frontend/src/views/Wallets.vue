<script setup lang="ts">
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import WalletDiagnosticSteps from '../components/WalletDiagnosticSteps.vue'
import {formatDateTimeSeconds} from '../utils/format'
import {onActivated,onDeactivated,onMounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'
type Wallet={id:string;label:string;wallet_address:string;signer_address:string;wallet_type:string}
const router=useRouter(),menuOpen=ref(false),wallets=ref<Wallet[]>([]),selected=ref<any>(null),message=ref('')
async function api(path:string){const r=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store'});const data=await r.json();if(!r.ok)throw new Error();return data.data}
async function load(){try{wallets.value=(await api('/wallets')).items}catch{message.value='钱包列表加载失败。'}}
const checking=ref(false)
async function status(wallet:Wallet){
  if(checking.value)return
  checking.value=true
  message.value='正在检查钱包状态…'
  try{selected.value=await api('/wallets/'+wallet.id+'/status?fresh=true');message.value=''}
  catch{message.value='钱包状态不可用，请稍后重试。'}
  finally{checking.value=false}
}
async function signOut(){try{await logout();await router.replace('/login')}catch(e){if(e instanceof ApiError&&e.status===401){clearSession();await router.replace('/login')}else message.value='退出请求未确认，请重试。'}}
onMounted(load)
let activated=false
onActivated(()=>{if(!activated){activated=true;return}void load()})
const deposit=ref<{walletId:string;address:string;wallet_address:string;checked_at:string;assets:{symbol:string;contract:string;minimum_usd:string}[]} | null>(null)
const depositBusy=ref(false), depositMessage=ref(''), depositQr=ref('')
const depositDialog=ref<HTMLDialogElement|null>(null)
const depositWallet=ref<Wallet|null>(null)
function openDeposit(wallet:Wallet){
  depositWallet.value=wallet
  depositDialog.value?.showModal()
  void loadDeposit(wallet.id)
}
onDeactivated(()=>depositDialog.value?.close())
async function loadDeposit(id:string){
  depositBusy.value=true
  deposit.value=null
  depositQr.value=''
  depositMessage.value=''
  try{
    const response=await fetch('/api/v1/wallets/'+encodeURIComponent(id)+'/deposit-address',{
      method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':session.csrf},
    })
    const body=await response.json()
    if(!response.ok)throw new Error(body?.error?.message || '充值地址暂不可用')
    deposit.value={...body.data,walletId:id}
    try{
      const {toDataURL}=await import('qrcode')
      depositQr.value=await toDataURL(body.data.address,{width:240,margin:4,errorCorrectionLevel:'M'})
    }catch{
      depositMessage.value='二维码暂不可用，可复制下方充值地址。'
    }
  }catch(e){depositMessage.value=e instanceof Error?e.message:'充值地址暂不可用'}
  finally{depositBusy.value=false}
}
async function copyDeposit(address:string){
  try{await navigator.clipboard.writeText(address);depositMessage.value='已复制充值地址。'}
  catch{depositMessage.value='无法复制，请手动选择并复制已显示的地址。'}
}
</script>
<template>
  <div class="app-layout">
    <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
    <main class="workspace wallet-workspace">
      <AppTopbar breadcrumb="工作台 / 钱包状态" :username="session.username" @sign-out="signOut" />
      <h1>钱包状态</h1>
      <p v-if="message" class="form-message" role="status">{{message}}</p>
      <p v-if="!wallets.length && !message">尚未配置钱包。</p>
      <div class="wallet-list">
        <article v-for="wallet in wallets" :key="wallet.id" class="wallet-card">
          <header class="wallet-header">
            <div>
              <h2>{{wallet.label}}</h2>
              <span class="wallet-type">{{wallet.wallet_type}}</span>
            </div>
            <div class="wallet-balance">
              <span>可用余额</span>
              <strong>{{selected?.wallet.id===wallet.id ? (selected.collateral_balance ?? '未知') : '—'}}</strong>
              <small v-if="selected?.wallet.id===wallet.id && selected.collateral_balance!=null">pUSD</small>
            </div>
          </header>
          <dl class="wallet-identities">
            <div><dt>钱包地址</dt><dd><code>{{wallet.wallet_address}}</code></dd></div>
            <div><dt>签名地址</dt><dd><code>{{wallet.signer_address}}</code></dd></div>
          </dl>
          <div class="wallet-actions">
            <button type="button" class="wallet-button wallet-primary" :disabled="depositBusy" @click="openDeposit(wallet)">查询 Polygon 充值地址</button>
            <button type="button" class="wallet-button" :disabled="checking" @click="status(wallet)">查看只读状态</button>
          </div>
          <div v-if="selected?.wallet.id===wallet.id" class="wallet-diagnostics">
            <div class="wallet-flags">
              <span>授权状态：{{selected.allowance_status==='unknown'?'未检查':selected.allowance_status}}</span>
              <span>地区状态：{{selected.availability==='blocked'?'受限':selected.availability==='allowed'?'本次检查允许':'未知'}}</span>
              <span>Builder 能力：未检查</span>
            </div>
            <p v-if="selected.diagnostic" class="form-message" role="status">{{selected.diagnostic.message}}（{{selected.diagnostic.code}}）</p>
            <details v-if="selected.steps?.length">
              <summary>查看诊断步骤</summary>
              <WalletDiagnosticSteps :steps="selected.steps" />
            </details>
          </div>
        </article>
      </div>
    </main>

    <dialog ref="depositDialog" class="deposit-dialog" aria-labelledby="deposit-title">
      <header class="deposit-header">
        <div>
          <h2 id="deposit-title">Polygon 充值地址</h2>
          <p v-if="depositWallet">{{depositWallet.label}}</p>
        </div>
        <form method="dialog"><button class="wallet-button" aria-label="关闭充值弹窗">关闭</button></form>
      </header>
      <div class="deposit-body">
        <p v-if="depositBusy && !deposit" role="status">正在查询充值地址…</p>
        <section v-if="deposit" aria-label="Polygon 充值" class="deposit-content">
          <div class="deposit-transfer">
            <img v-if="depositQr" class="deposit-qr" :src="depositQr" alt="Polygon 充值地址二维码" width="240" height="240">
            <div class="deposit-instructions">
              <div class="wallet-network">Polygon PoS · EVM · Chain ID 137</div>
              <div class="deposit-address">
                <span>收款地址</span>
                <code>{{deposit.address}}</code>
              </div>
              <button type="button" class="wallet-button wallet-primary" @click="copyDeposit(deposit.address)">复制充值地址</button>
              <p>扫码或复制后，在发送钱包选择 Polygon 网络和支持的代币，再填写金额。</p>
            </div>
          </div>
          <div class="deposit-assets">
            <h3>支持代币与最低充值金额</h3>
            <ul>
              <li v-for="asset in deposit.assets" :key="asset.contract">
                <details>
                  <summary><strong>{{asset.symbol}}</strong><span>最低 {{asset.minimum_usd}} USD 等值</span></summary>
                  <div class="deposit-contract">Polygon 代币合约 <code>{{asset.contract}}</code></div>
                </details>
              </li>
            </ul>
            <p>同名代币请展开并核对合约地址。</p>
          </div>
          <details class="deposit-source">
            <summary>入账钱包与地址来源</summary>
            <p><code>{{deposit.wallet_address}}</code></p>
            <p>Polymarket Bridge · {{formatDateTimeSeconds(deposit.checked_at)}} UTC+8</p>
          </details>
        </section>
        <p v-if="depositMessage" role="status">{{depositMessage}}</p>
        <button v-if="!deposit && !depositBusy && depositWallet" type="button" class="wallet-button" @click="loadDeposit(depositWallet.id)">重新查询</button>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
.wallet-workspace { min-width: 0; }
.wallet-workspace h1 { margin: 28px 0 20px; }
.wallet-list { display: grid; gap: 16px; max-width: 920px; }
.wallet-card { padding: clamp(16px, 3vw, 26px); border: 1px solid #e0e9e3; border-radius: 16px; background: #fff; }
.wallet-header, .deposit-header { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
.wallet-header h2, .deposit-header h2 { margin: 0 0 8px; font-size: 21px; }
.wallet-type, .wallet-network { display: inline-block; padding: 5px 9px; border-radius: 6px; background: #eaf5ef; color: #215d45; font-size: 12px; font-weight: 650; }
.wallet-balance { text-align: right; font-variant-numeric: tabular-nums; }
.wallet-balance span { display: block; color: #5b7065; font-size: 13px; }
.wallet-balance strong { font-size: clamp(19px, 3vw, 29px); }
.wallet-balance small { padding-left: 4px; }
.wallet-identities { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 20px 0 16px; }
.wallet-identities div, .deposit-address { padding: 12px; border-radius: 8px; background: #f4f8f5; }
.wallet-identities dt, .deposit-address span { display: block; margin-bottom: 5px; color: #5b7065; font-size: 13px; }
.wallet-identities dd { margin: 0; }
code { overflow-wrap: anywhere; font-size: 13px; }
.wallet-actions, .wallet-flags { display: flex; flex-wrap: wrap; gap: 8px; }
.wallet-button { padding: 8px 13px; border: 1px solid #c9dbd0; border-radius: 8px; background: #fff; color: #27533f; font-weight: 650; }
.wallet-button:disabled { opacity: .55; cursor: default; }
.wallet-primary { border-color: #22674f; background: #22674f; color: #fff; }
.wallet-diagnostics { margin-top: 16px; padding-top: 14px; border-top: 1px solid #e0e9e3; font-size: 14px; }
.wallet-flags span { padding: 4px 8px; border-radius: 6px; background: #f4f8f5; }
.wallet-diagnostics details { margin-top: 12px; }
summary { cursor: pointer; }
.deposit-dialog { width: min(690px, calc(100% - 24px)); max-width: none; max-height: calc(100dvh - 24px); padding: 0; overflow: auto; overscroll-behavior: contain; border: 1px solid #e0e9e3; border-radius: 17px; background: #fff; color: #1c2d29; }
.deposit-dialog::backdrop { background: rgb(16 31 25 / 52%); }
.deposit-header { position: sticky; top: 0; z-index: 1; padding: 20px; border-bottom: 1px solid #e0e9e3; background: #fff; }
.deposit-header p { margin: 0; color: #5b7065; font-size: 14px; }
.deposit-body { padding: 20px; }
.deposit-transfer { display: grid; grid-template-columns: 184px minmax(0, 1fr); align-items: center; gap: 16px; }
.deposit-transfer:not(:has(img)) { grid-template-columns: 1fr; }
.deposit-qr { display: block; width: 184px; max-width: 100%; height: auto; border: 1px solid #e0e9e3; border-radius: 12px; }
.deposit-address { margin: 10px 0; }
.deposit-instructions p, .deposit-assets p, .deposit-source { color: #5b7065; font-size: 13px; }
.deposit-assets h3 { margin: 20px 0 8px; font-size: 16px; }
.deposit-assets ul { list-style: none; margin: 0; padding: 0; border: 1px solid #e0e9e3; border-radius: 10px; }
.deposit-assets li + li { border-top: 1px solid #e0e9e3; }
.deposit-assets summary { padding: 9px 12px; font-size: 14px; }
.deposit-assets summary span { float: right; color: #426455; }
.deposit-contract { padding: 0 12px 10px; color: #426455; font-size: 13px; }
@media (max-width: 560px) {
  .wallet-identities, .deposit-transfer { grid-template-columns: 1fr; }
  .deposit-qr { justify-self: center; }
  .deposit-header, .deposit-body { padding: 14px; }
}
</style>
