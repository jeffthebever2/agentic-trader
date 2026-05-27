#!/usr/bin/env bash
set -euo pipefail

# Anti-AI Design Auditor v3
# Scans HTML/CSS/JS/TS/React/Vue/Svelte/Astro/full repos/repo trees/URLs for obvious AND niche AI/vibe-coded design tells.
# No browser required. Uses Python 3 standard library only.
#
# Usage:
#   bash anti-ai-design-auditor-v3.sh ./repo
#   bash anti-ai-design-auditor-v3.sh ./index.html
#   bash anti-ai-design-auditor-v3.sh repo_tree.md
#   bash anti-ai-design-auditor-v3.sh https://example.com
#
# Env:
#   OUT_DIR=anti-ai-audit-output-v3
#   MAX_FILES=4500
#   MAX_FILE_KB=1100
#   INCLUDE_MD=0|1
#   STRICTNESS=1|2|3       # 1 balanced, 2 strict, 3 brutal/niche-heavy
#   FETCH_TIMEOUT=18

TARGET="${1:-.}"
OUT_DIR="${OUT_DIR:-anti-ai-audit-output-v3}"
MAX_FILES="${MAX_FILES:-4500}"
MAX_FILE_KB="${MAX_FILE_KB:-1100}"
FETCH_TIMEOUT="${FETCH_TIMEOUT:-18}"
INCLUDE_MD="${INCLUDE_MD:-0}"
STRICTNESS="${STRICTNESS:-2}"
mkdir -p "$OUT_DIR"

python3 - "$TARGET" "$OUT_DIR" "$MAX_FILES" "$MAX_FILE_KB" "$FETCH_TIMEOUT" "$INCLUDE_MD" "$STRICTNESS" <<'PY'
import os, re, sys, json, csv, math, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
from html import escape

TARGET = sys.argv[1]
OUT_DIR = Path(sys.argv[2])
MAX_FILES = int(sys.argv[3])
MAX_FILE_KB = int(sys.argv[4])
FETCH_TIMEOUT = int(sys.argv[5])
INCLUDE_MD = sys.argv[6] == '1'
STRICTNESS = max(1, min(3, int(sys.argv[7])))
OUT_DIR.mkdir(parents=True, exist_ok=True)

IGNORE_DIRS = {
    '.git', '.hg', '.svn', 'node_modules', '.next', '.nuxt', '.svelte-kit', 'dist', 'build',
    'coverage', '.turbo', '.vercel', '.cache', '.parcel-cache', 'vendor', '__pycache__',
    'target', 'out', '.output', 'storybook-static', '.idea', '.vscode', '.venv', 'venv',
    '.venv-torch', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.backtest_cache', 'tmp',
    'site-packages', '__MACOSX', '.DS_Store', 'logs', '.wrangler', '.cloudflare', '.expo',
    'android', 'ios', '.gradle', '.pnpm-store', '.yarn', '.vite', '.angular', '.parcel-cache'
}
FRONTEND_DIR_HINTS = {
    'web', 'app', 'apps', 'src', 'pages', 'components', 'routes', 'public', 'static', 'styles',
    'assets', 'client', 'frontend', 'ui', 'views', 'layouts', 'theme', 'design-system'
}
EXTS = {
    '.html', '.htm', '.css', '.scss', '.sass', '.less', '.js', '.jsx', '.ts', '.tsx', '.vue',
    '.svelte', '.astro', '.mdx', '.json', '.yml', '.yaml', '.mjs', '.cjs'
}
if INCLUDE_MD:
    EXTS.add('.md')
STYLE_EXTS = {'.css', '.scss', '.sass', '.less'}
MARKUP_EXTS = {'.html', '.htm', '.vue', '.svelte', '.astro', '.jsx', '.tsx', '.mdx'}
SCRIPT_EXTS = {'.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte', '.astro', '.mjs', '.cjs'}

CATEGORY_CAPS = {
    'palette': 28,
    'surface': 28,
    'composition': 30,
    'component_cliches': 30,
    'motion': 26,
    'copy': 24,
    'iconography': 16,
    'typography': 18,
    'state_depth': 22,
    'a11y': 22,
    'design_system': 24,
    'domain_fit': 20,
    'repo_hygiene': 14,
}
CATEGORY_NAMES = {
    'palette': 'Palette & color tells',
    'surface': 'Surface, glow, blur, glass',
    'composition': 'Composition and layout rhythm',
    'component_cliches': 'Component/library clichés',
    'motion': 'Motion and animation taste',
    'copy': 'Copywriting AI smell',
    'iconography': 'Iconography and decoration',
    'typography': 'Typography decisions',
    'state_depth': 'State depth and product reality',
    'a11y': 'Accessibility basics',
    'design_system': 'Design system maturity',
    'domain_fit': 'Domain specificity vs template UI',
    'repo_hygiene': 'Repo hygiene / scan quality',
}

# severity: info/low/medium/high/critical. weight adds to AI-risk. negative weights reduce risk.
RULES = [
    # Palette / color: obvious + niche tells
    ('palette', 'critical', 'Purple/blue/cyan gradient stack', r'(from|via|to)-(purple|violet|indigo|blue|cyan|fuchsia)-(300|400|500|600|700|800|900)|linear-gradient\([^\)]*(purple|violet|indigo|blue|cyan|fuchsia)', 6),
    ('palette', 'high', 'Gradient-text headline treatment', r'(bg-clip-text|text-transparent).{0,120}(gradient|from-|to-)|gradient-text', 6),
    ('palette', 'high', 'Default dark SaaS background palette', r'(#020617|#0f172a|#111827|#18181b|bg-(slate|zinc|neutral|gray)-(900|950)|from-slate-950|to-slate-900)', 4),
    ('palette', 'medium', 'Primary/secondary/accent-only color language', r'(--(primary|secondary|accent|background|foreground|muted|card|popover)\b|primary-foreground|secondary-foreground)', 3),
    ('palette', 'medium', 'AI candy accent overload', r'(cyan|violet|purple|fuchsia|blue|emerald|pink).{0,40}(glow|gradient|accent|ring|shadow|blur)', 3),
    ('palette', 'medium', 'Random rainbow section accents', r'(from-red|from-orange|from-amber|from-yellow|from-green|from-emerald|from-cyan|from-blue|from-indigo|from-purple|from-pink)', 2),
    ('palette', 'low', 'HSL shadcn default variable palette', r'--(background|foreground|card|card-foreground|popover|popover-foreground|primary|primary-foreground):\s*\d+\s+\d+%\s+\d+%', 2),

    # Surface and decoration
    ('surface', 'critical', 'Glassmorphism recipe', r'(backdrop-blur|bg-white/10|bg-white/5|border-white/10|border-white/20|supports-\[backdrop-filter\])', 7),
    ('surface', 'critical', 'Decorative blurred orb/blob background', r'(absolute|fixed).{0,160}(blur-3xl|blur-2xl|rounded-full|radial-gradient|blob|orb).{0,120}(opacity-20|opacity-30|opacity-40|pointer-events-none)', 7),
    ('surface', 'high', 'Generic rounded shadow card formula', r'rounded-(xl|2xl|3xl).{0,120}(shadow-(lg|xl|2xl)|border|p-(4|5|6|8))', 5),
    ('surface', 'high', 'Gradient border wrapper', r'(p-\[1px\]|border-gradient|gradient-border).{0,120}(bg-gradient|from-|to-)', 5),
    ('surface', 'medium', 'Soft glow shadow as fake polish', r'(shadow-\[0_0_|drop-shadow|shadow-(cyan|purple|blue|violet|fuchsia)|blur-\[)', 4),
    ('surface', 'medium', 'Noise/grid overlay without product reason', r'(noise|grain|bg-grid|grid-pattern|dot-pattern|radial-mask|mask-image)', 3),
    ('surface', 'medium', 'Overuse of opacity overlays', r'(bg-black/\d+|bg-white/\d+|opacity-(10|20|30|40|50)).{0,100}(absolute|inset-0|overlay)', 2),

    # Composition/layout
    ('composition', 'critical', 'Huge centered hero formula', r'(min-h-screen|h-screen|py-(24|28|32|36)).{0,220}(text-center|items-center|justify-center).{0,220}(max-w-(3xl|4xl|5xl|6xl|7xl)|mx-auto)', 8),
    ('composition', 'critical', '3-card feature grid formula', r'(grid-cols-3|md:grid-cols-3|lg:grid-cols-3).{0,180}(feature|card|rounded|shadow|border|icon)', 8),
    ('composition', 'high', 'Same max-width centered sections repeated', r'(container mx-auto|max-w-(5xl|6xl|7xl) mx-auto).{0,80}(px-(4|6|8)|py-(12|16|20|24))', 5),
    ('composition', 'high', 'Stacked SaaS landing rhythm', r'(py-(16|20|24|32)).{0,80}(section|features|pricing|testimonials|faq)', 5),
    ('composition', 'high', 'Floating dashboard mockup hero', r'(dashboard|mockup|preview).{0,100}(rounded-(2xl|3xl)|shadow-2xl|border).{0,120}(absolute|relative|overflow-hidden)', 5),
    ('composition', 'medium', 'Split hero with screenshot card', r'(lg:grid-cols-2|grid-cols-2).{0,160}(hero|headline|cta|screenshot|preview)', 3),
    ('composition', 'medium', 'Every section uses same card/list/grid grammar', r'(space-y-(8|10|12|16)|gap-(6|8|10|12)).{0,100}(grid|card|section)', 3),
    ('composition', 'medium', 'AI-generated decorative top/bottom fade masks', r'(bg-gradient-to-(t|b|r|l)).{0,80}(from-transparent|to-transparent|via-background|from-background)', 2),

    # Component cliches
    ('component_cliches', 'critical', 'shadcn/ui default gravity', r'@/components/ui/(button|card|badge|input|dialog|sheet|tabs|dropdown-menu)|<Card\b|<CardHeader\b|<CardContent\b|<Button\b|class-variance-authority|cva\(', 7),
    ('component_cliches', 'high', 'Button variants look untouched/default', r'(variant:\s*["\'](default|secondary|ghost|outline|destructive)["\']|size:\s*["\'](sm|lg|icon)["\'])', 5),
    ('component_cliches', 'high', 'Rounded pill badge eyebrow before hero', r'(badge|eyebrow|pill|New|Beta|Introducing).{0,120}(rounded-full|text-xs|uppercase|tracking-wider|border)', 5),
    ('component_cliches', 'high', 'Generic CTA button dimensions', r'(rounded-full|rounded-lg).{0,100}(px-6|px-8|h-10|h-11|py-3).{0,100}(font-medium|font-semibold)', 5),
    ('component_cliches', 'high', 'Icon circle inside feature card', r'(rounded-(full|xl)|size-(10|12)|w-10|h-10).{0,120}(icon|Icon|lucide|svg).{0,180}(card|feature)', 5),
    ('component_cliches', 'medium', 'Lucide icon pack used as main visual language', r'(lucide-react|from ["\']lucide-react|@lucide/|<Sparkles|<Zap|<Shield|<Rocket|<Brain|<Cpu|<Bot)', 4),
    ('component_cliches', 'medium', 'Default card primitives repeated', r'(Card|card).{0,100}(rounded|border|shadow|p-|bg-card)', 3),
    ('component_cliches', 'medium', 'Template testimonial/pricing components', r'(testimonial|pricing|faq|trusted by|logo cloud|avatars|stars).{0,120}(card|grid|section)', 3),
    ('component_cliches', 'medium', 'Tab/accordion/sheet stack without custom system', r'(Tabs|Accordion|Sheet|Dialog|DropdownMenu|Command).{0,100}(Content|Trigger|Item)', 3),

    # Motion and interaction: obvious + niche
    ('motion', 'critical', 'transition-all duration-300 default everywhere', r'transition-all.{0,60}duration-300|duration-300.{0,60}transition-all', 7),
    ('motion', 'critical', 'Hover scale card lift formula', r'(hover:scale-(105|\[1\.02\]|102)|whileHover=\{\{\s*scale:\s*(1\.02|1\.03|1\.05)).{0,120}(card|group|rounded|shadow)', 7),
    ('motion', 'high', 'Framer fade-up recipe', r'(framer-motion|motion\.div).{0,220}(initial=\{\{\s*opacity:\s*0|animate=\{\{\s*opacity:\s*1|y:\s*(20|24|30|40))', 6),
    ('motion', 'high', 'Index-based stagger formula', r'(delay:\s*index\s*\*\s*0\.\d+|transitionDelay:\s*`\$\{.*index.*\}|staggerChildren)', 5),
    ('motion', 'high', 'Animate pulse/bounce/ping as filler polish', r'animate-(pulse|bounce|ping|spin)', 4),
    ('motion', 'medium', 'Spring physics without interaction reason', r'(type:\s*["\']spring["\']|stiffness:\s*\d+|damping:\s*\d+|mass:\s*\d+)', 3),
    ('motion', 'medium', 'One-size-fits-all easing/duration', r'(duration-(200|300|500)|ease-in-out|ease-out).{0,80}(transition|hover|motion)', 3),
    ('motion', 'medium', 'Scroll reveal / reveal-on-view trope', r'(whileInView|viewport=\{|IntersectionObserver|reveal|fade-in|slide-up|data-aos)', 4),
    ('motion', 'medium', 'Magnetic/spotlight/glow cursor trend', r'(spotlight|magnetic|cursor-glow|mouse-position|onMouseMove).{0,140}(gradient|radial|mask|transform)', 4),
    ('motion', 'low', 'Animation exists', r'(@keyframes|animation:|transition:|framer-motion|motion\.|gsap|lottie|rive)', 1),

    # Copy / product language
    ('copy', 'critical', 'Generic AI/SaaS hype copy', r'\b(unlock|seamless|effortless|powerful|supercharge|elevate|revolutionize|transform your|next[- ]generation|all[- ]in[- ]one|beautifully crafted|intuitive|delightful|streamline|cutting[- ]edge)\b', 5),
    ('copy', 'high', 'AI value prop phrase stack', r'\b(experience the future|built for the future|at your fingertips|smarter workflows|AI-powered|harness the power|boost productivity|save time and money|focus on what matters)\b', 5),
    ('copy', 'high', 'Landing-page section boilerplate', r'\b(Features|Testimonials|Pricing|FAQ|Get Started|Trusted by|Everything you need|Built for teams|How it works|Why choose us)\b', 4),
    ('copy', 'medium', 'Vague benefit nouns instead of domain language', r'\b(insights|workflow|collaboration|scale|growth|productivity|performance|innovation|experience|solution|platform)\b', 2),
    ('copy', 'medium', 'Template CTA language', r'\b(Get started|Start free|Book a demo|Learn more|Try it now|Join waitlist|Explore features)\b', 3),
    ('copy', 'medium', 'AI-written adjective pile', r'\b(robust|scalable|flexible|beautiful|elegant|seamless|powerful|advanced|smart|simple)\b.{0,60}\b(solution|platform|experience|interface|dashboard)\b', 3),

    # Iconography / decorative tells
    ('iconography', 'high', 'Sparkles/Zap/Rocket/Brain icon cliché', r'(<(Sparkles|Zap|Rocket|Brain|Bot|Wand2|Stars|Gem|ShieldCheck|Workflow)|\b(Sparkles|Zap|Rocket|Brain|Bot|Wand2|Stars)\b)', 5),
    ('iconography', 'medium', 'Emoji used as product iconography', r'[✨🚀⚡🔥🎯💡🧠🛡️⭐✅]', 3),
    ('iconography', 'medium', 'Generic SVG blobs and decorative paths', r'<svg[^>]{0,200}(blob|gradient|defs|filter|feGaussianBlur|radialGradient)', 3),
    ('iconography', 'medium', 'Icon-only visual hierarchy', r'(icon|Icon).{0,100}(w-5|h-5|size-5|w-6|h-6).{0,100}(text-(primary|muted|foreground|white))', 2),

    # Typography
    ('typography', 'high', 'Text-balance/tracking-tight/clamp combo', r'(text-balance|tracking-tight).{0,120}(text-(4xl|5xl|6xl|7xl|8xl)|clamp\()', 5),
    ('typography', 'medium', 'Default modern font stack only', r'(Inter|Geist|Satoshi|Manrope).{0,80}(font|sans|variable)|font-sans', 2),
    ('typography', 'medium', 'Hero headline size ramp template', r'(text-4xl.{0,60}sm:text-5xl.{0,60}lg:text-6xl|text-5xl.{0,60}md:text-7xl)', 4),
    ('typography', 'medium', 'Muted foreground paragraph under headline', r'(text-muted-foreground|text-slate-400|text-gray-400).{0,120}(max-w-(2xl|3xl)|mx-auto)', 3),

    # State depth and real product checks
    ('state_depth', 'medium', 'Loading state exists', r'\b(loading|skeleton|aria-busy|spinner|isLoading|pending)\b', -2),
    ('state_depth', 'medium', 'Empty state exists', r'\b(empty state|empty-state|no results|nothing here|isEmpty|EmptyState)\b', -3),
    ('state_depth', 'medium', 'Error/retry state exists', r'\b(error state|error-state|retry|try again|failed to|catch\(|onError|ErrorBoundary)\b', -3),
    ('state_depth', 'medium', 'Offline/degraded state exists', r'\b(offline|degraded|stale|cached|last updated|sync failed|reconnect)\b', -3),
    ('state_depth', 'medium', 'Disabled/busy/success states exist', r'\b(disabled|success|warning|danger|aria-disabled|aria-live|toast)\b', -2),

    # Accessibility
    ('a11y', 'critical', 'Reduced motion support exists', r'prefers-reduced-motion|useReducedMotion|motion-reduce|MotionConfig', -8),
    ('a11y', 'high', 'Keyboard focus treatment exists', r'focus-visible|:focus-visible|focus:ring|outline-offset|focus:outline|focus-within', -5),
    ('a11y', 'medium', 'ARIA/semantic care exists', r'aria-|role=|sr-only|visually-hidden|alt=|label htmlFor|<label', -2),
    ('a11y', 'medium', 'Color-only status risk', r'(text-(green|red|yellow|emerald|rose)-\d+).{0,80}(status|badge|alert|state)', 3),

    # Design system maturity
    ('design_system', 'high', 'Semantic tokens exist', r'--(surface|panel|ink|muted|danger|warning|success|accent|focus|radius|space|motion|ease|duration|elevation|border)', -5),
    ('design_system', 'medium', 'Component-specific tokens exist', r'--(button|card|nav|sidebar|input|modal|toast|chart|status)-', -4),
    ('design_system', 'medium', 'Only generic shadcn tokens detected', r'--(background|foreground|card|card-foreground|primary|primary-foreground|secondary|secondary-foreground|muted|muted-foreground|accent|accent-foreground|border|input|ring)', 2),
    ('design_system', 'medium', 'Tailwind utility soup / no extracted component language', r'(className=|class=)["\'][^"\']{180,}["\']', 3),
    ('design_system', 'medium', 'Magic numbers instead of spacing system', r'(p|m|gap|top|left|right|bottom|translate)-\[(7|13|17|23|31|37|41|53|97)px\]', 2),

    # Domain fit
    ('domain_fit', 'high', 'Template nouns dominate over domain nouns', r'\b(platform|solution|workflow|insights|features|dashboard|experience)\b', 2),
    ('domain_fit', 'medium', 'Noisy fake stats/social proof', r'\b(10k\+|100k\+|99\.9%|trusted by|users worldwide|companies)\b', 3),
]

ABSENCE_RULES = [
    ('a11y', 'No reduced-motion support found while motion exists', 10),
    ('a11y', 'No visible keyboard focus system found', 9),
    ('state_depth', 'No loading/empty/error/offline state language found', 7),
    ('design_system', 'No semantic design tokens found', 9),
    ('domain_fit', 'Weak domain-specific design vocabulary', 6),
]

DOMAIN_NOUNS = {
    'weather','radar','storm','forecast','alert','warning','cell','velocity','reflectivity','model','county','station','nexrad','spc','watch','outlook',
    'stock','trade','portfolio','ticker','order','position','risk','volatility','earnings','drawdown','signal','backtest','cash','broker',
    'home','room','device','light','thermostat','sensor','camera','routine','scene','household','garage','door','energy',
    'flight','aircraft','airport','route','altitude','callsign','tail','runway','gate','metar','taf','aviation','plane',
    'police','dispatch','unit','pursuit','callout','citation','incident','backup','els','lspdfr','patrol',
    'server','deploy','build','log','api','database','cache','queue','worker','cron','auth','tenant','admin'
}

AI_TEMPLATE_COMBOS = [
    ('hero_gradient_badge_cta', ['gradient text', 'badge eyebrow', 'generic cta']),
    ('blob_glass_card_stack', ['decorative blurred orb', 'glassmorphism', 'rounded shadow card']),
    ('three_feature_lucide_grid', ['3-card feature grid', 'lucide', 'icon circle']),
    ('framer_fadeup_hoverlift', ['framer fade-up', 'hover scale', 'transition-all duration-300']),
    ('shadcn_default_dark_saas', ['shadcn', 'default dark saas', 'generic cta']),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def is_url(s):
    try:
        p = urllib.parse.urlparse(s)
        return p.scheme in ('http', 'https') and bool(p.netloc)
    except Exception:
        return False


def fetch_url(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'AntiAIAuditV3/1.0 no-browser design-analysis'})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        raw = r.read(min(3_000_000, int(r.headers.get('content-length') or 3_000_000)))
    return raw.decode('utf-8', errors='ignore')


def safe_read(path):
    try:
        b = Path(path).read_bytes()
        if len(b) > MAX_FILE_KB * 1024:
            b = b[:MAX_FILE_KB * 1024]
        return b.decode('utf-8', errors='ignore')
    except Exception:
        return ''


def looks_like_repo_tree(path, content):
    name = Path(str(path)).name.lower()
    if not name.endswith(('.md', '.txt', '.log')):
        return False
    sample = content[:30000]
    lines = [l.strip() for l in sample.splitlines() if l.strip()]
    if len(lines) < 30:
        return False
    pathish = sum(1 for l in lines if l.startswith('./') or re.search(r'\.(tsx|jsx|js|css|html|vue|svelte|astro|md)$', l))
    return pathish / max(len(lines), 1) > 0.55


def collect_files(target):
    items = []
    skipped = Counter()
    mode = 'repo'

    if is_url(target):
        html = fetch_url(target)
        items.append({'path': target, 'ext': '.html', 'text': html, 'size': len(html), 'source': 'url'})
        return items, skipped, 'url'

    p = Path(target)
    if not p.exists():
        raise SystemExit(f'Target not found: {target}')

    if p.is_file():
        text = safe_read(p)
        if looks_like_repo_tree(p, text):
            items.append({'path': str(p), 'ext': p.suffix.lower(), 'text': text, 'size': len(text), 'source': 'repo-tree'})
            return items, skipped, 'repo-tree'
        items.append({'path': str(p), 'ext': p.suffix.lower(), 'text': text, 'size': len(text), 'source': 'file'})
        return items, skipped, 'file'

    count = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('__')]
        for fn in files:
            fp = Path(root) / fn
            ext = fp.suffix.lower()
            if ext not in EXTS:
                skipped['non_frontend_ext'] += 1
                continue
            try:
                st = fp.stat()
            except Exception:
                skipped['stat_error'] += 1
                continue
            if st.st_size > MAX_FILE_KB * 1024:
                skipped['too_large'] += 1
                continue
            if count >= MAX_FILES:
                skipped['max_files_reached'] += 1
                continue
            text = safe_read(fp)
            if not text.strip():
                skipped['empty'] += 1
                continue
            rel = str(fp.relative_to(p))
            items.append({'path': rel, 'ext': ext, 'text': text, 'size': len(text), 'source': 'repo'})
            count += 1
    return items, skipped, mode


def clean_text_for_copy(text):
    # Strip tags enough for phrase matching, without depending on bs4.
    s = re.sub(r'<script[\s\S]*?</script>', ' ', text, flags=re.I)
    s = re.sub(r'<style[\s\S]*?</style>', ' ', s, flags=re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def regex_count(pattern, text):
    try:
        return len(re.findall(pattern, text, flags=re.I | re.S))
    except re.error:
        return 0


def snippet_around(text, pattern):
    try:
        m = re.search(pattern, text, flags=re.I | re.S)
    except re.error:
        return ''
    if not m:
        return ''
    a = max(0, m.start()-80); b = min(len(text), m.end()+80)
    snip = re.sub(r'\s+', ' ', text[a:b]).strip()
    return snip[:260]


def severity_mult(sev):
    return {'info':0.75,'low':0.9,'medium':1.0,'high':1.15,'critical':1.35}.get(sev,1.0)


def strict_mult(cat, sev):
    if STRICTNESS == 1:
        return 0.85 if sev in ('medium','low','info') else 1.0
    if STRICTNESS == 2:
        return 1.0
    # Brutal mode amplifies niche tells.
    if cat in {'component_cliches','motion','iconography','copy','composition','surface'}:
        return 1.25
    return 1.1


def frontend_weight(path):
    parts = set(Path(path).parts)
    if any(part in FRONTEND_DIR_HINTS for part in parts):
        return 1.15
    if Path(path).suffix.lower() in MARKUP_EXTS | STYLE_EXTS:
        return 1.1
    return 1.0


def analyze(items, skipped, mode):
    findings = []
    category_points = defaultdict(float)
    file_points = defaultdict(float)
    keyword_hits = Counter()
    all_text = '\n'.join(i['text'] for i in items)
    all_lower = all_text.lower()

    for item in items:
        text = item['text']
        path = item['path']
        fw = frontend_weight(path)
        # Limit markdown/repo-tree copy cleaning but match source too.
        searchable = text
        visible_copy = clean_text_for_copy(text)
        for cat, sev, name, pattern, weight in RULES:
            target_text = searchable
            # copy rules benefit from visible text plus source class names.
            if cat == 'copy':
                target_text = visible_copy + '\n' + searchable
            cnt = regex_count(pattern, target_text)
            if cnt == 0:
                continue
            if weight < 0:
                pts = weight * min(cnt, 8) * 0.55
            else:
                # Diminishing returns but repetition matters.
                pts = weight * (1 + math.log1p(cnt)) * severity_mult(sev) * strict_mult(cat, sev) * fw
            category_points[cat] += pts
            file_points[path] += max(0, pts)
            keyword_hits[name.lower()] += cnt
            findings.append({
                'file': path,
                'category': cat,
                'severity': sev,
                'finding': name,
                'matches': cnt,
                'points': round(pts, 2),
                'snippet': snippet_around(target_text, pattern),
            })

    # Absence checks across whole repo/content.
    has_motion = bool(re.search(r'(@keyframes|animation:|transition:|framer-motion|motion\.|gsap|lottie|rive|animate-)', all_text, re.I))
    has_reduced = bool(re.search(r'prefers-reduced-motion|useReducedMotion|motion-reduce|MotionConfig', all_text, re.I))
    has_focus = bool(re.search(r'focus-visible|:focus-visible|focus:ring|outline-offset|focus:outline|focus-within', all_text, re.I))
    has_states = bool(re.search(r'\b(loading|skeleton|empty state|empty-state|no results|error state|retry|offline|degraded|stale|last updated|aria-busy|ErrorBoundary)\b', all_text, re.I))
    has_tokens = bool(re.search(r'--(surface|panel|ink|muted|danger|warning|success|accent|focus|radius|space|motion|ease|duration|elevation|border|button|card|nav|sidebar|input|modal|toast|chart|status)-?', all_text, re.I))
    domain_count = sum(1 for n in DOMAIN_NOUNS if re.search(r'\b' + re.escape(n) + r's?\b', all_lower))

    absence_flags = []
    if has_motion and not has_reduced:
        absence_flags.append(('a11y', 'No reduced-motion support found while motion exists', 10))
    if not has_focus:
        absence_flags.append(('a11y', 'No visible keyboard focus system found', 9))
    if not has_states:
        absence_flags.append(('state_depth', 'No loading/empty/error/offline state language found', 7))
    if not has_tokens:
        absence_flags.append(('design_system', 'No semantic design tokens found', 9))
    if mode != 'repo-tree' and domain_count < 6:
        absence_flags.append(('domain_fit', 'Weak domain-specific design vocabulary', 6))

    for cat, name, points in absence_flags:
        points = points * (1.2 if STRICTNESS == 3 else 1.0)
        category_points[cat] += points
        findings.append({'file':'<repo-wide>', 'category':cat, 'severity':'high', 'finding':name, 'matches':0, 'points':round(points,2), 'snippet':''})

    # Niche combo detection.
    combo_findings = []
    names = ' | '.join(f['finding'].lower() for f in findings)
    def has_phrase(p):
        return p in names or p in all_lower
    for combo, phrases in AI_TEMPLATE_COMBOS:
        hits = 0
        for p in phrases:
            # Allow short phrase approximations.
            if any(tok in names for tok in p.split()[:2]) or p in names:
                hits += 1
        if hits >= 2:
            pts = 6 * hits * (1.25 if STRICTNESS >= 2 else 1.0)
            category_points['composition'] += pts * 0.35
            category_points['domain_fit'] += pts * 0.25
            category_points['component_cliches'] += pts * 0.40
            combo_findings.append({'combo': combo, 'hits': hits, 'points': round(pts,2)})
            findings.append({'file':'<repo-wide>', 'category':'composition', 'severity':'critical', 'finding':f'Niche AI-template combo detected: {combo}', 'matches':hits, 'points':round(pts,2), 'snippet':', '.join(phrases)})

    # Repo-tree/repo hygiene scoring.
    if mode == 'repo-tree':
        tree = all_text
        bad_tree = []
        for d in IGNORE_DIRS:
            if f'/{d}/' in tree or f'./{d}' in tree:
                bad_tree.append(d)
        if bad_tree:
            pts = min(14, 2 + len(bad_tree) * 1.4)
            category_points['repo_hygiene'] += pts
            findings.append({'file':'<repo-tree>', 'category':'repo_hygiene', 'severity':'medium', 'finding':'Repo tree includes generated/cache/dependency folders that should be excluded from design scans', 'matches':len(bad_tree), 'points':round(pts,2), 'snippet':', '.join(sorted(bad_tree)[:20])})

    # Normalize category scores with caps.
    raw_positive_total = 0.0
    category_scores = {}
    for cat, cap in CATEGORY_CAPS.items():
        pts = category_points.get(cat, 0.0)
        # Negative points reduce risk, but floor category at 0.
        score = max(0.0, min(cap, pts))
        category_scores[cat] = round(score, 2)
        raw_positive_total += score

    max_total = sum(CATEGORY_CAPS.values())
    ai_risk = round(min(100, (raw_positive_total / max_total) * 100 * 1.18), 1)
    human_score = round(max(0, 100 - ai_risk), 1)
    if ai_risk >= 75:
        verdict = 'OBVIOUSLY AI / TEMPLATE-CODED'
    elif ai_risk >= 55:
        verdict = 'Strong AI-design smell'
    elif ai_risk >= 35:
        verdict = 'Some AI/template patterns'
    elif ai_risk >= 18:
        verdict = 'Mostly human, a few generic tells'
    else:
        verdict = 'Low AI-design smell'

    # Top files and top findings.
    top_files = sorted(file_points.items(), key=lambda kv: kv[1], reverse=True)[:30]
    top_findings = sorted([f for f in findings if f['points'] > 0], key=lambda f: f['points'], reverse=True)[:60]

    # Useful extra stats.
    ext_counts = Counter(i['ext'] for i in items)
    scanned_bytes = sum(i['size'] for i in items)
    stats = {
        'target': TARGET,
        'mode': mode,
        'strictness': STRICTNESS,
        'scanned_files': len(items),
        'scanned_bytes': scanned_bytes,
        'extensions': dict(ext_counts),
        'skipped': dict(skipped),
        'domain_noun_hits': domain_count,
        'has_motion': has_motion,
        'has_reduced_motion': has_reduced,
        'has_focus_system': has_focus,
        'has_state_depth': has_states,
        'has_semantic_tokens': has_tokens,
        'combo_findings': combo_findings,
    }
    score = {
        'generated_at': now_iso(),
        'target': TARGET,
        'mode': mode,
        'strictness': STRICTNESS,
        'ai_design_risk': ai_risk,
        'human_design_score': human_score,
        'verdict': verdict,
        'category_scores': category_scores,
        'category_caps': CATEGORY_CAPS,
        'stats': stats,
    }
    return score, findings, top_files, top_findings


def write_csv(findings):
    path = OUT_DIR / 'anti-ai-findings-v3.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['file','category','severity','finding','matches','points','snippet'])
        w.writeheader()
        for row in findings:
            w.writerow(row)


def md_table(rows, headers):
    out = ['|' + '|'.join(headers) + '|', '|' + '|'.join(['---']*len(headers)) + '|']
    for row in rows:
        out.append('|' + '|'.join(str(x).replace('|','\\|') for x in row) + '|')
    return '\n'.join(out)


def write_report(score, findings, top_files, top_findings):
    cat_rows = []
    for cat, val in sorted(score['category_scores'].items(), key=lambda kv: kv[1], reverse=True):
        cap = score['category_caps'][cat]
        cat_rows.append([CATEGORY_NAMES.get(cat, cat), val, cap, f'{round((val/cap)*100 if cap else 0)}%'])
    file_rows = [[p, round(v,2)] for p,v in top_files[:20]] or [['No major per-file offenders', '0']]
    find_rows = [[f['severity'], CATEGORY_NAMES.get(f['category'], f['category']), f['finding'], f['points'], f['file']] for f in top_findings[:25]]
    if not find_rows:
        find_rows = [['info','None','No major findings','0','']]

    stats = score['stats']
    report = f"""# Anti-AI Design Audit v3

Generated: `{score['generated_at']}`  
Target: `{score['target']}`  
Mode: `{score['mode']}`  
Strictness: `{score['strictness']}`

## Overall score

- **AI design risk:** `{score['ai_design_risk']}/100`
- **Human design score:** `{score['human_design_score']}/100`
- **Verdict:** **{score['verdict']}**

## Category scores

{md_table(cat_rows, ['Category','Risk points','Cap','Filled'])}

## Scan health

- Scanned files: `{stats['scanned_files']}`
- Scanned bytes: `{stats['scanned_bytes']}`
- Domain-specific noun hits: `{stats['domain_noun_hits']}`
- Motion found: `{stats['has_motion']}`
- Reduced-motion support found: `{stats['has_reduced_motion']}`
- Focus system found: `{stats['has_focus_system']}`
- Real app states found: `{stats['has_state_depth']}`
- Semantic tokens found: `{stats['has_semantic_tokens']}`

## Top offending files

{md_table(file_rows, ['File','Risk points'])}

## Highest-value findings

{md_table(find_rows, ['Severity','Category','Finding','Points','File'])}

## What v3 is stricter about

This version catches niche AI tells that normal audits miss:

- Hero badge + gradient text + two CTA buttons
- Blurred purple/cyan blobs behind everything
- Glass card stacks with `border-white/10`
- `max-w-7xl mx-auto` repeated across every section
- 3-card feature grids with Lucide icons in circles
- shadcn defaults that were never turned into a brand system
- `transition-all duration-300` and hover-scale on everything
- Framer Motion fade-up/stagger animations with no product reason
- Copy like “unlock,” “seamless,” “powerful,” “revolutionize,” and “at your fingertips”
- Missing reduced-motion support, focus states, and real loading/error/empty/offline states
- Generic `primary/secondary/accent` tokens instead of domain-aware tokens

## Fix strategy

1. **Choose a domain-specific art direction.** Do not use a generic SaaS template.
2. **Replace decorative gradients with purposeful surfaces.** Use color for hierarchy, status, and navigation.
3. **Break the landing-page rhythm.** Avoid hero + features + pricing + FAQ unless the product actually needs it.
4. **Make components feel authored.** Buttons, cards, nav, inputs, and badges should have project-specific anatomy.
5. **Use motion only for feedback, state changes, or orientation.** Remove filler hover-scale/reveal animations.
6. **Write product-specific copy.** Replace hype words with concrete nouns and actions.
7. **Add product states.** Loading, empty, error, offline, disabled, stale, and retry states make the UI feel real.
8. **Add accessibility polish.** Focus states and reduced-motion support are non-negotiable.

"""
    (OUT_DIR / 'ANTI_AI_DESIGN_AUDIT_V3.md').write_text(report, encoding='utf-8')


def write_prompts(score):
    prompt = f"""# Anti-AI Design Fix Prompt v3

Use this audit to make the UI look less AI-generated and more intentionally designed.

Input files to read:
- `ANTI_AI_DESIGN_AUDIT_V3.md`
- `anti-ai-score-v3.json`
- `anti-ai-findings-v3.csv`
- `NICHE_AI_DESIGN_TELLS.md`
- `HUMAN_DESIGN_REWRITE_BRIEF.md`

Current audit result:
- AI design risk: `{score['ai_design_risk']}/100`
- Verdict: `{score['verdict']}`

## Instructions

Do **not** redesign by adding more gradients, glassmorphism, blobs, or hover-scale animations.

First write a plan to `ui-updates.md`. Do not code until the plan exists.

The plan must include:
1. The current AI-looking design tells found in the repo
2. The chosen human design direction
3. Color system replacement
4. Surface/card replacement
5. Layout rhythm changes
6. Component anatomy changes
7. Typography changes
8. Motion/microinteraction rules
9. Loading/empty/error/offline/disabled state improvements
10. Accessibility improvements
11. Exact files to change
12. Files and logic not to touch

## Anti-AI design requirements

Avoid:
- Purple/blue/cyan gradient hero sections
- Gradient text as the main visual identity
- Glass cards and `backdrop-blur` everywhere
- Floating blurred blobs/orbs
- `max-w-7xl mx-auto` on every section
- Hero badge + huge headline + two CTA buttons
- Three-card feature grids with Lucide icons
- Default shadcn cards/buttons/badges with no brand layer
- `transition-all duration-300`
- Hover-scale on every card
- Framer Motion fade-up/stagger animation spam
- Generic copy like “unlock,” “seamless,” “powerful,” “revolutionize,” “next-generation”

Do instead:
- Make the interface fit the product domain
- Use color for status, priority, affordance, and brand memory
- Create a real surface system: base, panel, raised, inset, critical, interactive
- Use asymmetry, density changes, and section rhythm intentionally
- Replace generic cards with domain-specific modules
- Use motion for state changes, feedback, navigation, and attention only
- Add reduced-motion support
- Add real app states: loading, empty, error, offline, stale, disabled, syncing
- Add focus-visible styles and keyboard-friendly controls

## Verification

After changes:
- Run the app locally
- Check mobile and desktop
- Check auth/data loading still works
- Check no broken routes
- Re-run the auditor and reduce AI design risk by at least 30%
"""
    (OUT_DIR / 'ANTI_AI_FIX_PROMPT_V3.md').write_text(prompt, encoding='utf-8')

    brief = """# Human Design Rewrite Brief

## Goal

Move the interface from “AI-generated template” to “human-authored product.”

## Replace AI tells with human design decisions

| AI-looking tell | Human-designed replacement |
|---|---|
| Purple/cyan gradient background | Domain-specific palette with restrained accents |
| Glass cards everywhere | Surface levels with functional contrast |
| Hero badge + giant headline | Product-specific entry point and useful first screen |
| Three identical feature cards | Different modules based on actual user tasks |
| Lucide icon circles | Purposeful symbols, labels, data, or custom icons |
| Hover-scale everywhere | Feedback based on control type and consequence |
| Framer fade-up spam | Motion for state, navigation, and progress |
| Generic SaaS copy | Concrete product language and user goals |
| `primary/secondary/accent` only | Semantic tokens for product states and surfaces |

## Motion taste rules

- Motion should explain what changed.
- Motion should show cause and effect.
- Motion should respect reduced-motion preferences.
- Motion should not be the brand by itself.
- Hover is not a personality system.
- Loading states should communicate progress or fallback, not just pulse.
- Microinteractions should make controls feel responsive, not flashy.

## Color taste rules

- Do not use gradient text as a personality replacement.
- Pick one real accent family and one status system.
- Status colors must mean something consistent.
- Backgrounds should support content, not compete with it.
- Avoid random cyan/purple glow unless the product concept truly calls for it.

## Layout taste rules

- Avoid identical vertical sections.
- Mix dense/productive areas with calm reading areas.
- Use asymmetry where it improves scanning.
- Every card/module should have a specific job.
- Break template rhythm with domain-specific information architecture.
"""
    (OUT_DIR / 'HUMAN_DESIGN_REWRITE_BRIEF.md').write_text(brief, encoding='utf-8')


def write_niche_docs():
    niche = """# Niche AI Design Tells v3

These are the subtle patterns that make a website feel obviously AI/vibe-coded even when it looks polished.

## 1. The AI SaaS hero stack

Common pattern:

```txt
small pill badge → massive centered headline → gradient word → vague subheadline → two CTA buttons → floating dashboard mockup
```

Why it feels AI-made:
- It is a layout shortcut, not an information architecture decision.
- It works for almost anything, which means it belongs to nothing.

Human alternative:
- Start with the actual job the user came to do.
- Make the first screen useful, not just impressive.

## 2. Purple/cyan tech candy

Common pattern:

```txt
bg-slate-950 + purple/cyan gradients + blurred blobs + glowing borders
```

Why it feels AI-made:
- AI reaches for “tech” colors without a product reason.
- The same palette appears on AI tools, fake SaaS apps, dashboards, and portfolios.

Human alternative:
- Pick colors from the product domain: radar/status colors, finance risk colors, home-control warmth, aviation instrumentation, etc.

## 3. Glass card overload

Common pattern:

```txt
backdrop-blur bg-white/10 border-white/10 rounded-2xl shadow-xl
```

Why it feels AI-made:
- It creates visual polish without design hierarchy.
- Everything becomes equally shiny and equally unimportant.

Human alternative:
- Build surface levels: base, panel, raised, inset, critical, selected, disabled.

## 4. Lucide icon feature grid

Common pattern:

```txt
3 columns, each card has a round icon, heading, two-line description
```

Why it feels AI-made:
- Icons become decoration instead of function.
- Feature cards all have the same weight.

Human alternative:
- Use different module types based on importance and user flow.
- Replace generic icons with data, controls, screenshots, or domain-specific symbols.

## 5. Motion as filler

Common pattern:

```txt
transition-all duration-300 hover:scale-105 motion.div opacity 0 → 1 y 20
```

Why it feels AI-made:
- Everything moves the same way.
- Motion does not communicate state or consequence.

Human alternative:
- Define motion roles: feedback, navigation, reveal, progress, warning, completion.

## 6. Vague hype copy

Common pattern:

```txt
Unlock seamless powerful workflows with an intuitive next-generation platform.
```

Why it feels AI-made:
- The sentence sounds good but says nothing.

Human alternative:
- Use nouns and verbs from the actual product.
- Say what changes for the user.

## 7. Default component-library smell

Common pattern:

```txt
<Card><CardHeader><CardContent>
<Button variant="default">
<Badge variant="secondary">
```

Why it feels AI-made:
- The component library is visible instead of the product’s design system.

Human alternative:
- Wrap primitives into product components with domain names and distinct anatomy.

## 8. Missing real app states

Common pattern:
- No empty states
- No offline states
- No stale-data state
- No retry state
- No disabled/busy distinction

Why it feels AI-made:
- AI designs the happy path only.

Human alternative:
- Design for reality: loading, empty, partial, stale, degraded, error, retry, disabled, syncing, success.

## 9. Perfect symmetry everywhere

Common pattern:
- Centered everything
- Equal cards
- Equal gaps
- Equal section heights

Why it feels AI-made:
- It avoids hard design decisions.

Human alternative:
- Give important things more weight.
- Use asymmetry and density intentionally.

## 10. Generic tokens

Common pattern:

```css
--background;
--foreground;
--primary;
--secondary;
--accent;
```

Why it feels AI-made:
- The tokens describe UI mechanics, not product meaning.

Human alternative:

```css
--surface-map;
--surface-panel;
--status-live;
--status-stale;
--risk-warning;
--risk-critical;
--focus-ring;
```
"""
    (OUT_DIR / 'NICHE_AI_DESIGN_TELLS.md').write_text(niche, encoding='utf-8')

    tokens = """/* Anti-AI Human Design Tokens v3
   Starter tokens that push a project away from generic AI SaaS styling.
   Replace values with project-specific choices. */

:root {
  /* Surfaces: function first, not glass first */
  --surface-base: #111111;
  --surface-panel: #181818;
  --surface-raised: #202020;
  --surface-inset: #0b0b0b;
  --surface-selected: #23251f;

  /* Text */
  --ink-strong: #f2f0ea;
  --ink: #d7d3c8;
  --ink-muted: #9b968a;
  --ink-faint: #6f6a60;

  /* Borders and focus */
  --line-subtle: rgba(242, 240, 234, 0.10);
  --line-strong: rgba(242, 240, 234, 0.22);
  --focus-ring: #d7b46a;

  /* Product/status colors: use consistently */
  --status-live: #6fbf73;
  --status-stale: #d7b46a;
  --status-error: #d96c5f;
  --status-info: #7da7c7;
  --status-disabled: #5f5a52;

  /* Shape */
  --radius-control: 0.55rem;
  --radius-panel: 0.85rem;
  --radius-large: 1.25rem;

  /* Motion roles */
  --duration-feedback: 120ms;
  --duration-state: 180ms;
  --duration-navigation: 260ms;
  --ease-feedback: cubic-bezier(.2, 0, .1, 1);
  --ease-state: cubic-bezier(.2, .8, .2, 1);
  --ease-navigation: cubic-bezier(.16, 1, .3, 1);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.001ms !important;
  }
}

:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 3px;
}
"""
    (OUT_DIR / 'anti-ai-human-design-tokens-v3.css').write_text(tokens, encoding='utf-8')

    rubric = {
        'version': '3.0',
        'goal': 'Detect obvious and niche AI/vibe-coded website design patterns.',
        'categories': CATEGORY_NAMES,
        'high_risk_patterns': [
            'gradient hero with badge and generic CTA',
            'blurred orb/blob backgrounds',
            'glassmorphism card stacks',
            'three-card feature grids with Lucide icons',
            'shadcn defaults without a product design system',
            'transition-all duration-300 and hover-scale everywhere',
            'Framer fade-up reveal spam',
            'generic SaaS/AI copywriting',
            'missing reduced-motion/focus/app states',
            'generic primary/secondary/accent tokens'
        ],
        'strictness': {
            '1': 'Balanced: catches obvious patterns with fewer false positives.',
            '2': 'Strict: default, catches common and niche AI design tells.',
            '3': 'Brutal: amplifies niche patterns and is best before a major redesign.'
        }
    }
    (OUT_DIR / 'design-smell-rubric-v3.json').write_text(json.dumps(rubric, indent=2), encoding='utf-8')


def write_skill():
    skill_dir = OUT_DIR / '.agents' / 'skills' / 'anti-ai-design-review-v3'
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill = """# Anti-AI Design Review v3 Skill

## Purpose

Audit and improve interfaces so they do not look AI-generated, template-coded, or visually generic.

## Core rule

Do not fix AI-looking design by adding more decoration. Fix it by creating a clearer product-specific design system.

## Detect these AI tells

- Purple/cyan/blue gradient hero sections
- Gradient headline text
- Glassmorphism everywhere
- Blurred orb/blob backgrounds
- `max-w-7xl mx-auto` repeated through every section
- Hero badge + huge centered headline + two CTAs
- Three-card feature grids
- Lucide icons in circular feature badges
- Default shadcn cards/buttons/badges
- `transition-all duration-300`
- Hover-scale on every card
- Framer Motion fade-up/stagger spam
- Generic copy: unlock, seamless, powerful, elevate, revolutionize
- Missing loading, empty, error, offline, stale, disabled states
- Missing reduced-motion and focus-visible handling

## Redesign process

1. Identify AI-looking design tells.
2. Pick a domain-specific design direction.
3. Define semantic tokens.
4. Replace generic surfaces and cards.
5. Replace generic layout rhythm.
6. Rewrite vague copy with domain language.
7. Add real app states.
8. Add reduced-motion and focus states.
9. Verify no functionality was broken.

## Output requirements

Before changing code, write `ui-updates.md` with:

- Findings
- Design direction
- Token plan
- Component plan
- Motion rules
- State-depth plan
- Accessibility plan
- Files to change
- Files not to touch

Never copy a website exactly. Extract principles only.
"""
    (skill_dir / 'SKILL.md').write_text(skill, encoding='utf-8')


def write_triage(score):
    stats = score['stats']
    triage = f"""# Repo Triage v3

Target: `{score['target']}`  
Mode: `{score['mode']}`

## Best next scan targets

Run the auditor on actual UI source folders, not generated/cache folders.

Recommended commands:

```bash
OUT_DIR=anti-ai-web-audit-v3 STRICTNESS=3 bash anti-ai-design-auditor-v3.sh web
OUT_DIR=anti-ai-src-audit-v3 STRICTNESS=3 bash anti-ai-design-auditor-v3.sh src
OUT_DIR=anti-ai-static-audit-v3 STRICTNESS=3 bash anti-ai-design-auditor-v3.sh public
OUT_DIR=anti-ai-full-audit-v3 MAX_FILES=4500 STRICTNESS=2 bash anti-ai-design-auditor-v3.sh .
```

## Scan stats

```json
{json.dumps(stats, indent=2)}
```

## Ignore these during design audits

- `node_modules`
- `.venv`, `venv`, `.venv-torch`
- `dist`, `build`, `.next`
- cache folders
- generated artifacts
- test snapshots unless testing visual output
"""
    (OUT_DIR / 'REPO_TRIAGE_V3.md').write_text(triage, encoding='utf-8')


items, skipped, mode = collect_files(TARGET)
score, findings, top_files, top_findings = analyze(items, skipped, mode)

(OUT_DIR / 'anti-ai-score-v3.json').write_text(json.dumps(score, indent=2), encoding='utf-8')
write_csv(findings)
write_report(score, findings, top_files, top_findings)
write_prompts(score)
write_niche_docs()
write_skill()
write_triage(score)

# Top offending files text.
with (OUT_DIR / 'top-offending-files-v3.txt').open('w', encoding='utf-8') as f:
    for path, pts in top_files:
        f.write(f'{round(pts,2)}\t{path}\n')

print(json.dumps({
    'out_dir': str(OUT_DIR),
    'ai_design_risk': score['ai_design_risk'],
    'human_design_score': score['human_design_score'],
    'verdict': score['verdict'],
    'scanned_files': score['stats']['scanned_files'],
    'mode': score['mode'],
}, indent=2))
PY
