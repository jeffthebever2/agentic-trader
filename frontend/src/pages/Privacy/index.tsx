export default function PrivacyPage() {
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
    <div id="panel-privacy" style={{ padding: 24, overflowY: 'auto' }}>
      <div style={shell}>
        <div style={kicker}>Legal</div>
        <h2 style={titleStyle}>Privacy Policy</h2>
        <div style={updatedStyle}>Effective May 19, 2026</div>
        <p style={introStyle}>
          This Privacy Policy explains what information Agentic Trader may process
          when you use the private dashboard, trading tools, text alerts, email
          notices, AI features, and broker-related workflows.
        </p>

        <div style={sectionStyle}>
          <h3 style={h3Style}>1. Scope</h3>
          <p style={pStyle}>
            This policy applies to information processed by the Agentic Trader
            dashboard and its configured integrations. It does not replace the
            privacy policies of Cloudflare, Sendblue, brokers, market data
            providers, AI model providers, email providers, or other third-party
            services connected to the dashboard.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>2. Information processed</h3>
          <p style={pStyle}>The service may process the following categories of information:</p>
          <ul style={ulStyle}>
            <li>Login identity, email address, role, and access status from Cloudflare Access.</li>
            <li>Support contact information, including messages sent to support@agentictrader.org.</li>
            <li>Privacy request contact information, including messages sent to privacy@agentictrader.org.</li>
            <li>Dashboard settings, API provider choices, local preferences, security events, and audit logs.</li>
            <li>Watchlists, tickers, analysis requests, backtest results, paper trading records, and portfolio data.</li>
            <li>Phone numbers, SMS messages, email delivery metadata, and alert delivery status.</li>
            <li>Broker session data, account snapshots, positions, and order preview details when broker features are enabled.</li>
            <li>Environment configuration, API keys, tokens, local session files, temporary files, logs, and database records needed to run enabled features.</li>
            <li>Device, browser, IP, request, and security metadata processed by Cloudflare or the local server.</li>
          </ul>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>3. How information is used</h3>
          <p style={pStyle}>
            Information is used to authenticate users, operate the dashboard, run
            analysis and backtests, show portfolio and paper trading state, send
            alerts, support approval workflows, protect access, troubleshoot errors,
            investigate suspicious activity, respond to support requests, maintain
            records, and improve reliability.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>4. Legal and operational reasons for processing</h3>
          <p style={pStyle}>
            Information is processed because it is needed to provide requested
            features, protect the service, manage access, comply with applicable
            obligations, preserve records, prevent abuse, or follow a user-authorized
            workflow such as sending alerts, running model analysis, previewing
            broker actions, or displaying portfolio data.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>5. Storage locations</h3>
          <p style={pStyle}>
            Data may be stored on the server running this application, including
            local environment files, session files, databases, temporary files, and
            logs. Depending on deployment, that server may be a local computer,
            private server, cloud instance, or other host controlled by the site
            owner. Browser local storage may also store interface preferences.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>6. Third-party processors and providers</h3>
          <p style={pStyle}>
            The service may send necessary information to configured providers,
            including Cloudflare for access, security, DNS, and tunneling; Sendblue
            or other messaging providers for SMS; email providers for notices and
            support; broker APIs for account and order workflows; market data
            providers for quotes and history; and AI model providers for analysis.
            These providers may process information under their own terms and
            privacy policies.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>7. AI model and data provider use</h3>
          <p style={pStyle}>
            If AI features are enabled, prompts, tickers, portfolio context,
            analysis inputs, and generated outputs may be sent to configured model
            providers. Do not enter sensitive information into prompts unless the
            feature requires it and you accept the provider's data practices.
            Market data and AI outputs may be inaccurate, incomplete, delayed, or
            retained by providers according to their policies.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>8. Broker and financial information</h3>
          <p style={pStyle}>
            Broker-related features may process account identifiers, balances,
            positions, transaction context, previewed orders, and session state.
            Broker credentials and sessions should be treated as sensitive. The
            dashboard should not be used from untrusted devices or networks.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>9. SMS and email communications</h3>
          <p style={pStyle}>
            By adding a phone number or email address for alerts, you authorize the
            service to send operational messages such as approvals, status updates,
            runner events, access notices, and failure alerts. Message and data
            rates may apply. Automated system messages may be sent from{' '}
            <code>no-reply@agentictrader.org</code>. Human support and privacy
            requests should be sent to{' '}
            <a href="mailto:support@agentictrader.org" style={aStyle}>support@agentictrader.org</a>.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>10. Selling personal information</h3>
          <p style={pStyle}>
            Agentic Trader does not sell personal information. Data is shared only
            as needed to operate configured features, comply with law, protect the
            service, respond to support or privacy requests, or follow a
            user-authorized workflow.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>11. Security</h3>
          <p style={pStyle}>
            The dashboard is designed for restricted access using Cloudflare Access,
            allowlists, local server binding, masked secrets, and application-level
            checks. No system is perfectly secure, so users should protect their
            devices, accounts, API keys, broker sessions, and phone numbers. Report
            suspected unauthorized access or exposed credentials immediately.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>12. Retention</h3>
          <p style={pStyle}>
            Data is kept for as long as needed to operate the dashboard or until the
            site owner deletes it. Some records may remain longer in backups, logs,
            broker systems, provider systems, audit records, or records needed for
            security, troubleshooting, legal, tax, accounting, or operational
            reasons.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>13. Your choices and requests</h3>
          <p style={pStyle}>
            You can ask the site owner to remove your access, update your email,
            remove your phone number, delete local records, export available local
            data, or clear saved account data where technically available and not
            legally or operationally restricted. Requests should be sent to{' '}
            <a href="mailto:privacy@agentictrader.org" style={aStyle}>privacy@agentictrader.org</a>.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>14. State, national, and international privacy rights</h3>
          <p style={pStyle}>
            Depending on where you live, you may have rights to request access,
            correction, deletion, portability, restriction, objection, or information
            about how personal data is used. The site owner will review requests and
            respond as required by applicable law. The dashboard is intended as a
            private controlled-access service, not a public consumer data broker.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>15. Cookies, local storage, and logs</h3>
          <p style={pStyle}>
            The dashboard may use cookies, Cloudflare Access tokens, browser local
            storage, server logs, and security logs to keep users signed in, remember
            preferences, protect the service, diagnose errors, and route traffic.
            Blocking these technologies may break parts of the dashboard.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>16. Children</h3>
          <p style={pStyle}>
            This service is not intended for children or for anyone who is not
            authorized to access trading-related tools. Do not use the dashboard if
            you are under 18 or under the age required to open and control brokerage
            or financial accounts in your location.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>17. Changes to this policy</h3>
          <p style={pStyle}>
            This policy may be updated as the dashboard, integrations, providers,
            deployment, or legal requirements change. Continued use after an update
            means you acknowledge the updated policy.
          </p>
        </div>

        <div style={sectionStyle}>
          <h3 style={h3Style}>Contact</h3>
          <p style={pStyle}>
            Privacy, deletion, or data requests:{' '}
            <a href="mailto:privacy@agentictrader.org" style={aStyle}>privacy@agentictrader.org</a>
          </p>
        </div>

        <div style={noteStyle}>
          For account removal, data export, or privacy questions, email{' '}
          <a href="mailto:privacy@agentictrader.org" style={aStyle}>privacy@agentictrader.org</a>.
          Automated system messages will be sent from <code>no-reply@agentictrader.org</code>;
          replies should route to the support address where configured.
        </div>
      </div>
    </div>
  );
}
