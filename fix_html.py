import re

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
    html = html.replace('<script>', '<script>\n' + helper)

# 3. Replace .innerHTML = X with .innerHTML = safeHtml(X)
# We will use regex to find .innerHTML = <stuff>;
# But this is tricky because of multi-line template literals.
# Since we have the exact line numbers from CodeQL (approximate):
# 9940, 12689, 11910, 10952, 10409, 7176, 6665, 6412, 6292.
