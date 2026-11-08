// Add-in settings. Edit and reload the task pane.
window.MAIL_AGENT_CONFIG = {
  // Where `python -m mail_agent serve` runs (or your hosted API).
  apiBase: "https://localhost:3000",

  // Leave both empty for local dev (API_AUTH_MODE=dev).
  // For API_AUTH_MODE=entra: the Entra app's client id and the scope it exposes,
  // e.g. "api://localhost:3000/<client-id>/access_as_user". Uses nested app authentication (NAA).
  clientId: "",
  apiScope: "",
};
