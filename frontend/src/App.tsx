import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { useAuth } from '@/hooks/useAuth'
import { ErrorBoundary } from '@/components/shared/ErrorBoundary'
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
const LogsPage     = lazy(() => import('@/pages/Logs'))
const SignalsPage  = lazy(() => import('@/pages/Signals'))

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
      <ErrorBoundary>
        <Suspense fallback={<PageSkeleton />}>
          <Routes>
            <Route path="/"         element={<DashboardPage />} />
            <Route path="/signals"  element={<ErrorBoundary><SignalsPage /></ErrorBoundary>} />
            <Route path="/analyze"  element={<ErrorBoundary><AnalyzePage /></ErrorBoundary>} />
            <Route path="/paper"    element={<ErrorBoundary><PaperPage /></ErrorBoundary>} />
            <Route path="/backtest" element={<ErrorBoundary><BacktestPage /></ErrorBoundary>} />
            <Route path="/history"  element={<ErrorBoundary><HistoryPage /></ErrorBoundary>} />
            <Route path="/broker"   element={<ErrorBoundary><BrokerPage /></ErrorBoundary>} />
            <Route path="/ml"       element={<ErrorBoundary><MLPage /></ErrorBoundary>} />
            <Route path="/rl"       element={<ErrorBoundary><RLPage /></ErrorBoundary>} />
            <Route path="/settings" element={<ErrorBoundary><SettingsPage /></ErrorBoundary>} />
            <Route path="/admin"    element={<ErrorBoundary><AdminPage /></ErrorBoundary>} />
            <Route path="/hil"      element={<ErrorBoundary><HILPage /></ErrorBoundary>} />
            <Route path="/logs"     element={<ErrorBoundary><LogsPage /></ErrorBoundary>} />
            <Route path="/terms"    element={<TermsPage />} />
            <Route path="/privacy"  element={<PrivacyPage />} />
            <Route path="*"         element={<DashboardPage />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
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
