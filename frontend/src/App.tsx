import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { useAuth } from '@/hooks/useAuth'
import DashboardPage from '@/pages/Dashboard'

// Lazy-loaded pages (Dashboard kept eager for fastest first paint)
const AnalyzePage  = lazy(() => import('@/pages/Analyze'))
const PaperPage    = lazy(() => import('@/pages/Paper'))
const BacktestPage = lazy(() => import('@/pages/Backtest'))
const HistoryPage  = lazy(() => import('@/pages/History'))
const BrokerPage   = lazy(() => import('@/pages/Broker'))
const MLPage       = lazy(() => import('@/pages/ML'))
const RLPage       = lazy(() => import('@/pages/RL'))
const SettingsPage = lazy(() => import('@/pages/Settings'))
const AdminPage    = lazy(() => import('@/pages/Admin'))
const HILPage      = lazy(() => import('@/pages/HIL'))
const TermsPage    = lazy(() => import('@/pages/Terms'))
const PrivacyPage  = lazy(() => import('@/pages/Privacy'))

function PageSkeleton() {
  return (
    <div
      className="skeleton-pulse"
      style={{
        width: '100%',
        height: 200,
        borderRadius: 0,
      }}
    />
  )
}

function AppRoutes() {
  // Bootstrap auth on mount
  useAuth()

  return (
    <AppShell>
      <Suspense fallback={<PageSkeleton />}>
        <Routes>
          <Route path="/"         element={<DashboardPage />} />
          <Route path="/analyze"  element={<AnalyzePage />} />
          <Route path="/paper"    element={<PaperPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/history"  element={<HistoryPage />} />
          <Route path="/broker"   element={<BrokerPage />} />
          <Route path="/ml"       element={<MLPage />} />
          <Route path="/rl"       element={<RLPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/admin"    element={<AdminPage />} />
          <Route path="/hil"      element={<HILPage />} />
          <Route path="/terms"    element={<TermsPage />} />
          <Route path="/privacy"  element={<PrivacyPage />} />
          <Route path="*"         element={<DashboardPage />} />
        </Routes>
      </Suspense>
    </AppShell>
  )
}

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <AppRoutes />
    </BrowserRouter>
  )
}
