<script setup lang="ts">
const cachedViews = ['Dashboard', 'SportsCenter', 'Orders', 'History', 'Combos', 'Wallets', 'SystemPage']
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <KeepAlive :include="cachedViews">
      <component :is="Component" :key="route.path" />
    </KeepAlive>
  </RouterView>
</template>
