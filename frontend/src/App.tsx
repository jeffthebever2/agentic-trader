import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { useAuth } from '@/hooks/useAuth'
import DashboardPage  from '@/pages/Dashboard'
import AnalyzePage    from '@/pages/Analyze'
import PaperPage      from '@/pages/Paper'
import BacktestPage   from '@/pages/Backtest'
import HistoryPage    from '@/pages/History'
import BrokerPage     from '@/pages/Broker'
import MLPage         from '@/pages/ML'
import RLPage         from '@/pages/RL'
import SettingsPage   from '@/pages/Settings'
import AdminPage      from '@/pages/Admin'
import HILPage        from '@/pages/HIL'
import TermsPage      from '@/pages/Terms'
import PrivacyPage    from '@/pages/Privacy'

function AppRoutes() {
  // Bootstrap auth on mount
  useAuth()

  return (
    <AppShell>
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
