<script setup lang="ts">
defineProps<{ open: boolean }>()
const emit = defineEmits<{ toggle: [] }>()
</script>

<template>
  <header class="mobile-header">
    <div class="brand"><span class="brand-mark">P</span> PolySports</div>
    <button class="menu-button" :aria-expanded="open" aria-controls="primary-nav" @click="emit('toggle')">菜单</button>
  </header>
  <aside id="primary-nav" class="sidebar" :class="{ open }">
    <div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div>
    <p class="nav-label">工作台</p>
    <nav aria-label="主导航">
      <RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink>
      <RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink>
      <RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/history">历史与导出</RouterLink>
      <RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink>
    </nav>
  </aside>
</template>
