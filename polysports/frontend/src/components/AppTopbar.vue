<script setup lang="ts">
defineProps<{ breadcrumb: string; username: string }>()
const emit = defineEmits<{ signOut: [] }>()
</script>

<template>
  <div class="workspace-top">
    <span class="breadcrumb">{{ breadcrumb }}</span>
    <div class="account"><span>{{ username }}</span><button class="text-button" @click="emit('signOut')">退出</button></div>
  </div>
</template>
