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
    ("res.innerHTML = `<span class=\"text-red-400\">Error: ${escHtml(e.message)}</span>`;", "res.innerHTML = safeHtml(`<span class=\"text-red-400\">Error: ${escHtml(e.message)}</span>`);"),
    ("tbody.innerHTML = `<tr><td colspan=\"10\" class=\"px-4 py-8 text-center text-slate-600\">No ${status.toLowerCase()} orders</td></tr>`;", "tbody.innerHTML = safeHtml(`<tr><td colspan=\"10\" class=\"px-4 py-8 text-center text-slate-600\">No ${status.toLowerCase()} orders</td></tr>`);"),
    ("row.innerHTML = `", "row.innerHTML = safeHtml(`"),
    ("container.insertAdjacentHTML('beforeend', html);", "container.insertAdjacentHTML('beforeend', safeHtml(html));"),
    ("reportEl.innerHTML = verdictCard + (existing.includes('agents finish') ? '' : existing);", "reportEl.innerHTML = safeHtml(verdictCard + (existing.includes('agents finish') ? '' : existing));"),
    ("body.innerHTML = (verdictCard || '') + renderMd(content||'');", "body.innerHTML = safeHtml((verdictCard || '') + renderMd(content||''));"),
    ("tbody.innerHTML = `<tr><td colspan=\"5\" class=\"px-5 py-4 text-red-400 text-sm\">Error: ${escHtml(e.message)}</td></tr>`;", "tbody.innerHTML = safeHtml(`<tr><td colspan=\"5\" class=\"px-5 py-4 text-red-400 text-sm\">Error: ${escHtml(e.message)}</td></tr>`);"),
]

for old, new in replacements:
    html = html.replace(old, new)

with open("web/static/index.html", "w") as f:
    f.write(html)
