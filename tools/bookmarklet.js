/* Save the PrizePicks board from the session you are already in.
 *
 * scrape_props.py cannot ask for this endpoint: the provider refuses that
 * client, and no network change helps. The documented answer has been to
 * open the endpoint in a browser, save the JSON, and pipe it in. This is
 * that, minus the save dialog -- you click it, in your own browser, while
 * logged in as yourself, and the file lands in Downloads.
 *
 * It does nothing to disguise itself and nothing the page could not do:
 * it is the same request the site makes, from the same session, run
 * because you asked for it.
 */
(async () => {
  const ENDPOINT = "https://api.prizepicks.com/projections?per_page=250&single_stat=true";

  const say = (msg, bad) => {
    const el = document.createElement("div");
    el.textContent = msg;
    el.style.cssText = [
      "position:fixed", "z-index:2147483647", "left:50%", "top:24px",
      "transform:translateX(-50%)", "padding:12px 18px", "border-radius:10px",
      "font:600 14px/1.4 system-ui,sans-serif", "max-width:80vw",
      "box-shadow:0 6px 24px rgba(0,0,0,.35)",
      bad ? "background:#b3261e;color:#fff" : "background:#123f2b;color:#c9f7dd",
    ].join(";");
    document.body.appendChild(el);
    setTimeout(() => el.remove(), bad ? 12000 : 5000);
  };

  try {
    const res = await fetch(ENDPOINT, { credentials: "include" });
    if (!res.ok) {
      say(`PrizePicks answered HTTP ${res.status}. If that is 401 or 403, `
        + `reload the site, make sure you are logged in, and click again.`, true);
      return;
    }
    const text = await res.text();

    let count = 0;
    try {
      const data = JSON.parse(text);
      count = (data.data || []).filter((x) => x && x.type === "projection").length;
    } catch (e) {
      // Saved anyway: a payload this cannot parse is exactly the thing
      // worth having on disk to look at, and scrape_props.py will say
      // more about it than a banner can.
      say("That did not parse as JSON — saving it anyway so you can look at it.", true);
    }

    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    a.download = "prizepicks-payload.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);

    if (count) say(`Saved prizepicks-payload.json — ${count} projections.`);
  } catch (err) {
    say(`Could not read the board: ${err && err.message}. Open the endpoint in a `
      + `tab and use Save As, then pass that file to scrape_props.py.`, true);
  }
})();
