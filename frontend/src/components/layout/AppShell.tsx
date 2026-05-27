import React, { useEffect, useState } from 'react'
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

  // Trigger onboarding when user is loaded and hasn't completed it
  useEffect(() => {
    if (user && user.onboarding_completed === false) {
      setShowOnboarding(true)
    }
  }, [user])

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
    </div>
  )
}
