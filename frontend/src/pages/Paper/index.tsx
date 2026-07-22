import { PortfolioCompetition } from './PortfolioCompetition'

/**
 * Paper Trading = the 15-portfolio competition (isolated $10k accounts, paper-only,
 * ranked by all-time ROR). The legacy 6-strategy shared-cash runner was removed
 * 2026-07-05 — superseded by this system.
 */
export default function PaperPage() {
  return (
    <div id="panel-paper" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>Paper Trading</div>
        <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>
          15-portfolio competition · $10,000 each · paper-only · ranked by all-time ROR
        </div>
      </div>
      <PortfolioCompetition />
    </div>
  )
}
