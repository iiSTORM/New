/* Save the PrizePicks board that this tab is already showing.
 *
 * It makes no request. An earlier version fetched the endpoint from the
 * app's own page and was answered 403: a fetch() to api.prizepicks.com
 * from app.prizepicks.com is cross-origin, so it carries an Origin header
 * and goes through CORS, which is a different thing from opening the URL
 * and gets treated differently. Navigating to it works, which is the
 * route the README has always documented.
 *
 * So this does not re-ask for anything. You open the endpoint yourself,
 * the browser loads it as it always has, and this saves the document that
 * is on screen under the name the refresh script looks for. It is the
 * Save As dialog with the typing removed, and there is no request for
 * anything to refuse.
 */
(() => {
  const OUT_NAME = "prizepicks-payload.json";
  const ENDPOINT = "https://api.prizepicks.com/projections?per_page=250&single_stat=true";

  const say = (msg, bad) => {
    const el = document.createElement("div");
    el.textContent = msg;
    el.style.cssText = [
      "position:fixed", "z-index:2147483647", "left:50%", "top:24px",
      "transform:translateX(-50%)", "padding:12px 18px", "border-radius:10px",
      "font:600 14px/1.4 system-ui,sans-serif", "max-width:80vw",
      "box-shadow:0 6px 24px rgba(0,0,0,.35)", "white-space:pre-wrap",
      bad ? "background:#b3261e;color:#fff" : "background:#123f2b;color:#c9f7dd",
    ].join(";");
    document.body.appendChild(el);
    setTimeout(() => el.remove(), bad ? 14000 : 5000);
  };

  /* The raw document text, however the browser chose to present it.
   *
   * Chromium puts a JSON document in a <pre>. Firefox renders its own
   * viewer instead, whose innerText is the pretty-printed tree rather
   * than the source -- that is what the "Raw Data" tab is for, and the
   * message below says so rather than saving the tree and letting
   * scrape_props.py fail on it later. */
  const readDocument = () => {
    const pre = document.querySelector("pre");
    if (pre && pre.innerText.trim()) return pre.innerText.trim();
    const body = document.body ? document.body.innerText : "";
    return (body || "").trim();
  };

  const text = readDocument();
  if (!text) {
    say(`Nothing to save on this page.\n\nOpen this first, then click again:\n${ENDPOINT}`, true);
    return;
  }

  let payload;
  try {
    payload = JSON.parse(text);
  } catch (e) {
    say("This page is not raw JSON.\n\n"
      + "In Chrome or Edge, open the endpoint URL directly.\n"
      + "In Firefox, open it and switch to the \"Raw Data\" tab first.", true);
    return;
  }

  /* Shaped like the board, not merely like JSON. Saving the wrong page's
   * JSON under this name would send scrape_props.py off to report zero
   * matched props, which reads like a matching bug rather than the wrong
   * file. */
  const projections = ((payload && payload.data) || [])
    .filter((x) => x && x.type === "projection").length;
  if (!Array.isArray(payload && payload.data)) {
    say("That is JSON, but not a PrizePicks board — no \"data\" array.\n\n"
      + `Open this and try again:\n${ENDPOINT}`, true);
    return;
  }

  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  a.download = OUT_NAME;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 30000);

  say(projections
    ? `Saved ${OUT_NAME} — ${projections} projections.`
    : `Saved ${OUT_NAME}, but it holds no projections. That is a real state `
      + `between slates; if you expected lines, reload the endpoint and try again.`);
})();
