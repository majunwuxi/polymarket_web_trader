import { createRouter, createWebHistory } from 'vue-router'
import Login from './views/Login.vue'
import Dashboard from './views/Dashboard.vue'
import SportsCenter from './views/SportsCenter.vue'
import SystemPage from './views/SystemPage.vue'
import Wallets from './views/Wallets.vue'
import Orders from './views/Orders.vue'
import OrderEntry from './views/OrderEntry.vue'
import Combos from './views/Combos.vue'
import History from './views/History.vue'
import { restoreSession } from './auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login },
    { path: '/', component: Dashboard, meta: { requiresAuth: true } },
    { path: '/sports/:sport(football|nba)', component: SportsCenter, meta: { requiresAuth: true } },
    { path: '/system', component: SystemPage, meta: { requiresAuth: true } },
    { path: '/wallets', component: Wallets, meta: { requiresAuth: true } },
    { path: '/combos', component: Combos, meta: { requiresAuth: true } },
    { path: '/history', component: History, meta: { requiresAuth: true } },
    { path: '/orders', component: Orders, meta: { requiresAuth: true } },
    { path: '/orders/new/:link/:outcome', component: OrderEntry, meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async (to) => {
  if (!to.meta.requiresAuth) return true
  try { return (await restoreSession()) ? true : '/login' }
  catch { return '/login?reason=unavailable' }
})
