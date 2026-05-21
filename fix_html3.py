with open("web/static/index.html", "r") as f:
    html = f.read()

# 1. Inject DOMPurify
if "dompurify" not in html:
    html = html.replace('</head>', '  <script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.8/purify.min.js"></script>\n</head>')

# 2. Add safeHtml helper
helper = """
function safeHtml(html) {
  if (typeof DOMPurify !== 'undefined') {
    return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
  }
  return html;
}
"""
if "function safeHtml" not in html:
    html = html.replace('<script>', '<script>\n' + helper, 1)

# List of replacements from CodeQL lines:
replacements = [
    # 6292
    ("container.insertAdjacentHTML('beforeend', html);", "container.insertAdjacentHTML('beforeend', safeHtml(html));"),
    # 6412
    ("reportEl.innerHTML = verdictCard + (existing.includes('agents finish') ? '' : existing);", "reportEl.innerHTML = safeHtml(verdictCard + (existing.includes('agents finish') ? '' : existing));"),
    # 6665
    ("body.innerHTML = (verdictCard || '') + renderMd(content||'');", "body.innerHTML = safeHtml((verdictCard || '') + renderMd(content||''));"),
    # 7176
    ("row.innerHTML = `<td class=\"px-4 py-2.5 font-mono font-bold text-slate-200\">${escHtml(msg.ticker)}</td><td class=\"px-4 py-2.5 text-slate-400\">${escHtml(msg.date)}</td><td class=\"px-4 py-2.5 text-center\">${decisionBadge(msg.decision)}</td>`;", "row.innerHTML = safeHtml(`<td class=\"px-4 py-2.5 font-mono font-bold text-slate-200\">${escHtml(msg.ticker)}</td><td class=\"px-4 py-2.5 text-slate-400\">${escHtml(msg.date)}</td><td class=\"px-4 py-2.5 text-center\">${decisionBadge(msg.decision)}</td>`);"),
    # 9946 (CodeQL flagged 9940 area)
    ("res.innerHTML = `<span class=\"text-red-400\">Error: ${escHtml(e.message)}</span>`;", "res.innerHTML = safeHtml(`<span class=\"text-red-400\">Error: ${escHtml(e.message)}</span>`);"),
    # 10409
    ("tbody.innerHTML = rows.map(r => {", "tbody.innerHTML = safeHtml(rows.map(r => {"),
    ("    </tr>`;\n  }).join('');", "    </tr>`;\n  }).join(''));"),
    # 10952
    ("tbody.innerHTML = `<tr><td colspan=\"10\" class=\"px-4 py-8 text-center text-slate-600\">No ${status.toLowerCase()} orders</td></tr>`;", "tbody.innerHTML = safeHtml(`<tr><td colspan=\"10\" class=\"px-4 py-8 text-center text-slate-600\">No ${status.toLowerCase()} orders</td></tr>`);"),
    # 11910
    ("msg.innerHTML = `<span style=\"color:#22c55e\">Trade 2FA set to ${method}.</span>`;", "msg.innerHTML = safeHtml(`<span style=\"color:#22c55e\">Trade 2FA set to ${method}.</span>`);"),
    # 12689
    ("t.innerHTML = `${icons[type]||icons.info}\n    <div class=\"ta-toast-body\">\n      <div class=\"ta-toast-title\">${title||titles[type]||''}</div>\n      <div class=\"ta-toast-msg\">${msg}</div>\n    </div>\n    <button class=\"ta-toast-x\" onclick=\"taDismiss(this.parentElement)\">×</button>\n    <div class=\"ta-bar\" style=\"animation-duration:${ms}ms\"></div>`;", "t.innerHTML = safeHtml(`${icons[type]||icons.info}\n    <div class=\"ta-toast-body\">\n      <div class=\"ta-toast-title\">${title||titles[type]||''}</div>\n      <div class=\"ta-toast-msg\">${msg}</div>\n    </div>\n    <button class=\"ta-toast-x\" onclick=\"taDismiss(this.parentElement)\">×</button>\n    <div class=\"ta-bar\" style=\"animation-duration:${ms}ms\"></div>`);")
]

for old, new in replacements:
    if old not in html:
        print(f"Failed to find: {old[:50]}")
    html = html.replace(old, new)

with open("web/static/index.html", "w") as f:
    f.write(html)
