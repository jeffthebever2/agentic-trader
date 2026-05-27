import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getMe, getFeatures } from '@/api/auth'
import { useAuthStore } from '@/store/auth'

export function useAuth() {
  const { user, features, loading, isAdmin, setUser, setFeatures, setLoading } = useAuthStore()

  const meQuery = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: getMe,
    retry: 1,
    staleTime: 60_000,
  })

  const featuresQuery = useQuery({
    queryKey: ['auth', 'features'],
    queryFn: getFeatures,
    retry: 1,
    staleTime: 60_000,
  })

  useEffect(() => {
    if (meQuery.data)       setUser(meQuery.data)
    if (featuresQuery.data) setFeatures(featuresQuery.data)
    setLoading(meQuery.isLoading || featuresQuery.isLoading)
  }, [meQuery.data, meQuery.isLoading, featuresQuery.data, featuresQuery.isLoading])

  return { user, features, loading, isAdmin: isAdmin(), error: meQuery.error }
}
