import { useEffect, useRef } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { AuthGate } from './components/AuthGate'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Home } from './pages/Home'
import { AppHealth } from './pages/AppHealth'
import { Services } from './pages/Services'
import { Logs } from './pages/Logs'
import { Deployments } from './pages/Deployments'
import { ModelManagement } from './pages/ModelManagement'
import { DataSources } from './pages/DataSources'
import { SettingsPage } from './pages/Settings'
import { useSettingsStore } from './store/settings'
import { useAuthStore } from './store/auth'
import { Loading } from './components/StateViews'

function AppShell() {
  return (
    <div className="flex h-screen bg-gray-950 text-white overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <ErrorBoundary>
          <Routes>
            <Route path="/"         element={<Home />} />
            <Route path="/health"   element={<AppHealth />} />
            <Route path="/services" element={<Services />} />
            <Route path="/logs"     element={<Logs />} />
            <Route path="/deploy"   element={<Deployments />} />
            <Route path="/models"   element={<ModelManagement />} />
            <Route path="/data"     element={<DataSources />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*"         element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  )
}

export default function App() {
  const load = useSettingsStore(s => s.load)
  const loaded = useSettingsStore(s => s.loaded)
  const checkAuth = useAuthStore(s => s.checkAuth)
  const initializing = useAuthStore(s => s.initializing)

  // Run once on mount only
  const initialized = useRef(false)
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    load().then(() => checkAuth())
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Only block UI on the very first init — not on re-auth checks
  if (!loaded || initializing) {
    return (
      <div className="h-screen bg-gray-950 flex items-center justify-center">
        <Loading label="Connecting…" />
      </div>
    )
  }

  return (
    <AuthGate>
      <AppShell />
    </AuthGate>
  )
}
