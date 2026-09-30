(function () {
  const C = window.CATALOG;
  const wall = document.getElementById("wall");
  const listEl = document.getElementById("list");
  const stat = document.getElementById("stat");
  const meter = document.getElementById("meter");
  const about = document.getElementById("about");
  const countEl = document.getElementById("count");
  const note = document.getElementById("focusnote");
  if (!C || !C.images) {
    wall.innerHTML = '<p class="empty">No catalog. Run python build_docs.py</p>';
    return;
  }

  const works = C.works;
  const byWork = Object.fromEntries(works.map((w) => [w.id, w]));
  let sortKey = "year";
  let sortDir = 1;
  let mode = "works";
  let listFilter = "all";
  let listSort = "year";
  let query = "";
  let listQuery = "";
  let openId = null;
  let selectedWork = null;

  about.textContent = C.note;
  const indexWorks = works.filter((w) => !w.extra);
  const haveN = indexWorks.filter((w) => w.status === "have").length;
  const extraN = works.length - indexWorks.length;
  const unknownN = C.unidentified || 0;
  stat.textContent = haveN + " of " + indexWorks.length + " index works held · " + extraN + " more only in the folders" +
    (unknownN ? " · " + unknownN + " files not yet identified" : "");
  meter.style.width = (indexWorks.length ? (100 * haveN) / indexWorks.length : 0) + "%";

  function yearLabel(im) {
    if (!im.year) return "";
    return String(im.year);
  }

  function hay(im) {
    const w = im.work_id ? byWork[im.work_id] : null;
    return [im.title, im.year, im.source, im.collection, im.medium, w && w.title, w && w.collection]
      .filter(Boolean).join(" ").toLowerCase();
  }

  function visibleImages() {
    const q = query.trim().toLowerCase();
    let rows = C.images.filter((im) => (mode === "files" ? true : im.primary));
    if (q) rows = rows.filter((im) => hay(im).includes(q));
    const dir = sortDir;
    const val = (im) => {
      if (sortKey === "title") return (im.title || "\uffff").toLowerCase();
      if (sortKey === "source") return im.source || "";
      if (sortKey === "size") return im.w * im.h;
      if (sortKey === "index") {
        const w = im.work_id ? byWork[im.work_id] : null;
        return w ? w.year || 9999 : 10000;
      }
      return im.year || 9999;
    };
    rows.sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return (a.title || "").localeCompare(b.title || "");
    });
    return rows;
  }

  let hoverId = null;
  let mosaicKey = "";
  let view = "rows";
  let clusterOn = null;
  let mapCache = null;
  const GAP = 2;

  function indexSpace(S) {
    if (S && !S._by) S._by = Object.fromEntries((S.points || []).map((p) => [p.id, p]));
    return S;
  }
  indexSpace(window.VISUAL);
  indexSpace(window.STYLE);

  function space() {
    if (view === "style") return window.STYLE || null;
    if (view === "map") return window.VISUAL || null;
    return null;
  }
  function pointOf(id) {
    const S = space();
    return S && S._by ? S._by[id] : null;
  }
  function isMapView() {
    return view === "map" || view === "style";
  }

  // Strip treemap: each row is one strip, scaled so it meets both edges.
  // Tile width / height stays equal to the painting's aspect ratio, so nothing
  // is stretched and the only seam is the 2px gutter.
  function strips(items, width, target) {
    const rows = [];
    let row = [];
    let sum = 0;
    items.forEach((im) => {
      const a = im.aspect || 1;
      if (row.length) {
        const h = (width - GAP * row.length) / (sum + a);
        if (h < target) {
          rows.push(row);
          row = [im];
          sum = a;
          return;
        }
      }
      row.push(im);
      sum += a;
    });
    if (row.length) rows.push(row);
    return rows;
  }

  // Resting row height comes from the aspects. On hover or click the focused
  // painting keeps its aspect ratio and the whole row grows to that new height;
  // the others give up width so the row still meets both edges.
  function layoutRow(row, width) {
    const aspects = row.map((im) => im.aspect || 1);
    const sum = aspects.reduce((s, a) => s + a, 0);
    const inner = width - GAP * (row.length - 1);
    const h0 = inner / sum;
    const focus = row.findIndex((im) => im.id === openId || im.id === hoverId);
    const boost = focus < 0 || row.length < 2 ? 1 : (row[focus].id === openId ? 2.35 : 1.72);
    let h = h0 * boost;
    const widths = aspects.map((a) => h * a);
    if (focus >= 0 && row.length > 1) {
      const minOther = 36;
      const maxFocus = inner - (row.length - 1) * minOther;
      if (widths[focus] > maxFocus) {
        widths[focus] = maxFocus;
        h = maxFocus / aspects[focus];
      } else {
        widths[focus] = h * aspects[focus];
      }
      const rest = inner - widths[focus];
      const otherSum = sum - aspects[focus];
      aspects.forEach((a, i) => {
        if (i !== focus) widths[i] = rest * (a / otherSum);
      });
    }
    return { h, widths };
  }

  function caption(im) {
    const bits = [yearLabel(im), im.collection || im.source].filter(Boolean);
    if (im.copies > 1 && mode === "works") bits.push(im.copies + " files");
    if (view === "style") {
      const p = pointOf(im.id);
      const S = space();
      if (p && S && p.probs) {
        const c = S.clusters.find((x) => x.id === p.cluster);
        if (c) bits.push(c.name + " " + Math.round(p.probs[p.cluster] * 100) + "%");
      }
    }
    const link = im.page ? ' · <a href="' + im.page + '" target="_blank" rel="noopener">source</a>' : "";
    return "<b>" + esc(im.title || "Untitled") + "</b><span>" + esc(bits.join(" · ")) + link + "</span>";
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function layout(items) {
    if (isMapView()) placeMap(items);
    else place(items);
  }

  function renderWall() {
    const items = visibleImages();
    countEl.textContent = items.length + (mode === "works" ? " works" : " files");
    if (isMapView()) {
      renderMap(items);
      return;
    }
    const key = items.map((im) => im.id).join("|");
    if (!items.length) {
      wall.innerHTML = '<p class="empty">Nothing matches.</p>';
      mosaicKey = "";
      return;
    }
    if (key !== mosaicKey) {
      const canvas = document.createElement("div");
      canvas.className = "mosaic";
      items.forEach((im) => {
        const fig = document.createElement("figure");
        fig.className = "tile";
        fig.dataset.id = im.id;
        fig.dataset.work = im.work_id || "";
        fig.tabIndex = 0;
        const img = document.createElement("img");
        img.src = im.src;
        img.alt = im.title || "Painting";
        img.loading = "lazy";
        img.decoding = "async";
        const cap = document.createElement("figcaption");
        cap.innerHTML = caption(im);
        fig.append(img, cap);
        fig.addEventListener("mouseenter", () => {
          if (hoverId === im.id) return;
          hoverId = im.id;
          layout(items);
        });
        fig.addEventListener("click", (e) => {
          if (e.target.closest("a")) return;
          toggle(im.id, items);
        });
        fig.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(im.id, items); }
        });
        canvas.append(fig);
      });
      canvas.addEventListener("mouseleave", () => {
        if (!hoverId) return;
        hoverId = null;
        layout(items);
      });
      wall.replaceChildren(canvas);
      mosaicKey = key;
    }
    place(items);
  }

  function place(items) {
    const canvas = wall.querySelector(".mosaic");
    if (!canvas) return;
    const list = items || visibleImages();
    const width = Math.max(120, wall.clientWidth);
    const target = Number(document.getElementById("scale").value) || 180;
    const rows = strips(list, width, target);
    const nodes = new Map([...canvas.children].map((n) => [n.dataset.id, n]));
    let y = 0;
    rows.forEach((row) => {
      const laid = layoutRow(row, width);
      const h = laid.h;
      let x = 0;
      row.forEach((im, i) => {
        const node = nodes.get(im.id);
        const w = i === row.length - 1 ? width - x : Math.round(laid.widths[i]);
        if (node) {
          node.classList.toggle("open", im.id === openId);
          node.style.width = Math.max(1, w) + "px";
          node.style.height = Math.max(1, h) + "px";
          node.style.transform = "translate(" + x + "px," + y + "px)";
        }
        x += w + GAP;
      });
      y += h + GAP;
    });
    canvas.style.height = Math.max(0, y - GAP) + "px";
  }

  function mapImages(items) {
    const base = items || visibleImages();
    return base.filter((im) => pointOf(im.id) && (clusterOn == null || pointOf(im.id).cluster === clusterOn));
  }

  function separate(nodes, focusId) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = b.cx - a.cx;
        const dy = b.cy - a.cy;
        const ox = (a.w + b.w) / 2 + GAP - Math.abs(dx);
        const oy = (a.h + b.h) / 2 + GAP - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        let sa = 0.5, sb = 0.5;
        if (focusId && a.id === focusId) { sa = 0.08; sb = 0.92; }
        if (focusId && b.id === focusId) { sa = 0.92; sb = 0.08; }
        if (ox < oy) {
          const s = dx === 0 ? 1 : Math.sign(dx);
          a.cx -= s * ox * sa;
          b.cx += s * ox * sb;
        } else {
          const s = dy === 0 ? 1 : Math.sign(dy);
          a.cy -= s * oy * sa;
          b.cy += s * oy * sb;
        }
      }
    }
  }

  // Three regions sized by how many works fell in each style. Inside a region,
  // u/v is a CLIP similarity layout so related paintings stay near each other.
  function styleAnchor(p, width, boxH) {
    if (clusterOn != null) {
      return {
        cx: 24 + (p.u ?? 0.5) * (width - 48),
        cy: 24 + (p.v ?? 0.5) * (boxH - 48),
      };
    }
    const S = space();
    const counts = {};
    (S.clusters || []).forEach((c) => { counts[c.id] = c.n || 0; });
    const n = (counts[0] || 0) + (counts[1] || 0) + (counts[2] || 0) || 1;
    const scene = (counts[2] || 0) / n;
    const lineW = (counts[0] || 0) / ((counts[0] || 0) + (counts[1] || 0) || 1);
    const gap = 0.012;
    let x0, y0, x1, y1;
    if (p.cluster === 2) {
      x0 = gap; y0 = gap; x1 = 1 - gap; y1 = scene - gap;
    } else if (p.cluster === 0) {
      x0 = gap; y0 = scene + gap; x1 = lineW - gap; y1 = 1 - gap;
    } else {
      x0 = lineW + gap; y0 = scene + gap; x1 = 1 - gap; y1 = 1 - gap;
    }
    const u = p.u ?? 0.5, v = p.v ?? 0.5;
    return {
      cx: 16 + (x0 + u * (x1 - x0)) * (width - 32),
      cy: 16 + (y0 + v * (y1 - y0)) * (boxH - 32),
    };
  }

  function packBubbles(list, width) {
    const target = Number(document.getElementById("scale").value) || 180;
    const area = Math.pow(target * 0.55, 2);
    const boxH = Math.max(wall.clientHeight || 600, (list.length * area * 1.7) / width);
    const nodes = list.map((im) => {
      const aspect = im.aspect || 1;
      const h = Math.sqrt(area / aspect);
      const w = h * aspect;
      const p = pointOf(im.id);
      const anchored = view === "style" && p && p.u != null;
      const spot = anchored ? styleAnchor(p, width, boxH) : null;
      const cx = spot ? spot.cx : 24 + p.x * (width - 48);
      const cy = spot ? spot.cy : 24 + p.y * (boxH - 48);
      return { id: im.id, im, w, h, cx, cy, tx: cx, ty: cy };
    });
    for (let iter = 0; iter < 140; iter++) {
      separate(nodes, null);
      separate(nodes, null);
      const pull = 0.06 * (1 - iter / 160);
      nodes.forEach((n) => {
        n.cx += (n.tx - n.cx) * pull;
        n.cy += (n.ty - n.cy) * pull;
      });
    }
    for (let k = 0; k < 24; k++) separate(nodes, null);
    if (view === "style") {
      const clampX = () => nodes.forEach((n) => {
        n.cx = Math.min(width - n.w / 2 - 4, Math.max(n.w / 2 + 4, n.cx));
      });
      clampX();
      for (let k = 0; k < 48; k++) separate(nodes, null);
      clampX();
    }
    return nodes;
  }

  function applyBubbles(nodes) {
    const canvas = wall.querySelector(".mosaic");
    if (!canvas) return;
    let minX = Infinity, minY = Infinity, maxX = 0, maxY = 0;
    nodes.forEach((n) => {
      minX = Math.min(minX, n.cx - n.w / 2);
      minY = Math.min(minY, n.cy - n.h / 2);
      maxX = Math.max(maxX, n.cx + n.w / 2);
      maxY = Math.max(maxY, n.cy + n.h / 2);
    });
    const dx = 8 - minX, dy = 8 - minY;
    const dom = new Map([...canvas.children].map((el) => [el.dataset.id, el]));
    const focus = openId || hoverId;
    nodes.forEach((n) => {
      const el = dom.get(n.id);
      if (!el) return;
      const x = n.cx - n.w / 2 + dx;
      const y = n.cy - n.h / 2 + dy;
      el.classList.toggle("open", n.id === openId);
      el.style.zIndex = n.id === focus ? "4" : "1";
      el.style.width = Math.max(1, n.w) + "px";
      el.style.height = Math.max(1, n.h) + "px";
      el.style.transform = "translate(" + x + "px," + y + "px)";
    });
    canvas.style.height = (maxY + dy + 8) + "px";
  }

  function placeMap(items) {
    const canvas = wall.querySelector(".mosaic");
    if (!canvas || !space()) return;
    const list = mapImages(items);
    const width = Math.max(280, wall.clientWidth);
    const scale = Number(document.getElementById("scale").value) || 180;
    const key = clusterOn + ":" + scale + ":" + list.map((im) => im.id).join("|");
    if (!mapCache || mapCache.key !== key) mapCache = { key, nodes: packBubbles(list, width) };
    const nodes = mapCache.nodes.map((n) => ({ ...n }));
    const focus = openId || hoverId;
    const hit = focus && nodes.find((n) => n.id === focus);
    if (hit) {
      const boost = openId === focus ? 2.05 : 1.55;
      hit.w *= boost;
      hit.h *= boost;
      for (let k = 0; k < 36; k++) separate(nodes, focus);
    }
    applyBubbles(nodes);
  }

  function renderLegend() {
    const el = document.getElementById("legend");
    if (!el) return;
    const S = space();
    if (!isMapView() || !S) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.replaceChildren();
    const add = (label, id, color) => {
      const b = document.createElement("button");
      b.type = "button";
      if (color) b.innerHTML = '<i class="swatch" style="background:' + color + '"></i>' + esc(label);
      else b.textContent = label;
      b.className = clusterOn === id ? "on" : "";
      b.addEventListener("click", () => {
        clusterOn = id;
        mapCache = null;
        mosaicKey = "";
        renderLegend();
        renderWall();
      });
      el.append(b);
    };
    add(view === "style" ? "All styles" : "All colors", null, null);
    S.clusters.forEach((c) => add(c.name + " · " + c.n, c.id, c.color));
  }

  function renderMap(items) {
    const S = space();
    if (!S) {
      wall.innerHTML = view === "style"
        ? '<p class="empty">No style map yet. Run python analyze_style.py</p>'
        : '<p class="empty">No color map yet. Run python analyze_visual.py</p>';
      mosaicKey = "";
      return;
    }
    const list = mapImages(items);
    countEl.textContent = list.length + (view === "style" ? " in style space" : " in color space");
    const key = view + ":" + (clusterOn == null ? "*" : clusterOn) + ":" + list.map((im) => im.id).join("|");
    if (!list.length) {
      wall.innerHTML = view === "style"
        ? '<p class="empty">Nothing in this style.</p>'
        : '<p class="empty">Nothing in this color group.</p>';
      mosaicKey = "";
      return;
    }
    if (key !== mosaicKey) {
      const canvas = document.createElement("div");
      canvas.className = "mosaic";
      list.forEach((im) => {
        const fig = document.createElement("figure");
        fig.className = "tile";
        fig.dataset.id = im.id;
        fig.dataset.work = im.work_id || "";
        fig.tabIndex = 0;
        const img = document.createElement("img");
        img.src = im.src;
        img.alt = im.title || "Painting";
        img.loading = "lazy";
        img.decoding = "async";
        const cap = document.createElement("figcaption");
        cap.innerHTML = caption(im);
        fig.append(img, cap);
        fig.addEventListener("mouseenter", () => {
          if (hoverId === im.id) return;
          hoverId = im.id;
          placeMap(list);
        });
        fig.addEventListener("click", (e) => {
          if (e.target.closest("a")) return;
          toggle(im.id, list);
        });
        canvas.append(fig);
      });
      canvas.addEventListener("mouseleave", () => {
        if (!hoverId) return;
        hoverId = null;
        placeMap(list);
      });
      wall.replaceChildren(canvas);
      mosaicKey = key;
      mapCache = null;
    }
    placeMap(list);
  }

  function toggle(id, items) {
    openId = openId === id ? null : id;
    layout(items);
    const im = C.images.find((x) => x.id === id);
    if (im && im.work_id) selectWork(im.work_id, false);
    const tile = wall.querySelector('.tile[data-id="' + id + '"]');
    if (tile && openId) tile.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function renderList() {
    const q = (listQuery || query).trim().toLowerCase();
    let rows = works.filter((w) => {
      if (listFilter === "extra") return w.extra;
      if (w.extra) return false;
      return listFilter === "all" || w.status === listFilter;
    });
    if (q) {
      rows = rows.filter((w) => (w.title + " " + (w.year || "") + " " + (w.collection || "")).toLowerCase().includes(q));
    }
    rows.sort((a, b) => {
      if (listSort === "gap" && a.status !== b.status) return a.status === "missing" ? -1 : 1;
      if (listSort === "title") return a.title.localeCompare(b.title);
      return (a.year || 9999) - (b.year || 9999) || a.title.localeCompare(b.title);
    });
    listEl.innerHTML = "";
    rows.forEach((w) => {
      const li = document.createElement("li");
      li.className = w.status + (w.id === selectedWork ? " on" : "");
      li.dataset.id = w.id;
      const sub = w.collection || (w.status === "have" ? (w.images.length > 1 ? w.images.length + " files" : "in collection") : "not in folders");
      li.innerHTML = '<i class="box"></i><span class="yr">' + (w.year || "—") + '</span><span class="wt"><b>'
        + esc(w.title) + '</b><span class="sub">' + esc(sub) + "</span></span>";
      li.addEventListener("click", () => selectWork(w.id, true));
      listEl.append(li);
    });
  }

  function selectWork(id, fromList) {
    selectedWork = id;
    const w = byWork[id];
    document.querySelectorAll("#list li").forEach((li) => li.classList.toggle("on", li.dataset.id === id));
    const li = listEl.querySelector('li[data-id="' + id + '"]');
    if (li && fromList) li.scrollIntoView({ block: "nearest" });
    if (!w) return;
    if (w.status === "missing") {
      openId = null;
      layout();
      const link = (w.sources && w.sources[0]) ? ' <a href="' + esc(w.sources[0]) + '" target="_blank" rel="noopener">Record</a>' : "";
      note.hidden = false;
      note.innerHTML = "No image matched <b>" + esc(w.title) + "</b>"
        + (w.year ? ", " + w.year : "")
        + (w.collection ? " · " + esc(w.collection) : "")
        + "." + link;
      return;
    }
    note.hidden = true;
    if (!fromList) return;
    const tile = wall.querySelector('.tile[data-work="' + id + '"]');
    if (!tile) {
      query = "";
      document.getElementById("q").value = "";
      mode = "works";
      document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === "works"));
      renderWall();
    }
    const tile2 = wall.querySelector('.tile[data-work="' + id + '"]');
    if (tile2) {
      openId = tile2.dataset.id;
      layout();
      tile2.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  document.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
    sortKey = b.dataset.sort;
    document.querySelectorAll("[data-sort]").forEach((x) => x.classList.toggle("on", x === b));
    renderWall();
  }));
  document.getElementById("dir").addEventListener("click", (e) => {
    sortDir *= -1;
    e.currentTarget.textContent = sortDir === 1 ? "↓" : "↑";
    e.currentTarget.classList.toggle("on", sortDir === -1);
    renderWall();
  });
  document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("on", x === b));
    mosaicKey = "";
    mapCache = null;
    renderWall();
  }));
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    view = b.dataset.view;
    document.querySelectorAll("[data-view]").forEach((x) => x.classList.toggle("on", x === b));
    hoverId = null;
    openId = null;
    clusterOn = null;
    mosaicKey = "";
    mapCache = null;
    renderLegend();
    renderWall();
  }));
  document.querySelectorAll("[data-list]").forEach((b) => b.addEventListener("click", () => {
    listFilter = b.dataset.list;
    document.querySelectorAll("[data-list]").forEach((x) => x.classList.toggle("on", x === b));
    renderList();
  }));
  document.querySelectorAll("[data-lsort]").forEach((b) => b.addEventListener("click", () => {
    listSort = b.dataset.lsort;
    document.querySelectorAll("[data-lsort]").forEach((x) => x.classList.toggle("on", x === b));
    renderList();
  }));
  document.getElementById("q").addEventListener("input", (e) => {
    query = e.target.value;
    if (query) note.hidden = true;
    renderWall();
    renderList();
  });
  document.getElementById("q-list").addEventListener("input", (e) => { listQuery = e.target.value; renderList(); });
  document.getElementById("scale").addEventListener("input", () => renderWall());
  window.addEventListener("resize", () => renderWall());
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && openId) { openId = null; layout(); }
  });

  renderList();
  renderLegend();
  renderWall();
})();
