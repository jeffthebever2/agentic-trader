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
