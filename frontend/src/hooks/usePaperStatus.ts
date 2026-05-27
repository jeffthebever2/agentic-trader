import { useQuery } from '@tanstack/react-query'
import { getPaperStatus } from '@/api/paper'

export function usePaperStatus(refetchInterval = 15_000) {
  return useQuery({
    queryKey: ['paper', 'status'],
    queryFn: getPaperStatus,
    refetchInterval,
    staleTime: 10_000,
  })
}
