import { createRoot } from 'react-dom/client'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClerkProvider } from '@clerk/react'
import { ErrorBoundary } from '@/components/shared/ErrorBoundary'
import './index.css'
import App from './App.tsx'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
})

// Clerk is an OPTIONAL step-up 2FA factor (re-verify with Google before trading).
// It only wraps the app when a publishable key is configured — with no key the app
// renders exactly as before, and the Clerk step-up option simply doesn't appear.
const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

function MaybeClerk({ children }: { children: ReactNode }) {
  if (!CLERK_KEY) return <>{children}</>
  return <ClerkProvider publishableKey={CLERK_KEY}>{children}</ClerkProvider>
}

createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    <MaybeClerk>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MaybeClerk>
  </ErrorBoundary>,
)
