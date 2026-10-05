// CamAI demo-page config. Edit `dashboardUrl` to point at YOUR deployed dashboard,
// then commit — GitHub Pages serves this file next to index.html.
//
// The "Launch 3-hour live demo" button links to `${dashboardUrl}/demo`, which
// mints a session and opens the dashboard pinned to it. A same-origin flow on the
// dashboard side means there's nothing else to configure (no API base, no CORS).
//
// You can also override per-visit with ?app=<dashboard-url> on the page URL.
window.CAMAI_CONFIG = {
  // e.g. "https://camai-dashboard.onrender.com" or "https://app.yourdomain.com"
  dashboardUrl: "http://localhost:3000",
};
