// Shared client-side logic for the Lloyds Demo Web Application.
// The backend API base URL can be overridden with ?api=http://host:port,
// otherwise it defaults to localhost:8000 (the FastAPI backend's default port).
(function () {
  const params = new URLSearchParams(window.location.search);
  window.LLOYDS_API_BASE =
    params.get("api") || window.LLOYDS_API_BASE || "http://localhost:8000";

  window.lloydsFetch = async function (path, options) {
    const res = await fetch(`${window.LLOYDS_API_BASE}${path}`, options);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const error = new Error(body.detail || body.error || `Request failed (${res.status})`);
      error.status = res.status;
      throw error;
    }
    return res.json();
  };

  window.formatGBP = function (value) {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency: "GBP",
    }).format(value);
  };

  window.formatDate = function (iso) {
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  };

  window.setActiveNav = function () {
    const path = window.location.pathname.replace(/\/$/, "") || "/";
    document.querySelectorAll(".navbar nav a").forEach((a) => {
      const target = a.getAttribute("data-path");
      if (target === path) a.classList.add("active");
    });
  };

  document.addEventListener("DOMContentLoaded", window.setActiveNav);
})();
