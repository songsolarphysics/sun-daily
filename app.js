/* sun-daily gallery: reads data/cards.json and shows one clip at a time. No dependencies. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  // Channel colours follow the house convention for EUV channels (a swatch carries identity,
  // text stays black or grey).
  const CHANNEL = {
    94: "#008300", 131: "#1baf7a", 171: "#eda100", 174: "#eda100", 193: "#52514e", 195: "#52514e",
    211: "#4a3aa7", 304: "#e34948", 335: "#2a78d6", 1600: "#e87ba4", 1700: "#e87ba4",
  };
  const SPEEDS = [1, 0.5, 2];
  const params = new URLSearchParams(location.search);
  const kiosk = params.has("kiosk");
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const video = $("clip");
  let cards = [];
  let index = 0;
  let speed = 0;
  let plays = 0;

  // ---------------------------------------------------------------- formatting
  const day = (iso, opts) =>
    new Date(iso.slice(0, 10) + "T12:00:00Z").toLocaleDateString("en-GB", Object.assign(
      { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }, opts));
  const hm = (iso) => iso.slice(11, 16);

  function observed(o) {
    const sameDay = o.t_start.slice(0, 10) === o.t_end.slice(0, 10);
    return sameDay
      ? `${day(o.t_start)}, ${hm(o.t_start)}–${hm(o.t_end)} UT`
      : `${day(o.t_start)} ${hm(o.t_start)} to ${day(o.t_end)} ${hm(o.t_end)} UT`;
  }

  function swatch(label) {
    const m = /(\d{2,4})\s*Å/.exec(label || "");
    const s = document.createElement("span");
    s.className = "swatch";
    s.style.background = (m && CHANNEL[m[1]]) || "#b9b7b1";
    s.setAttribute("aria-hidden", "true");
    return s;
  }

  // "HRI_EUV 174 Å" -> HRI<sub>EUV</sub> 174 Å, built as nodes (no innerHTML)
  function labelNodes(label) {
    const frag = document.createDocumentFragment();
    const m = /^(.*?)_([A-Za-z0-9]+)(.*)$/.exec(label || "");
    if (!m) {
      frag.append(label || "");
      return frag;
    }
    const sub = document.createElement("sub");
    sub.textContent = m[2];
    frag.append(m[1], sub, m[3]);
    return frag;
  }

  function fact(dl, name, ...value) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = name;
    dd.append(...value);
    dl.append(dt, dd);
  }

  function sentence(text) {
    const t = (text || "").replace(/>=/g, "≥").trim();
    return t ? t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : "";
  }

  // ---------------------------------------------------------------- one entry
  function show(i, push) {
    index = Math.max(0, Math.min(cards.length - 1, i));
    const card = cards[index];
    const o = card.obs;

    document.title = `${card.title} | sun-daily`;
    $("date").textContent = day(card.date, { weekday: "long" });
    $("title").textContent = card.title;
    $("caption").textContent = card.caption;

    const dl = $("facts");
    dl.replaceChildren();
    fact(dl, "Instrument", swatch(card.label), labelNodes(card.label), ` on ${o.observer}`);
    fact(dl, "Observed", observed(o));
    const [w, h] = o.fov_arcsec;
    const square = Math.abs(w - h) < 0.02 * w;
    fact(dl, "Field of view", square
      ? `${Math.round(w)}″ × ${Math.round(h)}″, ${Math.round(o.fov_mm[0])} Mm across`
      : `${Math.round(w)}″ × ${Math.round(h)}″, ${Math.round(o.fov_mm[0])} × ${Math.round(o.fov_mm[1])} Mm`);
    fact(dl, "Sampling", `one frame every ${Math.round(o.cadence_s)} s, ${o.frames} frames`);
    fact(dl, "Playback", `${card.clip.speedup} times real speed`);
    if (o.observer !== "SDO") fact(dl, "Distance", `${o.dsun_au.toFixed(2)} au from the Sun`);
    if (card.reason) fact(dl, "Picked", sentence(card.reason));

    const ads = $("ads");
    ads.hidden = !(card.links && card.links.ads);
    if (!ads.hidden) ads.href = card.links.ads;
    const run = $("runlog");
    run.hidden = !(card.pipeline && card.pipeline.run_url);
    if (!run.hidden) run.href = card.pipeline.run_url;

    const lines = ["pip install git+https://github.com/songsolarphysics/sun-daily"];
    if (card.source === "aia_closeup") lines.push("export JSOC_EMAIL=you@example.org   # an address registered with JSOC");
    lines.push(card.recipe.command);
    $("recipe").textContent = lines.join("\n");

    video.style.aspectRatio = `${card.clip.width} / ${card.clip.height}`;
    video.poster = card.clip.poster;
    if (card.clip.archived) {
      video.removeAttribute("src");
      video.load();
    } else {
      video.src = card.clip.file;
      video.playbackRate = SPEEDS[speed];
      if (!reduceMotion || kiosk) video.play().catch(() => {});
    }
    $("play").disabled = !!card.clip.archived;
    $("play").textContent = (!reduceMotion || kiosk) ? "Pause" : "Play";
    $("prev").disabled = index >= cards.length - 1;
    $("next").disabled = index <= 0;

    for (const a of document.querySelectorAll("#sheet a")) {
      a.setAttribute("aria-current", a.dataset.date === card.date ? "true" : "false");
    }
    if (kiosk) {
      const cap = $("kiosk-caption");
      cap.hidden = false;
      const sub = document.createElement("span");
      sub.textContent = `${day(card.date)}`;
      cap.replaceChildren(card.title, sub);
    }
    if (push) history.pushState({ d: card.date }, "", index === 0 && !kiosk ? "./" : `?d=${card.date}${kiosk ? "&kiosk" : ""}`);
  }

  // ---------------------------------------------------------------- contact sheet
  function sheet() {
    const ol = $("sheet");
    ol.replaceChildren();
    cards.forEach((card, i) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = `?d=${card.date}`;
      a.dataset.date = card.date;
      const img = document.createElement("img");
      img.src = card.clip.poster;
      img.alt = "";
      img.loading = "lazy";
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = day(card.date, { month: "short" });
      const what = document.createElement("span");
      what.className = "what";
      what.append(swatch(card.label), card.title);
      a.append(img, when, what);
      a.addEventListener("click", (e) => {
        e.preventDefault();
        show(i, true);
        window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
      });
      li.append(a);
      ol.append(li);
    });
    $("count").textContent = cards.length ? `${cards.length}` : "";
  }

  // ---------------------------------------------------------------- controls
  $("play").addEventListener("click", () => {
    if (video.paused) video.play(); else video.pause();
  });
  video.addEventListener("play", () => { $("play").textContent = "Pause"; });
  video.addEventListener("pause", () => { $("play").textContent = "Play"; });
  video.addEventListener("click", () => $("play").click());
  $("speed").addEventListener("click", () => {
    speed = (speed + 1) % SPEEDS.length;
    video.playbackRate = SPEEDS[speed];
    $("speed").textContent = `Speed ${SPEEDS[speed]}×`;
  });
  $("full").addEventListener("click", () => {
    if (video.requestFullscreen) video.requestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  });
  $("prev").addEventListener("click", () => show(index + 1, true));
  $("next").addEventListener("click", () => show(index - 1, true));
  $("data-toggle").addEventListener("click", () => {
    const panel = $("data-panel");
    panel.hidden = !panel.hidden;
    $("data-toggle").setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("recipe").textContent);
      $("copy").textContent = "Copied";
    } catch (e) {
      $("copy").textContent = "Select the text to copy";
    }
    setTimeout(() => { $("copy").textContent = "Copy commands"; }, 1800);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("button, a, input, textarea")) return;
    if (e.key === "ArrowLeft") show(index + 1, true);
    if (e.key === "ArrowRight") show(index - 1, true);
    if (e.key === " ") { e.preventDefault(); $("play").click(); }
  });
  window.addEventListener("popstate", () => {
    const d = new URLSearchParams(location.search).get("d");
    const i = cards.findIndex((c) => c.date === d);
    show(i < 0 ? 0 : i, false);
  });

  // ---------------------------------------------------------------- start
  async function load() {
    try {
      const r = await fetch("data/cards.json", { cache: "no-cache" });
      return r.ok ? await r.json() : [];
    } catch (e) {
      return [];
    }
  }

  async function start() {
    cards = await load();
    if (!cards.length) {
      $("plate").hidden = true;
      $("title").textContent = "No clips yet";
      $("caption").textContent = "The first one is rendered tonight.";
      $("data-toggle").hidden = true;
      return;
    }
    if (kiosk) {
      document.body.classList.add("kiosk");
      video.loop = false;
      const recent = Math.min(cards.length, Number(params.get("kiosk")) || 7);
      video.addEventListener("ended", () => {
        plays += 1;
        if (plays >= 3) { plays = 0; show((index + 1) % recent, false); } else video.play();
      });
      setInterval(async () => {                       // pick up tonight's clip without a reload
        const fresh = await load();
        if (fresh.length && fresh[0].date !== cards[0].date) { cards = fresh; plays = 0; show(0, false); }
      }, 30 * 60 * 1000);
    }
    sheet();
    const want = params.get("d");
    const i = cards.findIndex((c) => c.date === want);
    show(i < 0 ? 0 : i, false);
  }

  start();
})();
