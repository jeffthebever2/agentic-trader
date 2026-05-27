/* ============================================
   TradingAgents UI Initializer
   Plain JS only. No React, no build step.
   ============================================ */

(function () {
  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
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
          background: "#047857",
          icon: false
        },
        {
          type: "error",
          background: "#b91c1c",
          icon: false
        },
        {
          type: "info",
          background: "#1d4ed8",
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
      duration: [140, 100],
      maxWidth: 280,
      touch: ["hold", 500]
    });
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

      // Chart defaults — structural only, never semantic dataset colors
      window.Chart.defaults.color = getComputedStyle(document.documentElement)
        .getPropertyValue("--ink-faint")
        .trim() || "#736C61";

      window.Chart.defaults.borderColor = "rgba(113, 108, 97, 0.13)";
      window.Chart.defaults.font.family =
        '"Geist", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

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
        background: "var(--surface, #FCFBFA)",
        color: "var(--ink, #1A1714)",
        confirmButtonColor: "var(--accent, #D63A00)",
        cancelButtonColor: "var(--surface-raised, #EBE9E4)",
        showCancelButton: true,
        reverseButtons: true,
        ...options
      });
    };
  }

  function initAxeDevHelper() {
    // Only runs if axe.min.js is loaded and ?axe=1 is in the URL
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
    initIcons();
    initToasts();
    initTooltips();
    initChartPlugins();
    initSweetAlertDefaults();
    initAxeDevHelper();

    document.documentElement.classList.add("ta-ui-ready");
  });
})();
