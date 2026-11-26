<script setup lang="ts">
import {onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {useRoute} from 'vue-router'

// 页面滚动超过约三分之一屏才出现，避免在顶部时就挡住内容。
const THRESHOLD=200
const visible=ref(false)
// 赛事中心的组合草稿条固定在底部，按钮要避开它，不能压在草稿内容上。
const clearance=ref(0)
const route=useRoute()

function trayClearance(){
  const tray=document.querySelector('.combo-tray')
  if(!tray)return 0
  const box=tray.getBoundingClientRect()
  return Math.max(0,Math.round(window.innerHeight-box.top)+12)
}

let frame=0
function update(){
  frame=0
  visible.value=window.scrollY>THRESHOLD
  clearance.value=visible.value?trayClearance():0
}
function onScroll(){if(frame)return;frame=requestAnimationFrame(update)}

function toTop(){
  const reduce=typeof window.matchMedia==='function'
    &&window.matchMedia('(prefers-reduced-motion: reduce)').matches
  window.scrollTo({top:0,behavior:reduce?'auto':'smooth'})
}

onMounted(()=>{update();window.addEventListener('scroll',onScroll,{passive:true})})
onBeforeUnmount(()=>{
  window.removeEventListener('scroll',onScroll)
  if(frame)cancelAnimationFrame(frame)
})

// 切换路由后滚动位置可能被重置或保留，重新计算一次可见性与避让高度。
watch(()=>route.fullPath,()=>requestAnimationFrame(update))
</script>

<template>
  <button
    v-if="visible"
    type="button"
    class="back-to-top"
    :style="clearance?'--tray-clearance:'+clearance+'px':undefined"
    aria-label="回到页面顶部"
    title="回到页面顶部"
    @click="toTop"
  >
    <span aria-hidden="true">↑</span>
  </button>
</template>
