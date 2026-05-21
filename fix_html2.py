import re

with open("web/static/index.html", "r") as f:
    html = f.read()

# 1. Inject DOMPurify
if "dompurify" not in html:
    html = html.replace('</head>', '  <script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.8/purify.min.js"></script>\n</head>')

# 2. Add safeHtml helper if missing
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

# Function to wrap RHS in safeHtml
def wrap_safe(match):
    prefix = match.group(1)
    rhs = match.group(2)
    # Don't wrap if already wrapped
    if rhs.startswith('safeHtml(') or rhs.startswith('DOMPurify.sanitize(') or "''" in rhs or '""' in rhs:
        return match.group(0)
    # Simple check: if it's just a constant string or empty, ignore
    if rhs.strip() in ("''", '""', "``"):
        return match.group(0)
    
    return f"{prefix}safeHtml({rhs})"

# Replace .innerHTML = ...; where ... doesn't have semicolons (single line)
html = re.sub(r'(\.innerHTML\s*=\s*)([^;]+;)', wrap_safe, html)

# Replace .insertAdjacentHTML(pos, ...); 
def wrap_adjacent(match):
    prefix = match.group(1)
    pos = match.group(2)
    rhs = match.group(3)
    return f"{prefix}{pos}, safeHtml({rhs}))"

html = re.sub(r'(\.insertAdjacentHTML\s*\(\s*)([\'"][a-zA-Z]+[\'"]\s*,\s*)([^\)]+)\)', wrap_adjacent, html)

with open("web/static/index.html", "w") as f:
    f.write(html)
