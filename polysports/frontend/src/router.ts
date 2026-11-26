import { createRouter, createWebHistory } from 'vue-router'
import { restoreSession } from './auth'

const Login = () => import('./views/Login.vue')
const Dashboard = () => import('./views/Dashboard.vue')
const SportsCenter = () => import('./views/SportsCenter.vue')
const SystemPage = () => import('./views/SystemPage.vue')
const Wallets = () => import('./views/Wallets.vue')
const Orders = () => import('./views/Orders.vue')
const OrderEntry = () => import('./views/OrderEntry.vue')
const Combos = () => import('./views/Combos.vue')
const History = () => import('./views/History.vue')
const PnlCalendar = () => import('./views/PnlCalendar.vue')

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
    { path: '/pnl-calendar', component: PnlCalendar, meta: { requiresAuth: true } },
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
