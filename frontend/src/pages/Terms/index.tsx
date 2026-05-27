export default function TermsPage() {
  const shell: React.CSSProperties = { maxWidth: 820, margin: 'auto' };
  const kicker: React.CSSProperties = { fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--ink-faint)', marginBottom: 8 };
  const titleStyle: React.CSSProperties = { fontSize: 28, fontWeight: 800, color: 'var(--ink)', marginBottom: 4 };
  const updatedStyle: React.CSSProperties = { fontSize: 12, color: 'var(--ink-faint)', marginBottom: 24 };
  const introStyle: React.CSSProperties = { fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.7, marginBottom: 32, borderBottom: '1px solid var(--surface-rule)', paddingBottom: 24 };
  const sectionStyle: React.CSSProperties = { marginBottom: 28 };
  const h3Style: React.CSSProperties = { fontSize: 14, fontWeight: 700, color: 'var(--ink)', marginBottom: 8 };
  const pStyle: React.CSSProperties = { fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.7 };
  const ulStyle: React.CSSProperties = { paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.7 };
  const aStyle: React.CSSProperties = { color: 'var(--accent)', fontWeight: 600, textDecoration: 'none' };
  const noteStyle: React.CSSProperties = { fontSize: 12, color: 'var(--ink-faint)', lineHeight: 1.7, marginTop: 32, paddingTop: 20, borderTop: '1px solid var(--surface-rule)' };

  return (
    <div id="panel-terms" style={{ padding: 24, overflowY: 'auto' }}>
      <div style={shell}>
        <div style={kicker}>Legal</div>
        <h2 style={titleStyle}>Terms of Service</h2>
        <div style={updatedStyle}>Effective May 19, 2026</div>
        <p style={introStyle}>
          These Terms of Service govern access to and use of Agentic Trader,
          including the dashboard, AI analysis tools, screeners, backtests, paper
          trading runner, broker connection screens, alerts, approval flows, and
          any related automation exposed through this site.
        </p>

        <div style={sectionStyle}>
          <h3 style={h3Style}>1. Private access and acceptance</h3>
          <p style={pStyle}>
            Agentic Trader is a private, access-controlled trading research and
            operations dashboard. By using the service, you agree to these terms.
            If you do not agree, do not use the dashboard. Access may be limited to
            approved users through Cloudflare Access, email allowlists, local
            application checks, and other security controls.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>2. Not financial, investment, tax, or legal advice</h3>
          <p style={pStyle}>
            The dashboard, AI outputs, backtests, screeners, alerts, and trading
            signals are provided for informational, educational, research, and
            personal workflow purposes only. They are not financial advice,
            investment advice, tax advice, accounting advice, legal advice, a
            recommendation to buy or sell any security, or an offer or solicitation
            to enter any transaction. You are solely responsible for deciding
            whether any trade, strategy, asset, broker, model, or setting is
            appropriate for you.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>3. Trading and automation risk</h3>
          <p style={pStyle}>
            Trading securities, options, crypto assets, currencies, or any other
            market instrument involves substantial risk, including the possible
            loss of principal. Automated and AI-assisted tools may misunderstand
            instructions, rely on stale data, overfit historical patterns, produce
            inaccurate analysis, or fail during fast markets. Backtested, simulated,
            paper, hypothetical, or AI-generated performance does not guarantee
            future results.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>4. No adviser, broker, fiduciary, or client relationship</h3>
          <p style={pStyle}>
            Agentic Trader is software. Unless a separate written agreement says
            otherwise, use of the dashboard does not create an investment adviser,
            broker-dealer, fiduciary, attorney-client, tax adviser, or other
            professional relationship with the site owner, contributors, or any
            configured AI, data, cloud, SMS, or broker provider.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>5. User responsibility for decisions and orders</h3>
          <p style={pStyle}>
            You are responsible for reviewing all information before acting. Before
            approving, placing, or relying on any trade or order, verify the symbol,
            side, quantity, account, order type, limit price, time in force, market
            conditions, liquidity, fees, tax effects, account restrictions, and
            whether the order is paper, simulated, preview-only, or live. Approval
            of any live action is your decision.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>6. Paper trading and backtests</h3>
          <p style={pStyle}>
            Paper trading, backtests, scans, rankings, charts, and simulated
            account values are estimates. They may omit commissions, spreads,
            slippage, partial fills, market impact, borrow costs, corporate actions,
            taxes, latency, rejects, and broker-specific rules. Do not treat them
            as a promise of profit or as a complete model of real-world execution.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>7. Broker connections and third-party services</h3>
          <p style={pStyle}>
            The service may connect to third-party services such as brokers, market
            data providers, AI model providers, Cloudflare, Sendblue, email
            providers, and other APIs. Those services are governed by their own
            terms, policies, limits, outages, pricing, and data practices. Agentic
            Trader does not control third-party services and is not responsible for
            their errors, delays, security incidents, suspensions, changes, or
            availability.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>8. Credentials, sessions, and devices</h3>
          <p style={pStyle}>
            You are responsible for keeping your login, email account, phone
            number, broker credentials, broker sessions, API keys, devices, and
            browser sessions secure. Do not share access unless the site owner has
            approved it. If you believe your access, phone number, API key, or
            broker session has been compromised, stop using the service and contact
            support immediately.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>9. Alerts, email, and SMS</h3>
          <p style={pStyle}>
            Alerts may be delayed, blocked, duplicated, filtered, or fail to arrive.
            SMS and email are not guaranteed delivery channels and should not be
            used as the only source of time-sensitive trading information. Automated
            system emails may come from <code>no-reply@agentictrader.org</code>,
            while support requests should go to{' '}
            <a href="mailto:support@agentictrader.org" style={aStyle}>support@agentictrader.org</a>
            {' '}and privacy requests should go to{' '}
            <a href="mailto:privacy@agentictrader.org" style={aStyle}>privacy@agentictrader.org</a>.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>10. Acceptable use</h3>
          <p style={pStyle}>You agree not to:</p>
          <ul style={ulStyle}>
            <li>Use the service for unlawful, abusive, deceptive, or unauthorized purposes.</li>
            <li>Attempt to bypass Cloudflare Access, authentication, allowlists, rate limits, or application controls.</li>
            <li>Upload or enter credentials, API keys, or personal data unless needed for an intended feature.</li>
            <li>Use the service to manipulate markets, evade broker rules, or violate exchange, securities, commodities, or sanctions laws.</li>
            <li>Reverse engineer, overload, scrape, resell, or provide access to the service without permission.</li>
          </ul>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>11. Availability, changes, and suspension</h3>
          <p style={pStyle}>
            The service may change, fail, pause, or be unavailable at any time.
            Access may be suspended or removed for security reasons, maintenance,
            suspected abuse, legal risk, provider changes, or violation of these
            terms. Features may be experimental and may be changed or removed
            without notice.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>12. No warranties</h3>
          <p style={pStyle}>
            The service is provided as-is and as-available. To the fullest extent
            permitted by law, the site owner and contributors disclaim all
            warranties, including warranties of accuracy, reliability, uptime,
            merchantability, fitness for a particular purpose, non-infringement,
            profitability, data completeness, and error-free operation.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>13. Limitation of liability</h3>
          <p style={pStyle}>
            To the fullest extent permitted by law, the site owner and contributors
            are not liable for direct, indirect, incidental, consequential,
            special, punitive, exemplary, trading, market, data, account, business,
            reputational, tax, or lost-profit damages related to your use of the
            service, inability to use it, reliance on its outputs, data errors,
            automation failures, security incidents, outages, broker actions, or
            third-party services.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>14. Indemnity</h3>
          <p style={pStyle}>
            You agree to defend, indemnify, and hold harmless the site owner and
            contributors from claims, losses, liabilities, damages, costs, and fees
            arising from your use of the service, your trading decisions, your
            violation of these terms, your misuse of credentials or third-party
            services, or your violation of law or third-party rights.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>15. Privacy</h3>
          <p style={pStyle}>
            Use of the service is also governed by the Privacy Policy on this site.
            By using the service, you acknowledge that data may be processed by the
            local application and configured providers as described there.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>16. Changes to these terms</h3>
          <p style={pStyle}>
            These terms may be updated as the dashboard, providers, security model,
            or legal requirements change. Continued use after an update means you
            accept the updated terms.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>Contact</h3>
          <p style={pStyle}>
            Questions about these terms:{' '}
            <a href="mailto:support@agentictrader.org" style={aStyle}>support@agentictrader.org</a>
          </p>
        </div>

        <div style={noteStyle}>
          These terms are protective operating terms for a private software
          dashboard, not a substitute for advice from a qualified attorney. If you
          do not agree with these terms, stop using the dashboard and email{' '}
          <a href="mailto:support@agentictrader.org" style={aStyle}>support@agentictrader.org</a>
          {' '}to have your access removed.
        </div>
      </div>
    </div>
  );
}
