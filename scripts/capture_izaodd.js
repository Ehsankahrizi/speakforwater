// Capture the ADD_SOURCE (izAoDd) request payload from the Gemini Notebook web UI.
//
// Why: notebooklm-py 0.8.0 sends a static payload its own source notes was only
// "verified live against an un-migrated account". Our CI now gets rpc_code=9
// (FAILED_PRECONDITION) in ~0.7s on every URL. This grabs the shape the real,
// migrated web UI sends, so the fix is a transcription rather than a guess.
//
// HOW TO USE
//   1. Open https://notebook.google.com and open (or create) any notebook.
//   2. DevTools (Cmd+Opt+I) > Console. Paste this whole file, hit Enter.
//   3. In the UI: Add source > Website (or Link), paste any public URL
//      (https://en.wikipedia.org/wiki/Water works), confirm.
//   4. The console prints PROBE PAYLOAD — a JSON array with the URL and
//      notebook id already swapped for __URL__ / __NOTEBOOK_ID__ tokens.
//   5. Save it to captured.json, then:
//        python scripts/notebooklm_wire_probe.py --payload-file captured.json
//
// This only reads bodies of requests the page itself sends, for one rpcid, and
// prints them locally. It sends nothing anywhere. Reload the tab to remove it.

(() => {
  const RPCID = "izAoDd"; // ADD_SOURCE

  const show = (body) => {
    try {
      const freq = new URLSearchParams(body).get("f.req");
      if (!freq || !freq.includes(RPCID)) return;

      // f.req is [[[rpcid, "<inner JSON as a string>", null, "generic"]]]
      const outer = JSON.parse(freq);
      for (const call of outer[0]) {
        if (call[0] !== RPCID) continue;
        const inner = JSON.parse(call[1]);

        // Tokenise so the probe can substitute its own values.
        let s = JSON.stringify(inner);
        const url = (JSON.stringify(inner).match(/"(https?:\/\/[^"]+)"/) || [])[1];
        if (url) s = s.split(JSON.stringify(url)).join('"__URL__"');
        if (typeof inner[1] === "string") {
          s = s.split(JSON.stringify(inner[1])).join('"__NOTEBOOK_ID__"');
        }

        console.log("%c=== PROBE PAYLOAD (save as captured.json) ===", "color:#0a0;font-weight:bold");
        console.log(s);
        console.log("%c=== raw, for reference ===", "color:#888");
        console.log(JSON.stringify(inner, null, 1));
      }
    } catch (e) {
      console.warn("izAoDd capture: could not parse a request", e);
    }
  };

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    try {
      const [input, init] = args;
      const u = typeof input === "string" ? input : input && input.url;
      if (u && u.includes("batchexecute") && init && typeof init.body === "string") {
        show(init.body);
      }
    } catch (_) {}
    return origFetch.apply(this, args);
  };

  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    try {
      if (typeof body === "string" && body.includes("f.req")) show(body);
    } catch (_) {}
    return origSend.apply(this, arguments);
  };

  console.log(
    "%cizAoDd capture armed. Now add a website source in the UI.",
    "color:#06c;font-weight:bold"
  );
})();
