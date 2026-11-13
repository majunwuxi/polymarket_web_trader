<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, login } from '../auth'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const showPassword = ref(false)
const busy = ref(false)
const message = ref(route.query.reason === 'unavailable' ? '服务暂不可用，请稍后重试。' : '')

async function submit() {
  if (busy.value) return
  busy.value = true; message.value = ''
  try {
    await login(username.value, password.value)
    await router.replace('/')
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) message.value = `登录尝试过于频繁，请等待 ${error.retryAfter ?? '一段时间'} 秒。`
    else if (error instanceof ApiError && error.status === 401) message.value = '用户名或密码无效。'
    else message.value = '暂时无法登录，请稍后重试。'
  } finally { password.value = ''; busy.value = false }
}
</script>

<template>
  <main class="login-layout">
    <section class="login-intro" aria-label="PolySports 介绍">
      <div class="brand"><span class="brand-mark" aria-hidden="true">P</span> PolySports</div>
      <div class="intro-copy"><p class="eyebrow">SPORTS · DATA · ORGANIZATION</p><h1>赛事，<br>清晰掌握。</h1><p>一个用于管理足球与 NBA 赛事的个人工作台。</p></div>
      <p class="intro-foot">PRIVATE WORKSPACE <span>·</span> UTC+8</p>
    </section>
    <section class="login-main"><div class="login-card">
      <p class="eyebrow">ADMINISTRATOR</p><h2>欢迎回来</h2><p class="muted">使用管理员账号登录 PolySports。</p>
      <form @submit.prevent="submit">
        <label for="username">用户名</label><input id="username" v-model="username" name="username" autocomplete="username" required maxlength="100" />
        <label for="password">密码</label><div class="password-field"><input id="password" v-model="password" :type="showPassword ? 'text' : 'password'" name="password" autocomplete="current-password" required /><button type="button" class="show-password" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button></div>
        <p class="form-message" role="status" aria-live="polite">{{ message }}</p>
        <button class="primary" type="submit" :disabled="busy">{{ busy ? '正在登录…' : '登录工作台' }} <span aria-hidden="true">→</span></button>
      </form>
      <p class="security-note">安全连接 · 单管理员访问</p>
    </div></section>
  </main>
</template>
