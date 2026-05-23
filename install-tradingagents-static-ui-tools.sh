#!/usr/bin/env bash
set -euo pipefail

# TradingAgents vanilla frontend UI/UX tools installer
# Works with current setup: FastAPI + web/static + plain HTML/CSS/vanilla JS + Chart.js
#
# Usage:
#   ./install-tradingagents-static-ui-tools.sh
#   ./install-tradingagents-static-ui-tools.sh --inject
#   ./install-tradingagents-static-ui-tools.sh --with-three
#   ./install-tradingagents-static-ui-tools.sh --inject --with-three
#
# What it does:
# - Downloads approved vanilla/CDN-compatible tools into web/static/vendor/
# - Creates premium-static-ui.css and premium-static-ui.js
# - Optionally injects the needed tags into web/static/index.html
#
# It does NOT:
# - create package.json
# - install React/Vite/Next
# - touch backend Python files
# - delete web/static

INJECT=0
WITH_THREE=0

for arg in "$@"; do
  case "$arg" in
    --inject) INJECT=1 ;;
    --with-three) WITH_THREE=1 ;;
    -h|--help)
      echo "Usage: $0 [--inject] [--with-three]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--inject] [--with-three]"
      exit 1
      ;;
  esac
done

echo "🚀 Installing TradingAgents static UI/UX tool stack..."

if [[ ! -d "web/static" ]]; then
  echo "❌ web/static not found."
  echo "Run this from the TradingAgents repo root."
  exit 1
fi

if [[ ! -f "web/static/index.html" ]]; then
  echo "❌ web/static/index.html not found."
  echo "This script expects the existing vanilla frontend to live in web/static/."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "❌ curl is required."
  exit 1
fi

mkdir -p web/static/vendor/css
mkdir -p web/static/vendor/js
mkdir -p web/static/vendor/dev
mkdir -p web/static/assets

download() {
  local url="$1"
  local out="$2"

  if [[ -f "$out" ]]; then
    echo "✅ Exists: $out"
    return 0
  fi

  echo "⬇️  Downloading: $out"
  curl -fL --retry 3 --connect-timeout 15 "$url" -o "$out"
}

echo ""
echo "🎨 Downloading CSS tools..."
download "https://cdnjs.cloudflare.com/ajax/libs/normalize/8.0.1/normalize.min.css" "web/static/vendor/css/normalize.min.css"
download "https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.css" "web/static/vendor/css/notyf.min.css"
download "https://cdn.jsdelivr.net/npm/tippy.js@6.3.7/dist/tippy.css" "web/static/vendor/css/tippy.css"
download "https://cdn.jsdelivr.net/npm/tabulator-tables@6.2.1/dist/css/tabulator.min.css" "web/static/vendor/css/tabulator.min.css"
download "https://cdn.jsdelivr.net/npm/sweetalert2@11/dist/sweetalert2.min.css" "web/static/vendor/css/sweetalert2.min.css"

echo ""
echo "🎬 Downloading animation/effects tools..."
download "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js" "web/static/vendor/js/gsap.min.js"
download "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js" "web/static/vendor/js/ScrollTrigger.min.js"
download "https://cdn.jsdelivr.net/npm/lenis@1.3.13/dist/lenis.min.js" "web/static/vendor/js/lenis.min.js"

echo ""
echo "💬 Downloading feedback/tooltip/modal tools..."
download "https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.js" "web/static/vendor/js/notyf.min.js"
download "https://cdn.jsdelivr.net/npm/tippy.js@6.3.7/dist/tippy-bundle.umd.min.js" "web/static/vendor/js/tippy-bundle.umd.min.js"
download "https://cdn.jsdelivr.net/npm/sweetalert2@11/dist/sweetalert2.all.min.js" "web/static/vendor/js/sweetalert2.all.min.js"
download "https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js" "web/static/vendor/js/fuse.min.js"

echo ""
echo "📈 Downloading chart/table tools..."
download "https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js" "web/static/vendor/js/chartjs-plugin-datalabels.min.js"
download "https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.1.0/dist/chartjs-plugin-annotation.min.js" "web/static/vendor/js/chartjs-plugin-annotation.min.js"
download "https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js" "web/static/vendor/js/hammer.min.js"
download "https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js" "web/static/vendor/js/chartjs-plugin-zoom.min.js"
download "https://cdn.jsdelivr.net/npm/tabulator-tables@6.2.1/dist/js/tabulator.min.js" "web/static/vendor/js/tabulator.min.js"
download "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js" "web/static/vendor/js/echarts.min.js"

echo ""
echo "🧩 Downloading icons/dev tools..."
download "https://unpkg.com/lucide@latest/dist/umd/lucide.min.js" "web/static/vendor/js/lucide.min.js"
download "https://cdn.jsdelivr.net/npm/axe-core@4.9.1/axe.min.js" "web/static/vendor/dev/axe.min.js"

if [[ "$WITH_THREE" -eq 1 ]]; then
  echo ""
  echo "🧪 Downloading optional Three.js WebGL tool..."
  download "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js" "web/static/vendor/js/three.min.js"
fi

echo ""
echo "🧱 Creating premium CSS helpers..."
cat > web/static/premium-static-ui.css <<'CSS'
/* ============================================
   TradingAgents Premium Static UI Layer
   Works with plain HTML/CSS/vanilla JS.
   ============================================ */

:root {
  color-scheme: dark;

  --ta-bg: #06070a;
  --ta-bg-soft: #090b11;
  --ta-surface: rgba(15, 23, 42, 0.72);
  --ta-surface-strong: rgba(15, 23, 42, 0.94);
  --ta-surface-hover: rgba(30, 41, 59, 0.78);
  --ta-border: rgba(148, 163, 184, 0.16);
  --ta-border-strong: rgba(148, 163, 184, 0.28);

  --ta-text: #e5edf8;
  --ta-muted: #94a3b8;
  --ta-muted-2: #64748b;

  --ta-primary: #38bdf8;
  --ta-primary-soft: rgba(56, 189, 248, 0.14);
  --ta-success: #22c55e;
  --ta-success-soft: rgba(34, 197, 94, 0.14);
  --ta-warning: #f59e0b;
  --ta-warning-soft: rgba(245, 158, 11, 0.15);
  --ta-danger: #ef4444;
  --ta-danger-soft: rgba(239, 68, 68, 0.15);

  --ta-radius-sm: 0.5rem;
  --ta-radius-md: 0.75rem;
  --ta-radius-lg: 1rem;
  --ta-radius-xl: 1.25rem;

  --ta-shadow-card: 0 18px 50px rgba(0, 0, 0, 0.28);
  --ta-shadow-soft: 0 8px 30px rgba(0, 0, 0, 0.22);

  --ta-ease: cubic-bezier(0.16, 1, 0.3, 1);
  --ta-fast: 160ms var(--ta-ease);
  --ta-med: 260ms var(--ta-ease);
}

html {
  background:
    radial-gradient(circle at top left, rgba(56, 189, 248, 0.08), transparent 30rem),
    radial-gradient(circle at 80% 10%, rgba(99, 102, 241, 0.08), transparent 28rem),
    var(--ta-bg);
}

body {
  background: transparent;
  color: var(--ta-text);
}

/* Premium animated background section. Add class="ta-mesh-bg" to hero/header panels. */
.ta-mesh-bg {
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at 20% 20%, rgba(56, 189, 248, 0.18), transparent 32rem),
    radial-gradient(circle at 80% 30%, rgba(99, 102, 241, 0.16), transparent 30rem),
    radial-gradient(circle at 45% 90%, rgba(34, 197, 94, 0.08), transparent 24rem),
    linear-gradient(135deg, rgba(15, 23, 42, 0.92), rgba(2, 6, 23, 0.98));
  background-size: 140% 140%;
  animation: taMeshShift 18s ease-in-out infinite alternate;
}

@keyframes taMeshShift {
  from { background-position: 0% 45%; }
  to { background-position: 100% 55%; }
}

/* Fixed mouse-reactive spotlight. Injected automatically by premium-static-ui.js. */
.ta-spotlight {
  position: fixed;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background:
    radial-gradient(
      620px circle at var(--ta-mouse-x, 50%) var(--ta-mouse-y, 50%),
      rgba(56, 189, 248, 0.075),
      transparent 42%
    );
  opacity: 0.9;
  mix-blend-mode: screen;
}

/* Keep main app above spotlight when needed. */
body > *:not(.ta-spotlight) {
  position: relative;
  z-index: 2;
}

/* Premium cards. Use on existing dashboard panels as class="ta-card". */
.ta-card {
  background: var(--ta-surface);
  border: 1px solid var(--ta-border);
  border-radius: var(--ta-radius-lg);
  box-shadow: var(--ta-shadow-card);
  backdrop-filter: blur(16px);
  transition:
    transform var(--ta-med),
    border-color var(--ta-med),
    background var(--ta-med),
    box-shadow var(--ta-med);
}

.ta-card:hover {
  transform: translateY(-2px);
  border-color: var(--ta-border-strong);
  background: var(--ta-surface-hover);
  box-shadow: 0 22px 60px rgba(0, 0, 0, 0.34);
}

.ta-glass {
  background: rgba(15, 23, 42, 0.62);
  border: 1px solid var(--ta-border);
  backdrop-filter: blur(18px);
}

.ta-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  min-height: 2.5rem;
  border-radius: var(--ta-radius-md);
  border: 1px solid var(--ta-border);
  padding: 0.6rem 0.95rem;
  color: var(--ta-text);
  background: rgba(30, 41, 59, 0.62);
  transition:
    transform var(--ta-fast),
    border-color var(--ta-fast),
    background var(--ta-fast),
    box-shadow var(--ta-fast);
}

.ta-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(56, 189, 248, 0.34);
  background: rgba(51, 65, 85, 0.78);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
}

.ta-btn:active {
  transform: translateY(0) scale(0.985);
}

.ta-btn-primary {
  border-color: rgba(56, 189, 248, 0.34);
  background: linear-gradient(135deg, rgba(14, 165, 233, 0.92), rgba(37, 99, 235, 0.92));
  color: white;
}

.ta-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 999px;
  border: 1px solid var(--ta-border);
  padding: 0.22rem 0.55rem;
  font-size: 0.78rem;
  font-weight: 650;
  color: var(--ta-muted);
  background: rgba(15, 23, 42, 0.62);
}

.ta-badge-success {
  color: #86efac;
  border-color: rgba(34, 197, 94, 0.28);
  background: var(--ta-success-soft);
}

.ta-badge-warning {
  color: #fcd34d;
  border-color: rgba(245, 158, 11, 0.28);
  background: var(--ta-warning-soft);
}

.ta-badge-danger {
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.28);
  background: var(--ta-danger-soft);
}

/* Skeletons */
.ta-skeleton {
  border-radius: var(--ta-radius-sm);
  background:
    linear-gradient(
      90deg,
      rgba(30, 41, 59, 0.55) 25%,
      rgba(51, 65, 85, 0.72) 45%,
      rgba(30, 41, 59, 0.55) 65%
    );
  background-size: 240% 100%;
  animation: taShimmer 1.45s ease-in-out infinite;
}

@keyframes taShimmer {
  0% { background-position: 120% 0; }
  100% { background-position: -120% 0; }
}

/* Empty/error/success state containers */
.ta-state {
  border: 1px dashed var(--ta-border-strong);
  border-radius: var(--ta-radius-xl);
  padding: 2rem;
  text-align: center;
  background: rgba(15, 23, 42, 0.38);
}

.ta-state-title {
  margin: 0 0 0.45rem;
  color: var(--ta-text);
  font-weight: 750;
}

.ta-state-text {
  margin: 0 auto;
  max-width: 38rem;
  color: var(--ta-muted);
}

/* Tables */
.ta-table-wrap {
  overflow: auto;
  border: 1px solid var(--ta-border);
  border-radius: var(--ta-radius-lg);
  background: rgba(15, 23, 42, 0.44);
}

.ta-table-wrap table {
  width: 100%;
  border-collapse: collapse;
}

.ta-table-wrap th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: var(--ta-muted);
  background: rgba(15, 23, 42, 0.92);
  backdrop-filter: blur(12px);
}

.ta-table-wrap th,
.ta-table-wrap td {
  padding: 0.85rem 1rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.10);
}

.ta-table-wrap tr {
  transition: background var(--ta-fast);
}

.ta-table-wrap tbody tr:hover {
  background: rgba(56, 189, 248, 0.045);
}

/* Toast / tooltip polish */
.notyf__toast {
  border-radius: var(--ta-radius-md) !important;
  box-shadow: var(--ta-shadow-soft) !important;
}

.tippy-box {
  border-radius: var(--ta-radius-sm);
  font-size: 0.82rem;
}

/* Command palette shell, if you build one later */
.ta-command-palette {
  position: fixed;
  top: 12vh;
  left: 50%;
  width: min(680px, calc(100vw - 2rem));
  transform: translateX(-50%);
  border: 1px solid var(--ta-border-strong);
  border-radius: var(--ta-radius-xl);
  background: rgba(2, 6, 23, 0.88);
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.46);
  backdrop-filter: blur(22px);
  z-index: 9999;
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }

  .ta-spotlight {
    display: none;
  }
}

@media (max-width: 760px) {
  .ta-card:hover {
    transform: none;
  }

  .ta-spotlight {
    display: none;
  }
}
CSS

echo ""
echo "⚙️ Creating premium JS initializer..."
cat > web/static/premium-static-ui.js <<'JS'
/* ============================================
   TradingAgents Premium Static UI Initializer
   Plain JS only. No React, no build step.
   ============================================ */

(function () {
  const prefersReducedMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function createSpotlight() {
    if (prefersReducedMotion || window.innerWidth < 760) return;

    let spotlight = document.querySelector(".ta-spotlight");
    if (!spotlight) {
      spotlight = document.createElement("div");
      spotlight.className = "ta-spotlight";
      spotlight.setAttribute("aria-hidden", "true");
      document.body.prepend(spotlight);
    }

    let x = window.innerWidth / 2;
    let y = window.innerHeight / 2;
    let raf = null;

    function update() {
      document.documentElement.style.setProperty("--ta-mouse-x", `${x}px`);
      document.documentElement.style.setProperty("--ta-mouse-y", `${y}px`);
      raf = null;
    }

    window.addEventListener("mousemove", (event) => {
      x = event.clientX;
      y = event.clientY;
      if (!raf) raf = requestAnimationFrame(update);
    }, { passive: true });
  }

  function initIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function initToasts() {
    if (!window.Notyf) return;

    const notyf = new Notyf({
      duration: 2800,
      position: { x: "right", y: "bottom" },
      ripple: false,
      dismissible: true,
      types: [
        {
          type: "success",
          background: "linear-gradient(135deg, #16a34a, #22c55e)",
          icon: false
        },
        {
          type: "error",
          background: "linear-gradient(135deg, #dc2626, #ef4444)",
          icon: false
        },
        {
          type: "info",
          background: "linear-gradient(135deg, #0284c7, #38bdf8)",
          icon: false
        }
      ]
    });

    window.TAToast = {
      success: (message) => notyf.success(message),
      error: (message) => notyf.error(message),
      info: (message) => notyf.open({ type: "info", message })
    };
  }

  function initTooltips() {
    if (!window.tippy) return;

    window.tippy("[data-tippy-content], [data-tooltip]", {
      theme: "light-border",
      animation: "shift-away-subtle",
      delay: [120, 40],
      duration: [160, 120],
      maxWidth: 280,
      touch: ["hold", 500]
    });
  }

  function initLenis() {
    if (prefersReducedMotion || !window.Lenis) return;

    // Dashboards often have nested scroll areas. Keep Lenis conservative.
    try {
      const lenis = new Lenis({
        autoRaf: true,
        smoothWheel: true,
        syncTouch: false,
        lerp: 0.12
      });
      window.TALenis = lenis;
    } catch (error) {
      console.warn("[TradingAgents UI] Lenis failed to initialize:", error);
    }
  }

  function initGSAP() {
    if (prefersReducedMotion || !window.gsap) return;

    try {
      if (window.ScrollTrigger) {
        window.gsap.registerPlugin(window.ScrollTrigger);
      }

      window.gsap.from(".ta-card, .card, [data-animate='card']", {
        opacity: 0,
        y: 14,
        duration: 0.42,
        stagger: 0.045,
        ease: "power2.out",
        clearProps: "transform"
      });

      window.gsap.from("[data-animate='fade-up']", {
        opacity: 0,
        y: 18,
        duration: 0.46,
        stagger: 0.05,
        ease: "power2.out"
      });
    } catch (error) {
      console.warn("[TradingAgents UI] GSAP animation failed:", error);
    }
  }

  function initChartPlugins() {
    if (!window.Chart) return;

    try {
      const plugins = [];

      if (window.ChartDataLabels) plugins.push(window.ChartDataLabels);
      if (window.ChartAnnotation) plugins.push(window.ChartAnnotation);
      if (window.ChartZoom) plugins.push(window.ChartZoom);

      if (plugins.length) {
        window.Chart.register(...plugins);
      }

      // Global Chart.js visual polish. Existing chart options can override this.
      window.Chart.defaults.color = getComputedStyle(document.documentElement)
        .getPropertyValue("--ta-muted")
        .trim() || "#94a3b8";

      window.Chart.defaults.borderColor = "rgba(148, 163, 184, 0.13)";
      window.Chart.defaults.font.family =
        'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

      if (window.Chart.defaults.plugins && window.Chart.defaults.plugins.legend) {
        window.Chart.defaults.plugins.legend.labels.usePointStyle = true;
        window.Chart.defaults.plugins.legend.labels.boxWidth = 8;
        window.Chart.defaults.plugins.legend.labels.boxHeight = 8;
      }
    } catch (error) {
      console.warn("[TradingAgents UI] Chart plugin setup failed:", error);
    }
  }

  function initSweetAlertDefaults() {
    if (!window.Swal) return;

    window.TAConfirm = function TAConfirm(options) {
      return window.Swal.fire({
        background: "rgba(2, 6, 23, 0.96)",
        color: "#e5edf8",
        confirmButtonColor: "#0284c7",
        cancelButtonColor: "#334155",
        showCancelButton: true,
        reverseButtons: true,
        ...options
      });
    };
  }

  function initAxeDevHelper() {
    // Only runs if you manually load axe.min.js and set ?axe=1
    if (!window.axe || !window.location.search.includes("axe=1")) return;

    window.axe.run().then((results) => {
      if (results.violations.length) {
        console.group("[TradingAgents UI] axe accessibility violations");
        results.violations.forEach((violation) => {
          console.warn(violation.id, violation.description, violation.nodes);
        });
        console.groupEnd();
      } else {
        console.info("[TradingAgents UI] axe: no violations detected");
      }
    });
  }

  ready(() => {
    createSpotlight();
    initIcons();
    initToasts();
    initTooltips();
    initLenis();
    initGSAP();
    initChartPlugins();
    initSweetAlertDefaults();
    initAxeDevHelper();

    document.documentElement.classList.add("ta-premium-ui-ready");
  });
})();
JS

if [[ "$INJECT" -eq 1 ]]; then
  echo ""
  echo "🪄 Injecting tags into web/static/index.html..."

  python3 - <<'PY'
from pathlib import Path

index = Path("web/static/index.html")
html = index.read_text()

head_tags = """
<!-- TradingAgents premium static UI vendor CSS -->
<link rel="stylesheet" href="/static/vendor/css/normalize.min.css">
<link rel="stylesheet" href="/static/vendor/css/notyf.min.css">
<link rel="stylesheet" href="/static/vendor/css/tippy.css">
<link rel="stylesheet" href="/static/vendor/css/tabulator.min.css">
<link rel="stylesheet" href="/static/vendor/css/sweetalert2.min.css">
<link rel="stylesheet" href="/static/premium-static-ui.css">
"""

body_tags = """
<!-- TradingAgents premium static UI vendor JS -->
<script src="/static/vendor/js/gsap.min.js"></script>
<script src="/static/vendor/js/ScrollTrigger.min.js"></script>
<script src="/static/vendor/js/lenis.min.js"></script>
<script src="/static/vendor/js/notyf.min.js"></script>
<script src="/static/vendor/js/tippy-bundle.umd.min.js"></script>
<script src="/static/vendor/js/sweetalert2.all.min.js"></script>
<script src="/static/vendor/js/fuse.min.js"></script>
<script src="/static/vendor/js/hammer.min.js"></script>
<script src="/static/vendor/js/chartjs-plugin-datalabels.min.js"></script>
<script src="/static/vendor/js/chartjs-plugin-annotation.min.js"></script>
<script src="/static/vendor/js/chartjs-plugin-zoom.min.js"></script>
<script src="/static/vendor/js/tabulator.min.js"></script>
<script src="/static/vendor/js/echarts.min.js"></script>
<script src="/static/vendor/js/lucide.min.js"></script>
<script src="/static/premium-static-ui.js"></script>
"""

marker_css = "/static/premium-static-ui.css"
marker_js = "/static/premium-static-ui.js"

if marker_css not in html:
    if "</head>" in html:
        html = html.replace("</head>", head_tags + "\n</head>", 1)
    else:
        html = head_tags + "\n" + html

if marker_js not in html:
    if "</body>" in html:
        html = html.replace("</body>", body_tags + "\n</body>", 1)
    else:
        html = html + "\n" + body_tags

index.write_text(html)
print("✅ Injected premium UI CSS/JS tags into web/static/index.html")
PY
fi

echo ""
echo "✅ Static UI/UX tools installed."
echo ""
echo "Installed vendor files:"
echo "  web/static/vendor/css/"
echo "  web/static/vendor/js/"
echo "  web/static/vendor/dev/"
echo ""
echo "Created:"
echo "  web/static/premium-static-ui.css"
echo "  web/static/premium-static-ui.js"
echo ""
if [[ "$INJECT" -ne 1 ]]; then
  cat <<'TAGS'
Next step: add these tags to web/static/index.html.

Inside <head>:
  <link rel="stylesheet" href="/static/vendor/css/normalize.min.css">
  <link rel="stylesheet" href="/static/vendor/css/notyf.min.css">
  <link rel="stylesheet" href="/static/vendor/css/tippy.css">
  <link rel="stylesheet" href="/static/vendor/css/tabulator.min.css">
  <link rel="stylesheet" href="/static/vendor/css/sweetalert2.min.css">
  <link rel="stylesheet" href="/static/premium-static-ui.css">

Before </body>, after Chart.js if Chart.js is already loaded:
  <script src="/static/vendor/js/gsap.min.js"></script>
  <script src="/static/vendor/js/ScrollTrigger.min.js"></script>
  <script src="/static/vendor/js/lenis.min.js"></script>
  <script src="/static/vendor/js/notyf.min.js"></script>
  <script src="/static/vendor/js/tippy-bundle.umd.min.js"></script>
  <script src="/static/vendor/js/sweetalert2.all.min.js"></script>
  <script src="/static/vendor/js/fuse.min.js"></script>
  <script src="/static/vendor/js/hammer.min.js"></script>
  <script src="/static/vendor/js/chartjs-plugin-datalabels.min.js"></script>
  <script src="/static/vendor/js/chartjs-plugin-annotation.min.js"></script>
  <script src="/static/vendor/js/chartjs-plugin-zoom.min.js"></script>
  <script src="/static/vendor/js/tabulator.min.js"></script>
  <script src="/static/vendor/js/echarts.min.js"></script>
  <script src="/static/vendor/js/lucide.min.js"></script>
  <script src="/static/premium-static-ui.js"></script>

Or rerun:
  ./install-tradingagents-static-ui-tools.sh --inject
TAGS
fi

echo ""
echo "Run your app:"
echo "  python run_web.py"
echo ""
echo "Test in browser:"
echo "  http://localhost:8001"
echo ""
echo "Optional dev-only a11y audit:"
echo "  Add <script src=\"/static/vendor/dev/axe.min.js\"></script> temporarily, then visit ?axe=1"
