import React, { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { AppHeader } from './AppHeader'
import { OnboardingModal } from '@/components/modals/OnboardingModal'
import { StepUpModal } from '@/components/modals/StepUpModal'
import { HilToast } from '@/components/modals/HilToast'
import { ToastRegion } from '@/components/ui/Toast'
import { GlobalOverlays } from '@/components/ui/GlobalOverlays'
import { useAuthStore } from '@/store/auth'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const { user } = useAuthStore()
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()
  const prevPath = useRef(location.pathname)

  // Trigger onboarding when user is loaded and hasn't completed it
  useEffect(() => {
    if (user && user.onboarding_completed === false) {
      setShowOnboarding(true)
    }
  }, [user])

  // Nav curtain + progress on route change
  useEffect(() => {
    if (location.pathname === prevPath.current) return
    prevPath.current = location.pathname

    const progress = document.getElementById('nav-progress')
    const curtain  = document.getElementById('nav-curtain')
    if (progress) { progress.classList.remove('run'); void progress.offsetWidth; progress.classList.add('run') }
    if (curtain)  { curtain.classList.remove('run');  void curtain.offsetWidth;  curtain.classList.add('run') }
  }, [location.pathname])

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--canvas)' }}>
      {/* Mobile backdrop — shown only when sidebarOpen on narrow screens */}
      {sidebarOpen && (
        <div
          id="mobile-backdrop"
          onClick={() => setSidebarOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,.4)',
            zIndex: 19,
          }}
        />
      )}

      <Sidebar
        onOpenOnboarding={() => setShowOnboarding(true)}
        mobileOpen={sidebarOpen}
        onMobileClose={() => setSidebarOpen(false)}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <AppHeader onToggleSidebar={() => setSidebarOpen(o => !o)} />
        <main id="main-content" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
          {children}
        </main>
      </div>

      {/* Global modals */}
      {showOnboarding && <OnboardingModal onClose={() => setShowOnboarding(false)} />}
      <StepUpModal />
      <HilToast />
      <ToastRegion />
      <GlobalOverlays />

      {/* Nav chrome — CSS-animated, outside the layout flow */}
      <div id="nav-progress" />
      <div id="nav-curtain" />
    </div>
  )
}
