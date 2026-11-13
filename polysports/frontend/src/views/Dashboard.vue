<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, clearSession, logout, session } from '../auth'
const router = useRouter()
const menuOpen = ref(false)
const errorMessage = ref('')
async function signOut() {
  try { await logout(); await router.replace('/login') }
  catch (error) { if (error instanceof ApiError && error.status === 401) { clearSession(); await router.replace('/login') } else { errorMessage.value = '退出请求未确认，请重试。' } }
}
</script>
<template>
  <div class="app-layout">
    <header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen = !menuOpen">菜单</button></header>
    <aside id="primary-nav" class="sidebar" :class="{ open: menuOpen }"><div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div><p class="nav-label">工作台</p><nav aria-label="主导航"><RouterLink to="/" class="nav-active">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink></nav><div class="sidebar-foot">个人赛事工作台<br><span>POLYSPORTS</span></div></aside>
    <main class="workspace"><div class="workspace-top"><span class="breadcrumb">工作台 / 总览</span><div class="account"><span>{{ session.username }}</span><button class="text-button" @click="signOut">退出</button></div></div><p class="form-message" role="status">{{ errorMessage }}</p><div class="page-heading"><div><p class="eyebrow">YOUR WORKSPACE</p><h1>赛事总览</h1><p class="muted">欢迎回来，{{ session.username }}。你的个人工作台已就绪。</p></div><span class="phase-pill">基础建设中</span></div><section class="welcome-card"><div class="welcome-number">01 / 06</div><h2>从可靠的数据开始</h2><p>赛事、关注与单场订单已接入；提交前请核对真实资金风险。</p><div class="info-grid"><div><span>足球赛事</span><strong>进入赛事中心</strong></div><div><span>NBA 赛事</span><strong>进入赛事中心</strong></div><div><span>交易功能</span><strong>单场订单</strong></div></div></section><p class="footer-note">总览页面不显示赛事、报价或交易数据。</p></main>
  </div>
</template>
