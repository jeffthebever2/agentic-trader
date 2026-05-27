import React, { useEffect, useState } from 'react'
import { Sidebar } from './Sidebar'
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

  // Trigger onboarding when user is loaded and hasn't completed it
  useEffect(() => {
    if (user && user.onboarding_completed === false) {
      setShowOnboarding(true)
    }
  }, [user])

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--canvas)' }}>
      <Sidebar onOpenOnboarding={() => setShowOnboarding(true)} />
      <main style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
        {children}
      </main>

      {/* Global modals */}
      {showOnboarding && <OnboardingModal onClose={() => setShowOnboarding(false)} />}
      <StepUpModal />
      <HilToast />
      <ToastRegion />
      <GlobalOverlays />
    </div>
  )
}
