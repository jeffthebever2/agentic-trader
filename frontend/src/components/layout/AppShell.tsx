import React, { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { AppHeader } from './AppHeader'
import { OnboardingModal } from '@/components/modals/OnboardingModal'
import { StepUpModal } from '@/components/modals/StepUpModal'
import { HilToast } from '@/components/modals/HilToast'
import { ToastRegion } from '@/components/ui/Toast'
import { GlobalOverlays } from '@/components/ui/GlobalOverlays'
import { CommandPalette } from '@/components/CommandPalette'
import { useAuthStore } from '@/store/auth'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const { user, realUser, restoreAdminView } = useAuthStore()
  const [onboardingDismissed, setOnboardingDismissed] = useState(false)
  const showOnboarding = !onboardingDismissed && !!(user && user.onboarding_completed === false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()
  const prevPath = useRef(location.pathname)

  // Nav curtain + progress on route change
  useEffect(() => {
    if (location.pathname === prevPath.current) return
    prevPath.current = location.pathname

    const progress = document.getElementById('nav-progress')
    const curtain  = document.getElementById('nav-curtain')
    const main     = document.getElementById('main-content')
    if (progress) { progress.classList.remove('run'); void progress.offsetWidth; progress.classList.add('run') }
    if (curtain)  { curtain.classList.remove('run');  void curtain.offsetWidth;  curtain.classList.add('run') }
    if (main)     { main.classList.remove('page-enter'); void main.offsetWidth;  main.classList.add('page-enter') }
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
        onOpenOnboarding={() => setOnboardingDismissed(false)}
        mobileOpen={sidebarOpen}
        onMobileClose={() => setSidebarOpen(false)}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <AppHeader onToggleSidebar={() => setSidebarOpen(o => !o)} />
        {realUser && (
          <div
            id="view-as-banner"
            style={{
              background: 'rgba(224,62,0,.09)',
              borderBottom: '1px solid rgba(224,62,0,.22)',
              padding: '8px 24px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              flexShrink: 0,
              color: 'var(--ink)',
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            <div>
              Viewing as <span id="view-as-email" style={{ fontFamily: 'var(--font-mono)' }}>{user?.email ?? 'standard user'}</span>
              <span id="view-as-admin" style={{ color: 'var(--ink-faint)', fontWeight: 500 }}> from {realUser.email}</span>
            </div>
            <button className="btn-secondary" style={{ fontSize: 11, padding: '4px 10px' }} onClick={restoreAdminView}>
              Return to admin
            </button>
          </div>
        )}
        <main id="main-content" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
          {children}
        </main>
      </div>

      {/* Global modals */}
      {showOnboarding && <OnboardingModal onClose={() => setOnboardingDismissed(true)} />}
      <StepUpModal />
      <HilToast />
      <ToastRegion />
      <GlobalOverlays />
      <CommandPalette />

      {/* Nav chrome — CSS-animated, outside the layout flow */}
      <div id="nav-progress" />
      <div id="nav-curtain" />
    </div>
  )
}
