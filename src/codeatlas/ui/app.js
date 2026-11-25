/* CodeAtlas Architecture Studio Application Controller */
(function () {
  "use strict";

  const Layouts = window.CodeAtlasLayouts;
  const CanvasEngine = window.CodeAtlasCanvas;

  const LAYER_ORDER = Layouts.LAYER_ORDER;
  const LAYER_LABELS = Layouts.LAYER_LABELS;

  const THEME_STORAGE_KEY = "codeatlas_theme";
  const RING_CIRCUMFERENCE = 2 * Math.PI * 17;

  const BOILERPLATE_PATH_SEGMENTS = [
    "database/migrations/",
    "database/seeders/",
    "lang/",
    "public/js/filament/",
    "public/vendor/",
    "node_modules/",
  ];

  const KEYWORDS_SET = new Set([
    "as", "assert", "async", "await", "break", "case", "catch", "class",
    "const", "continue", "def", "default", "defer", "del", "do", "echo",
    "elif", "else", "enum", "except", "export", "extends", "false", "False",
    "finally", "fn", "for", "from", "func", "function", "global", "go",
    "if", "implements", "import", "in", "interface", "is", "lambda", "let",
    "match", "namespace", "new", "None", "null", "or", "package", "pass",
    "private", "protected", "public", "raise", "readonly", "return", "select",
    "self", "static", "struct", "switch", "this", "throw", "trait", "true",
    "True", "try", "type", "typeof", "undefined", "use", "var", "void",
    "while", "with", "yield",
  ]);

  const TOKEN_REGEX =
    /(\/\/[^\n]*|#(?![\[])[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|#\[[^\]]*\]|@[A-Za-z_][A-Za-z0-9_]*|\b0x[0-9a-fA-F]+\b|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b)/g;

  const state = {
    isStatic: false,
    repoName: "",
    watchEnabled: false,
    graph: null,
    health: null,
    nodeById: new Map(),
    layoutMode: "layers",
    viewMode: "modules",
    activeClusterKey: null,
    drillKind: "cluster",
    drillLabel: "",
    drillIncludeNeighbors: false,
    focusPreset: "all",
    explorerOpen: false,
    explorerTab: "folders",
    explorerQuery: "",
    expandedFolders: new Set(),
    activeLayerFilter: null,
    showTests: true,
    showCyclesOnly: false,
    showParticles: true,
    theme: "obsidian",
    selectedNodeId: null,
    selectedClusterId: null,
    currentClusterGraph: null,
    lastClusterCount: 0,
    inspectorMode: "node",
    activeTab: "overview",
    spotlight: null,
    traceFlow: null,
    blastCache: new Map(),
    traceCache: new Map(),
    fileCache: new Map(),
    paletteItems: [],
    paletteMatches: [],
    paletteIndex: 0,
    eventSource: null,
    hasFittedOnce: false,
    hasInitializedViewDefaults: false,
  };

  let engine = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function clearNode(container) {
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }
  }

  function getSvgNamespace() {
    const svgEl = document.querySelector(".brand-mark svg");
    return svgEl ? svgEl.namespaceURI : null;
  }

  function createChevronSvg() {
    const ns = getSvgNamespace();
    if (!ns) {
      return el("span", "tree-chevron-fallback", ">");
    }
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "12");
    svg.setAttribute("height", "12");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    const poly = document.createElementNS(ns, "polyline");
    poly.setAttribute("points", "9 18 15 12 9 6");
    svg.appendChild(poly);
    return svg;
  }

  function createFolderSvg() {
    const ns = getSvgNamespace();
    if (!ns) {
      return el("span", "tree-folder-icon", "/");
    }
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "13");
    svg.setAttribute("height", "13");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.9");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "tree-folder-icon");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z");
    svg.appendChild(path);
    return svg;
  }

  function safeStorageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_err) {
      return null;
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_err) {
      /* ignore storage restrictions */
    }
  }

  function showToast(message) {
    const region = document.getElementById("toast-region");
    if (!region) {
      return;
    }
    const toast = el("div", "toast-item", message);
    region.appendChild(toast);
    window.setTimeout(() => {
      if (toast.parentNode === region) {
        region.removeChild(toast);
      }
    }, 3200);
  }

  function getSavedOrDefaultTheme() {
    const saved = safeStorageGet(THEME_STORAGE_KEY);
    if (saved === "paper" || saved === "obsidian") {
      return saved;
    }
    const attr = document.documentElement.getAttribute("data-theme");
    return attr === "paper" ? "paper" : "obsidian";
  }

  function applyTheme(themeName) {
    const next = themeName === "paper" ? "paper" : "obsidian";
    state.theme = next;
    document.documentElement.setAttribute("data-theme", next);
    safeStorageSet(THEME_STORAGE_KEY, next);
    if (engine) {
      engine.setTheme(next);
    }
  }

  function toggleTheme() {
    applyTheme(state.theme === "obsidian" ? "paper" : "obsidian");
  }

  function normalizePosixPath(rawPath) {
    return String(rawPath || "")
      .replace(/\\/g, "/")
      .replace(/^\/+/, "");
  }

  function isBoilerplatePath(rawPath) {
    const norm = normalizePosixPath(rawPath).toLowerCase();
    for (const seg of BOILERPLATE_PATH_SEGMENTS) {
      const bare = seg.slice(0, -1);
      if (norm === bare || norm.startsWith(seg) || norm.includes("/" + seg)) {
        return true;
      }
    }
    return false;
  }

  function computeHealthSets(graph, health) {
    const cycleSet = new Set();
    const violationSet = new Set();
    const godSet = new Set((health && health.hotspots) || []);
    const deadSet = new Set((health && health.dead_files) || []);

    for (const e of (graph && graph.edges) || []) {
      if (e.is_cycle) {
        cycleSet.add(e.source);
        cycleSet.add(e.target);
      }
      if (e.is_violation) {
        violationSet.add(e.source);
        violationSet.add(e.target);
      }
    }

    for (const c of (health && health.cycles) || []) {
      for (const nid of c.nodes || []) {
        cycleSet.add(nid);
      }
    }

    for (const smell of (health && health.smells) || []) {
      for (const nid of smell.nodes || []) {
        if (smell.kind === "circular_dependency") {
          cycleSet.add(nid);
        } else if (smell.kind === "layer_violation") {
          violationSet.add(nid);
        } else if (smell.kind === "god_module") {
          godSet.add(nid);
        } else if (smell.kind === "dead_code") {
          deadSet.add(nid);
        }
      }
    }

    const issueSet = new Set();
    for (const n of (graph && graph.nodes) || []) {
      const hs = n.health_status || "healthy";
      if (
        cycleSet.has(n.id) ||
        violationSet.has(n.id) ||
        godSet.has(n.id) ||
        hs === "critical" ||
        hs === "cycle" ||
        hs === "violation" ||
        hs === "hotspot" ||
        hs === "god"
      ) {
        issueSet.add(n.id);
      }
    }

    return { cycleSet, violationSet, godSet, deadSet, issueSet };
  }

  function computeRoutesFlowNodeSet(graph) {
    const nodes = (graph && graph.nodes) || [];
    const edges = (graph && graph.edges) || [];
    const outgoing = new Map();
    for (const n of nodes) {
      outgoing.set(n.id, []);
    }
    for (const e of edges) {
      if (outgoing.has(e.source) && outgoing.has(e.target)) {
        outgoing.get(e.source).push(e.target);
      }
    }

    const visited = new Set();
    const queue = [];
    for (const n of nodes) {
      if (
        n.is_entrypoint ||
        n.layer === "entrypoint" ||
        (Array.isArray(n.routes) && n.routes.length > 0)
      ) {
        visited.add(n.id);
        queue.push(n.id);
      }
    }

    let head = 0;
    while (head < queue.length) {
      const curr = queue[head];
      head += 1;
      for (const nextId of outgoing.get(curr) || []) {
        if (!visited.has(nextId)) {
          visited.add(nextId);
          queue.push(nextId);
        }
      }
    }
    return visited;
  }

  function getPresetFilteredGraph() {
    if (!state.graph) {
      return { nodes: [], edges: [], clusters: [] };
    }
    const allNodes = state.graph.nodes || [];
    const allEdges = state.graph.edges || [];

    let nodes = allNodes;
    if (state.focusPreset === "core") {
      nodes = allNodes.filter(
        (n) => n.layer !== "test" && !isBoilerplatePath(n.path || n.id)
      );
    } else if (state.focusPreset === "issues") {
      const sets = computeHealthSets(state.graph, state.health);
      nodes = allNodes.filter((n) => sets.issueSet.has(n.id));
    } else if (state.focusPreset === "routes") {
      const flowSet = computeRoutesFlowNodeSet(state.graph);
      nodes = allNodes.filter((n) => flowSet.has(n.id));
    }

    const visibleIds = new Set(nodes.map((n) => n.id));
    const edges = allEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target)
    );
    return {
      nodes,
      edges,
      clusters: state.graph.clusters || [],
    };
  }

  function getBaseFilteredModuleGraph() {
    const presetGraph = getPresetFilteredGraph();
    const nodes = presetGraph.nodes.filter((n) => {
      if (!state.showTests && n.layer === "test") {
        return false;
      }
      if (state.activeLayerFilter && n.layer !== state.activeLayerFilter) {
        return false;
      }
      return true;
    });
    const visibleIds = new Set(nodes.map((n) => n.id));
    const edges = presetGraph.edges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target)
    );
    return {
      nodes,
      edges,
      clusters: presetGraph.clusters || [],
    };
  }

  function buildClusterGraphSafe(moduleGraph) {
    if (Layouts && typeof Layouts.buildClusterGraph === "function") {
      return Layouts.buildClusterGraph(moduleGraph, state.health, {});
    }
    return {
      nodes: [],
      edges: [],
      clusterMembers: new Map(),
      clusterLookup: new Map(),
    };
  }

  function getDrilldownFilteredGraph(baseGraph, clusterGraph) {
    if (!state.activeClusterKey) {
      return baseGraph;
    }

    let memberIds = new Set();
    if (state.drillKind === "cluster" && clusterGraph) {
      const cid = "cluster:" + state.activeClusterKey;
      if (clusterGraph.clusterMembers && clusterGraph.clusterMembers.has(cid)) {
        memberIds = new Set(clusterGraph.clusterMembers.get(cid));
      } else {
        for (const cNode of clusterGraph.nodes || []) {
          if (cNode.clusterKey === state.activeClusterKey && Array.isArray(cNode.memberIds)) {
            memberIds = new Set(cNode.memberIds);
            break;
          }
        }
      }
    } else {
      const key = normalizePosixPath(state.activeClusterKey);
      for (const n of baseGraph.nodes || []) {
        const p = normalizePosixPath(n.path || n.id);
        if (key === "(root)") {
          if (!p.includes("/")) {
            memberIds.add(n.id);
          }
        } else if (p === key || p.startsWith(key + "/")) {
          memberIds.add(n.id);
        }
      }
    }

    const visibleIds = new Set(memberIds);
    if (state.drillIncludeNeighbors && memberIds.size > 0) {
      for (const e of baseGraph.edges || []) {
        if (memberIds.has(e.source)) {
          visibleIds.add(e.target);
        }
        if (memberIds.has(e.target)) {
          visibleIds.add(e.source);
        }
      }
    }

    const nodes = (baseGraph.nodes || []).filter((n) => visibleIds.has(n.id));
    const finalIds = new Set(nodes.map((n) => n.id));
    const edges = (baseGraph.edges || []).filter(
      (e) => finalIds.has(e.source) && finalIds.has(e.target)
    );
    return {
      nodes,
      edges,
      clusters: baseGraph.clusters || [],
      memberCount: memberIds.size,
    };
  }

  function getActiveCanvasGraph() {
    if (state.viewMode === "overview" && !state.activeClusterKey && state.currentClusterGraph) {
      return state.currentClusterGraph;
    }
    const baseModules = getBaseFilteredModuleGraph();
    if (state.activeClusterKey) {
      const clusterGraph = state.drillKind === "cluster" ? buildClusterGraphSafe(baseModules) : null;
      return getDrilldownFilteredGraph(baseModules, clusterGraph);
    }
    return baseModules;
  }

  function findClusterKeyForNode(nodeId) {
    const baseModules = getBaseFilteredModuleGraph();
    const clusterGraph = buildClusterGraphSafe(baseModules);
    for (const cNode of clusterGraph.nodes || []) {
      if (Array.isArray(cNode.memberIds) && cNode.memberIds.includes(nodeId)) {
        return { key: cNode.clusterKey, label: cNode.name || cNode.clusterKey };
      }
    }
    if (clusterGraph.clusterMembers) {
      for (const [cid, members] of clusterGraph.clusterMembers.entries()) {
        if (members && members.has(nodeId)) {
          const summary = clusterGraph.clusterLookup ? clusterGraph.clusterLookup.get(cid) : null;
          const key = (summary && summary.clusterKey) || cid.replace(/^cluster:/, "");
          return { key, label: (summary && summary.name) || key };
        }
      }
    }
    return null;
  }

  function syncViewModeControls(visibleDrillCount) {
    const overviewBtn = document.getElementById("view-mode-overview");
    const modulesBtn = document.getElementById("view-mode-modules");
    const isOverviewActive = state.viewMode === "overview" && !state.activeClusterKey;
    if (overviewBtn) {
      overviewBtn.classList.toggle("is-active", isOverviewActive);
      overviewBtn.setAttribute("aria-pressed", String(isOverviewActive));
    }
    if (modulesBtn) {
      modulesBtn.classList.toggle("is-active", !isOverviewActive);
      modulesBtn.setAttribute("aria-pressed", String(!isOverviewActive));
    }

    const breadcrumbBar = document.getElementById("drill-breadcrumb-bar");
    if (breadcrumbBar) {
      const inDrilldown = Boolean(state.activeClusterKey);
      breadcrumbBar.classList.toggle("hidden", !inDrilldown);
      if (inDrilldown) {
        const allLabel = document.getElementById("breadcrumb-all-label");
        const currLabel = document.getElementById("breadcrumb-current-label");
        const countLabel = document.getElementById("breadcrumb-file-count");
        const neighborsBtn = document.getElementById("breadcrumb-neighbors-btn");
        if (allLabel) {
          allLabel.textContent = "All Clusters (" + state.lastClusterCount + ")";
        }
        if (currLabel) {
          currLabel.textContent = state.drillLabel || state.activeClusterKey;
        }
        if (countLabel) {
          const count = visibleDrillCount !== undefined ? visibleDrillCount : 0;
          countLabel.textContent = String(count) + (count === 1 ? " file" : " files");
        }
        if (neighborsBtn) {
          neighborsBtn.classList.toggle("is-active", state.drillIncludeNeighbors);
          neighborsBtn.setAttribute("aria-pressed", String(state.drillIncludeNeighbors));
        }
      }
    }
  }

  function updateCanvasAndStats(fitAfter) {
    if (!state.graph || !engine) {
      return;
    }

    const baseModules = getBaseFilteredModuleGraph();
    const clusterGraph = buildClusterGraphSafe(baseModules);
    state.lastClusterCount = (clusterGraph.nodes || []).length;

    let displayGraph = baseModules;
    let isClusterView = false;

    if (state.viewMode === "overview" && !state.activeClusterKey) {
      state.currentClusterGraph = clusterGraph;
      displayGraph = clusterGraph;
      isClusterView = true;
    } else if (state.activeClusterKey) {
      state.currentClusterGraph = null;
      displayGraph = getDrilldownFilteredGraph(baseModules, clusterGraph);
    } else {
      state.currentClusterGraph = null;
    }

    const focusId = isClusterView ? state.selectedClusterId : state.selectedNodeId;
    const layoutResult = Layouts.computeLayout(state.layoutMode, displayGraph, state.health, {
      focusNodeId: focusId,
      blastRadius:
        focusId && state.blastCache.has(focusId) ? state.blastCache.get(focusId) : null,
    });

    engine.setGraphData({
      nodes: displayGraph.nodes,
      edges: displayGraph.edges,
      layoutResult,
      repoName: state.repoName,
      animate: true,
    });

    syncViewModeControls(displayGraph.nodes.length);

    const emptyState = document.getElementById("canvas-empty-state");
    if (emptyState) {
      emptyState.classList.toggle("hidden", displayGraph.nodes.length > 0);
    }

    const statNodes = document.getElementById("stat-nodes");
    const statNodesLabel = document.getElementById("stat-nodes-label");
    const statEdges = document.getElementById("stat-edges");
    const statCycles = document.getElementById("stat-cycles");
    if (statNodes) {
      statNodes.textContent = String(displayGraph.nodes.length);
    }
    if (statNodesLabel) {
      statNodesLabel.textContent = isClusterView
        ? displayGraph.nodes.length === 1
          ? "cluster"
          : "clusters"
        : displayGraph.nodes.length === 1
          ? "module"
          : "modules";
    }
    if (statEdges) {
      statEdges.textContent = String(displayGraph.edges.length);
    }
    if (statCycles) {
      const cycleCount =
        state.health && Array.isArray(state.health.cycles) ? state.health.cycles.length : 0;
      statCycles.textContent = String(cycleCount);
    }

    if (fitAfter || !state.hasFittedOnce) {
      state.hasFittedOnce = true;
      engine.fitToView(78, fitAfter ? 450 : 0);
    }
  }

  function renderHealthRing() {
    const scoreEl = document.getElementById("health-score-num");
    const gradeEl = document.getElementById("health-grade-tag");
    const arcEl = document.getElementById("health-ring-arc");
    const btnEl = document.getElementById("health-ring-btn");
    if (!state.health || !scoreEl || !gradeEl || !arcEl || !btnEl) {
      return;
    }

    const score = Math.max(0, Math.min(100, Math.round(Number(state.health.score || 0))));
    const grade = state.health.grade || "A";
    scoreEl.textContent = String(score);
    gradeEl.textContent = grade;

    const offset = RING_CIRCUMFERENCE * (1 - score / 100);
    arcEl.setAttribute("stroke-dashoffset", offset.toFixed(2));
    btnEl.classList.toggle("is-warn", score >= 60 && score < 80);
    btnEl.classList.toggle("is-bad", score < 60);
  }

  function renderLayerPills() {
    const container = document.getElementById("layer-pills");
    if (!container || !state.graph) {
      return;
    }
    clearNode(container);

    const presetGraph = getPresetFilteredGraph();
    const counts = new Map();
    for (const layer of LAYER_ORDER) {
      counts.set(layer, 0);
    }
    for (const node of presetGraph.nodes || []) {
      const l = counts.has(node.layer) ? node.layer : "shared";
      counts.set(l, counts.get(l) + 1);
    }

    const allPill = el(
      "button",
      "layer-pill" + (state.activeLayerFilter === null ? " is-active" : "")
    );
    allPill.type = "button";
    allPill.setAttribute("aria-pressed", String(state.activeLayerFilter === null));
    allPill.appendChild(el("span", null, "All"));
    allPill.appendChild(el("span", "pill-count", String((presetGraph.nodes || []).length)));
    allPill.addEventListener("click", () => {
      state.activeLayerFilter = null;
      renderLayerPills();
      updateCanvasAndStats(true);
    });
    container.appendChild(allPill);

    for (const layer of LAYER_ORDER) {
      const count = counts.get(layer) || 0;
      if (count === 0) {
        continue;
      }
      const isActive = state.activeLayerFilter === layer;
      const pill = el("button", "layer-pill" + (isActive ? " is-active" : ""));
      pill.type = "button";
      pill.setAttribute("aria-pressed", String(isActive));
      const dot = el("span", "layer-dot");
      dot.setAttribute("data-layer", layer);
      dot.setAttribute("aria-hidden", "true");
      pill.appendChild(dot);
      pill.appendChild(el("span", null, LAYER_LABELS[layer] || layer));
      pill.appendChild(el("span", "pill-count", String(count)));
      pill.addEventListener("click", () => {
        if (layer === "test" && !state.showTests) {
          state.showTests = true;
          syncToggleButtons();
        }
        state.activeLayerFilter = state.activeLayerFilter === layer ? null : layer;
        renderLayerPills();
        updateCanvasAndStats(true);
      });
      container.appendChild(pill);
    }
  }

  function syncToggleButtons() {
    const testsBtn = document.getElementById("toggle-tests-btn");
    const cyclesBtn = document.getElementById("toggle-cycles-btn");
    const particlesBtn = document.getElementById("toggle-particles-btn");
    if (testsBtn) {
      testsBtn.classList.toggle("is-active", state.showTests);
      testsBtn.setAttribute("aria-pressed", String(state.showTests));
    }
    if (cyclesBtn) {
      cyclesBtn.classList.toggle("is-active", state.showCyclesOnly);
      cyclesBtn.setAttribute("aria-pressed", String(state.showCyclesOnly));
    }
    if (particlesBtn) {
      particlesBtn.classList.toggle("is-active", state.showParticles);
      particlesBtn.setAttribute("aria-pressed", String(state.showParticles));
    }
  }

  function setViewMode(mode, options) {
    const opts = options || {};
    if (mode !== "overview" && mode !== "modules") {
      return;
    }
    state.viewMode = mode;
    if (mode === "overview") {
      state.activeClusterKey = null;
      state.drillIncludeNeighbors = false;
      state.selectedNodeId = null;
      if (state.inspectorMode === "node") {
        closeInspector();
      }
      clearSpotlightAndTrace();
    } else {
      state.selectedClusterId = null;
      if (state.inspectorMode === "cluster") {
        closeInspector();
      }
      if (state.spotlight && String(state.spotlight.targetId).startsWith("cluster:")) {
        clearSpotlightAndTrace();
      }
      if (state.traceFlow && String(state.traceFlow.entrypoint_id).startsWith("cluster:")) {
        clearSpotlightAndTrace();
      }
    }

    syncViewModeControls();
    updateCanvasAndStats(!opts.skipFit);
    if (state.explorerOpen) {
      renderExplorer();
    }
  }

  function enterClusterDrilldown(clusterKey, options) {
    const opts = options || {};
    if (!clusterKey) {
      return;
    }
    state.viewMode = "modules";
    state.activeClusterKey = clusterKey;
    state.drillKind = opts.kind || "cluster";
    state.drillLabel = opts.label || clusterKey;
    state.selectedClusterId = null;

    if (!opts.keepInspector && state.inspectorMode === "cluster") {
      closeInspector();
    }
    if (state.spotlight && String(state.spotlight.targetId).startsWith("cluster:")) {
      clearSpotlightAndTrace();
    }
    if (state.traceFlow && String(state.traceFlow.entrypoint_id).startsWith("cluster:")) {
      clearSpotlightAndTrace();
    }

    updateCanvasAndStats(!opts.skipFit);
    if (state.explorerOpen) {
      renderExplorer();
    }
  }

  function exitDrilldownToOverview() {
    state.activeClusterKey = null;
    state.drillIncludeNeighbors = false;
    setViewMode("overview");
  }

  function setLayoutMode(mode) {
    if (!["layers", "constellation", "blast", "health"].includes(mode)) {
      return;
    }
    state.layoutMode = mode;
    const buttons = document.querySelectorAll(".layout-btn");
    for (const btn of buttons) {
      const active = btn.getAttribute("data-layout") === mode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", String(active));
    }

    if (mode === "blast") {
      const activeGraph = getActiveCanvasGraph();
      let targetId =
        state.viewMode === "overview" && !state.activeClusterKey
          ? state.selectedClusterId
          : state.selectedNodeId;
      if (!targetId && activeGraph && Array.isArray(activeGraph.nodes) && activeGraph.nodes.length > 0) {
        const sortedHubs = activeGraph.nodes.slice().sort((a, b) => {
          const degA = Number(a.fan_in || 0) + Number(a.fan_out || 0);
          const degB = Number(b.fan_in || 0) + Number(b.fan_out || 0);
          return (degB - degA) || (Number(b.loc || 0) - Number(a.loc || 0));
        });
        targetId = sortedHubs[0].id;
      }
      if (targetId) {
        activateBlastSpotlight(targetId, true);
        return;
      }
    }

    updateCanvasAndStats(true);
  }

  function updateModeBanner() {
    const banner = document.getElementById("active-mode-banner");
    const badge = document.getElementById("mode-banner-badge");
    const text = document.getElementById("mode-banner-text");
    if (!banner || !badge || !text) {
      return;
    }

    if (state.traceFlow) {
      badge.textContent = "Trace Flow";
      const targetCount = (state.traceFlow.data_targets || []).length;
      text.textContent =
        state.traceFlow.entrypoint_id +
        " reaching " +
        targetCount +
        (targetCount === 1 ? " data target" : " data targets");
      banner.classList.remove("hidden");
    } else if (state.spotlight) {
      badge.textContent = "Blast Radius";
      const upCount = state.spotlight.upstreamMap ? state.spotlight.upstreamMap.size : 0;
      const downCount = state.spotlight.downstreamMap ? state.spotlight.downstreamMap.size : 0;
      text.textContent =
        state.spotlight.targetId +
        " (" +
        upCount +
        " upstream, " +
        downCount +
        " downstream)";
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  function clearSpotlightAndTrace() {
    state.spotlight = null;
    state.traceFlow = null;
    if (engine) {
      engine.setSpotlight(null);
      engine.setTraceFlow(null);
    }
    updateModeBanner();
    syncActionButtons();
  }

  async function loadBlastRadius(nodeId) {
    if (!nodeId || !state.graph) {
      return null;
    }
    if (state.blastCache.has(nodeId)) {
      return state.blastCache.get(nodeId);
    }
    const isCluster = String(nodeId).startsWith("cluster:");
    const targetGraph = isCluster ? getActiveCanvasGraph() : state.graph;
    const localResult = Layouts.computeBlastRadiusBFS(targetGraph, nodeId, 5);
    if (state.isStatic || isCluster) {
      state.blastCache.set(nodeId, localResult);
      return localResult;
    }
    try {
      const resp = await fetch(
        "/api/blast-radius?node=" + encodeURIComponent(nodeId) + "&depth=5"
      );
      if (resp.ok) {
        const data = await resp.json();
        state.blastCache.set(nodeId, data);
        return data;
      }
    } catch (_err) {
      /* fallback to local BFS */
    }
    state.blastCache.set(nodeId, localResult);
    return localResult;
  }

  async function loadTraceFlow(nodeId) {
    if (!nodeId || !state.graph) {
      return null;
    }
    if (state.traceCache.has(nodeId)) {
      return state.traceCache.get(nodeId);
    }
    const isCluster = String(nodeId).startsWith("cluster:");
    const targetGraph = isCluster ? getActiveCanvasGraph() : state.graph;
    const localResult = Layouts.computeTraceFlowBFS(targetGraph, nodeId, 8);
    if (state.isStatic || isCluster) {
      state.traceCache.set(nodeId, localResult);
      return localResult;
    }
    try {
      const resp = await fetch("/api/trace?node=" + encodeURIComponent(nodeId));
      if (resp.ok) {
        const data = await resp.json();
        state.traceCache.set(nodeId, data);
        return data;
      }
    } catch (_err) {
      /* fallback to local BFS */
    }
    state.traceCache.set(nodeId, localResult);
    return localResult;
  }

  async function activateBlastSpotlight(nodeId, shouldFit) {
    if (!nodeId) {
      return;
    }
    state.traceFlow = null;
    if (engine) {
      engine.setTraceFlow(null);
    }
    const blast = await loadBlastRadius(nodeId);
    if (!blast) {
      return;
    }
    const upstreamMap = new Map();
    const downstreamMap = new Map();
    for (const u of blast.upstream || []) {
      upstreamMap.set(u.node_id, u.depth);
    }
    for (const d of blast.downstream || []) {
      downstreamMap.set(d.node_id, d.depth);
    }
    state.spotlight = {
      targetId: nodeId,
      upstreamMap,
      downstreamMap,
      blast,
    };
    if (engine) {
      engine.setSpotlight(state.spotlight);
    }
    if (state.layoutMode === "blast" || shouldFit) {
      updateCanvasAndStats(Boolean(shouldFit));
    }
    updateModeBanner();
    syncActionButtons();
    if (state.selectedNodeId === nodeId && state.activeTab === "impact") {
      renderImpactTab(state.nodeById.get(nodeId));
    }
  }

  async function activateTraceFlow(nodeId) {
    if (!nodeId) {
      return;
    }
    state.spotlight = null;
    if (engine) {
      engine.setSpotlight(null);
    }
    const trace = await loadTraceFlow(nodeId);
    if (!trace) {
      return;
    }
    state.traceFlow = trace;
    if (engine) {
      engine.setTraceFlow(trace);
    }
    updateModeBanner();
    syncActionButtons();
    if ((trace.paths || []).length === 0) {
      showToast("No downstream data-layer path reachable from " + nodeId);
    }
    if (state.selectedNodeId === nodeId && state.activeTab === "impact") {
      renderImpactTab(state.nodeById.get(nodeId));
    }
  }

  function syncActionButtons() {
    const drillBtn = document.getElementById("action-drill-btn");
    const blastBtn = document.getElementById("action-blast-btn");
    const traceBtn = document.getElementById("action-trace-btn");

    const activeId =
      state.inspectorMode === "cluster" ? state.selectedClusterId : state.selectedNodeId;
    const node = state.selectedNodeId ? state.nodeById.get(state.selectedNodeId) : null;
    const clusterNode =
      state.inspectorMode === "cluster" &&
      state.selectedClusterId &&
      state.currentClusterGraph &&
      state.currentClusterGraph.clusterLookup
        ? state.currentClusterGraph.clusterLookup.get(state.selectedClusterId)
        : null;

    if (drillBtn) {
      drillBtn.classList.toggle("hidden", state.inspectorMode !== "cluster");
    }

    if (blastBtn) {
      const isBlastActive = Boolean(
        state.spotlight && activeId && state.spotlight.targetId === activeId
      );
      blastBtn.classList.toggle("is-active", isBlastActive);
    }

    if (traceBtn) {
      const canTrace = Boolean(
        (node &&
          (node.is_entrypoint ||
            node.layer === "entrypoint" ||
            (Array.isArray(node.routes) && node.routes.length > 0))) ||
          (clusterNode &&
            (Number(clusterNode.routesCount || 0) > 0 || clusterNode.layer === "entrypoint"))
      );
      traceBtn.classList.toggle("hidden", !canTrace);
      const isTraceActive = Boolean(
        state.traceFlow && activeId && state.traceFlow.entrypoint_id === activeId
      );
      traceBtn.classList.toggle("is-active", isTraceActive);
    }
  }

  function getHealthBadgeClass(hs) {
    if (hs === "critical" || hs === "cycle" || hs === "violation") {
      return "is-critical";
    }
    if (hs === "warning" || hs === "hotspot" || hs === "god" || hs === "dead") {
      return "is-warning";
    }
    return "is-healthy";
  }

  function navigateToModule(nodeId, options) {
    const opts = options || {};
    const node = state.nodeById.get(nodeId);
    if (!node) {
      return;
    }

    if (node.layer === "test" && !state.showTests) {
      state.showTests = true;
      syncToggleButtons();
    }
    if (state.activeLayerFilter && state.activeLayerFilter !== node.layer) {
      state.activeLayerFilter = null;
      renderLayerPills();
    }
    if (state.focusPreset !== "all") {
      const presetNodes = getPresetFilteredGraph().nodes;
      if (!presetNodes.some((n) => n.id === nodeId)) {
        state.focusPreset = "all";
        syncPresetButtons();
        renderLayerPills();
      }
    }

    let switchedCanvas = false;
    if (state.viewMode === "overview" && !state.activeClusterKey) {
      const clusterInfo = findClusterKeyForNode(nodeId);
      if (clusterInfo) {
        enterClusterDrilldown(clusterInfo.key, {
          kind: "cluster",
          label: clusterInfo.label,
          skipFit: true,
        });
      } else {
        setViewMode("modules", { skipFit: true });
      }
      switchedCanvas = true;
    } else if (state.activeClusterKey) {
      const currentVisible = getActiveCanvasGraph().nodes || [];
      if (!currentVisible.some((n) => n.id === nodeId)) {
        const clusterInfo = findClusterKeyForNode(nodeId);
        if (clusterInfo) {
          enterClusterDrilldown(clusterInfo.key, {
            kind: "cluster",
            label: clusterInfo.label,
            skipFit: true,
          });
        } else {
          state.activeClusterKey = null;
          state.drillIncludeNeighbors = false;
          updateCanvasAndStats(false);
        }
        switchedCanvas = true;
      }
    }

    if (!switchedCanvas) {
      const visibleNow = getActiveCanvasGraph().nodes || [];
      if (!visibleNow.some((n) => n.id === nodeId)) {
        updateCanvasAndStats(false);
      }
    }

    selectAndInspectNode(nodeId, {
      fly: opts.fly !== false,
      zoom: opts.zoom !== undefined ? opts.zoom : 1.15,
      tab: opts.tab,
      line: opts.line,
    });
  }

  function selectAndInspectNode(nodeId, options) {
    const opts = options || {};
    if (!nodeId) {
      closeInspector();
      return;
    }

    if (
      String(nodeId).startsWith("cluster:") &&
      state.currentClusterGraph &&
      state.currentClusterGraph.clusterLookup &&
      state.currentClusterGraph.clusterLookup.has(nodeId)
    ) {
      const cNode = state.currentClusterGraph.clusterLookup.get(nodeId);
      openInspectorForCluster(cNode, opts);
      return;
    }

    if (!state.nodeById.has(nodeId)) {
      closeInspector();
      return;
    }

    state.selectedClusterId = null;
    state.selectedNodeId = nodeId;
    state.inspectorMode = "node";
    if (engine) {
      engine.setSelection(nodeId);
    }

    if (state.explorerOpen) {
      renderExplorer();
    }

    if (state.layoutMode === "blast") {
      openInspectorForNode(state.nodeById.get(nodeId), opts.tab, opts.line);
      activateBlastSpotlight(nodeId, true);
      return;
    }

    if (opts.fly !== false && engine) {
      engine.flyTo(nodeId, opts.zoom);
    }

    openInspectorForNode(state.nodeById.get(nodeId), opts.tab, opts.line);
  }

  function closeInspector() {
    state.selectedNodeId = null;
    state.selectedClusterId = null;
    if (engine) {
      engine.setSelection(null);
    }
    const drawer = document.getElementById("inspector-drawer");
    if (drawer) {
      drawer.classList.add("hidden");
    }
    if (state.explorerOpen) {
      renderExplorer();
    }
  }

  function switchInspectorTab(tabName) {
    const allowed = ["overview", "impact", "symbols", "source"];
    const next = allowed.includes(tabName) ? tabName : "overview";
    state.activeTab = next;

    const tabs = document.querySelectorAll(".drawer-tab");
    for (const tab of tabs) {
      const active = tab.getAttribute("data-tab") === next;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    }

    for (const key of allowed) {
      const panel = document.getElementById("tab-panel-" + key);
      if (panel) {
        panel.classList.toggle("hidden", key !== next);
      }
    }
  }

  function openInspectorForCluster(clusterNode, options) {
    const opts = options || {};
    state.selectedClusterId = clusterNode.id;
    state.selectedNodeId = null;
    state.inspectorMode = "cluster";
    if (engine) {
      engine.setSelection(clusterNode.id);
      if (opts.fly) {
        engine.flyTo(clusterNode.id, opts.zoom);
      }
    }

    const drawer = document.getElementById("inspector-drawer");
    const titleEl = document.getElementById("inspector-title");
    const pathEl = document.getElementById("inspector-path");
    const badgesEl = document.getElementById("inspector-badges");
    const actionsEl = document.getElementById("inspector-actions");
    const tabsEl = document.getElementById("inspector-tabs");
    const panel = document.getElementById("tab-panel-overview");

    if (!drawer || !titleEl || !pathEl || !badgesEl || !actionsEl || !tabsEl || !panel) {
      return;
    }

    drawer.classList.remove("hidden");
    actionsEl.classList.remove("hidden");
    tabsEl.classList.add("hidden");

    titleEl.textContent = clusterNode.name || clusterNode.clusterKey;
    pathEl.textContent =
      clusterNode.clusterKey + " (" + String(clusterNode.fileCount || 0) + " modules)";

    clearNode(badgesEl);
    badgesEl.appendChild(el("span", "badge-tag is-info", clusterNode.layer || "shared"));
    badgesEl.appendChild(el("span", "badge-tag", clusterNode.language || "mixed"));
    const hs = clusterNode.health_status || "healthy";
    badgesEl.appendChild(el("span", "badge-tag " + getHealthBadgeClass(hs), hs));
    if (Number(clusterNode.routesCount || 0) > 0) {
      badgesEl.appendChild(
        el("span", "badge-tag is-info", String(clusterNode.routesCount) + " Routes")
      );
    }

    syncActionButtons();
    switchInspectorTab("overview");
    renderClusterOverviewPanel(clusterNode, panel);
  }

  function renderClusterOverviewPanel(clusterNode, panel) {
    clearNode(panel);

    const ctaBlock = el("div", "section-block");
    const drillBtn = el("button", "hud-btn drill-cta-btn");
    drillBtn.type = "button";
    drillBtn.textContent = "Drill Into Cluster (" + String(clusterNode.fileCount || 0) + " files)";
    drillBtn.addEventListener("click", () => {
      enterClusterDrilldown(clusterNode.clusterKey, {
        kind: "cluster",
        label: clusterNode.name || clusterNode.clusterKey,
      });
    });
    ctaBlock.appendChild(drillBtn);
    panel.appendChild(ctaBlock);

    const metricsSection = el("div", "section-block");
    metricsSection.appendChild(el("h3", "section-heading", "Cluster Telemetry"));
    const grid = el("div", "metric-grid");
    const cells = [
      ["Modules", String(clusterNode.fileCount || 0)],
      ["Total LOC", String(clusterNode.loc || 0)],
      ["HTTP Routes", String(clusterNode.routesCount || 0)],
      [
        "Issues",
        String(clusterNode.issueCount || 0) +
          (clusterNode.criticalCount ? " (" + clusterNode.criticalCount + " crit)" : ""),
      ],
    ];
    for (const pair of cells) {
      const cell = el("div", "metric-cell");
      cell.appendChild(el("span", "metric-label", pair[0]));
      cell.appendChild(el("span", "metric-value", pair[1]));
      grid.appendChild(cell);
    }
    metricsSection.appendChild(grid);
    panel.appendChild(metricsSection);

    const memberIds = Array.isArray(clusterNode.memberIds) ? clusterNode.memberIds : [];
    const memberNodes = memberIds
      .map((id) => state.nodeById.get(id))
      .filter(Boolean);
    const memberSet = new Set(memberIds);

    const layerCounts = new Map();
    for (const layer of LAYER_ORDER) {
      layerCounts.set(layer, 0);
    }
    for (const m of memberNodes) {
      const l = layerCounts.has(m.layer) ? m.layer : "shared";
      layerCounts.set(l, layerCounts.get(l) + 1);
    }

    const layerSection = el("div", "section-block");
    layerSection.appendChild(el("h3", "section-heading", "Layer Breakdown"));
    const layerList = el("div", "layer-breakdown-list");
    for (const layer of LAYER_ORDER) {
      const cnt = layerCounts.get(layer) || 0;
      if (cnt === 0) {
        continue;
      }
      const row = el("div", "layer-breakdown-row");
      const left = el("div", "layer-breakdown-left");
      const dot = el("span", "layer-dot");
      dot.setAttribute("data-layer", layer);
      dot.setAttribute("aria-hidden", "true");
      left.appendChild(dot);
      left.appendChild(el("span", null, LAYER_LABELS[layer] || layer));
      row.appendChild(left);
      row.appendChild(el("span", "layer-breakdown-count", String(cnt)));
      layerList.appendChild(row);
    }
    layerSection.appendChild(layerList);
    panel.appendChild(layerSection);

    const relatedSmells = ((state.health && state.health.smells) || []).filter(
      (s) => Array.isArray(s.nodes) && s.nodes.some((nid) => memberSet.has(nid))
    );
    const relatedCycles = ((state.health && state.health.cycles) || []).filter(
      (c) => Array.isArray(c.nodes) && c.nodes.some((nid) => memberSet.has(nid))
    );

    if (relatedCycles.length > 0 || relatedSmells.length > 0) {
      const findingsSec = el("div", "section-block");
      findingsSec.appendChild(
        el(
          "h3",
          "section-heading",
          "Cluster Findings (" + String(relatedCycles.length + relatedSmells.length) + ")"
        )
      );
      const stack = el("div", "list-stack");
      for (const cycle of relatedCycles.slice(0, 6)) {
        const card = el("div", "smell-row is-high");
        const head = el("div", "smell-head");
        head.appendChild(el("span", "smell-title", "Cycle Membership"));
        head.appendChild(el("span", "badge-tag is-critical", "Cycle"));
        card.appendChild(head);
        card.appendChild(
          el("p", "smell-desc", cycle.display_path || (cycle.nodes || []).join(" -> "))
        );
        stack.appendChild(card);
      }
      for (const smell of relatedSmells.slice(0, 8)) {
        const card = el("div", "smell-row is-" + (smell.severity || "medium"));
        const head = el("div", "smell-head");
        head.appendChild(el("span", "smell-title", smell.title));
        const sevClass =
          smell.severity === "high"
            ? "is-critical"
            : smell.severity === "medium"
              ? "is-warning"
              : "is-info";
        head.appendChild(el("span", "badge-tag " + sevClass, smell.rule_id));
        card.appendChild(head);
        card.appendChild(el("p", "smell-desc", smell.description));
        stack.appendChild(card);
      }
      findingsSec.appendChild(stack);
      panel.appendChild(findingsSec);
    }

    const sortedMembers = memberNodes.slice().sort((a, b) => {
      const rank = (n) => {
        const hsVal = n.health_status || "healthy";
        if (hsVal === "critical" || hsVal === "cycle" || hsVal === "violation") {
          return 0;
        }
        if (hsVal === "warning" || hsVal === "hotspot" || hsVal === "god" || hsVal === "dead") {
          return 1;
        }
        return 2;
      };
      return (rank(a) - rank(b)) || (Number(b.loc || 0) - Number(a.loc || 0)) || a.id.localeCompare(b.id);
    });

    const filesSec = el("div", "section-block");
    filesSec.appendChild(el("h3", "section-heading", "Member Files (" + sortedMembers.length + ")"));
    const stack = el("div", "list-stack");
    for (const m of sortedMembers) {
      const row = el("button", "row-button");
      row.type = "button";
      const main = el("div", "row-main");
      main.appendChild(el("span", "row-title", m.name || m.id));
      main.appendChild(
        el(
          "span",
          "row-subtitle",
          (m.layer || "shared") + " · " + String(m.loc || 0) + " LOC · CC " + String(m.complexity || 1)
        )
      );
      row.appendChild(main);
      const mHs = m.health_status || "healthy";
      row.appendChild(el("span", "badge-tag " + getHealthBadgeClass(mHs), mHs));
      row.addEventListener("click", () => {
        navigateToModule(m.id, { fly: true, zoom: 1.15, tab: "overview" });
      });
      stack.appendChild(row);
    }
    filesSec.appendChild(stack);
    panel.appendChild(filesSec);
  }

  function openHealthDiagnostics() {
    state.selectedNodeId = null;
    state.selectedClusterId = null;
    state.inspectorMode = "health";
    if (engine) {
      engine.setSelection(null);
    }
    const drawer = document.getElementById("inspector-drawer");
    const titleEl = document.getElementById("inspector-title");
    const pathEl = document.getElementById("inspector-path");
    const badgesEl = document.getElementById("inspector-badges");
    const actionsEl = document.getElementById("inspector-actions");
    const tabsEl = document.getElementById("inspector-tabs");

    if (!drawer || !titleEl || !pathEl || !badgesEl || !actionsEl || !tabsEl) {
      return;
    }

    drawer.classList.remove("hidden");
    actionsEl.classList.add("hidden");
    tabsEl.classList.add("hidden");

    const score = state.health ? Math.round(Number(state.health.score || 0)) : 0;
    const grade = state.health ? state.health.grade : "-";
    titleEl.textContent = "Architecture Health";
    pathEl.textContent = state.repoName + " diagnostics summary";

    clearNode(badgesEl);
    const gradeBadge = el(
      "span",
      "badge-tag " + (score >= 80 ? "is-healthy" : score >= 60 ? "is-warning" : "is-critical"),
      "Score " + score + " (" + grade + ")"
    );
    badgesEl.appendChild(gradeBadge);

    switchInspectorTab("overview");
    renderHealthOverviewPanel();
  }

  function renderHealthOverviewPanel() {
    const panel = document.getElementById("tab-panel-overview");
    if (!panel || !state.health) {
      return;
    }
    clearNode(panel);

    const bd = state.health.breakdown || {};
    const metricSection = el("div", "section-block");
    metricSection.appendChild(el("h3", "section-heading", "Score Breakdown"));
    const grid = el("div", "metric-grid");
    const cells = [
      ["Base Score", String(Math.round(Number(bd.base_score || 100)))],
      ["Cycle Penalty", "-" + Number(bd.cycle_penalty || 0).toFixed(1)],
      ["Violation Penalty", "-" + Number(bd.violation_penalty || 0).toFixed(1)],
      [
        "God & Dead Penalty",
        "-" + (Number(bd.god_penalty || 0) + Number(bd.dead_penalty || 0)).toFixed(1),
      ],
    ];
    for (const pair of cells) {
      const cell = el("div", "metric-cell");
      cell.appendChild(el("span", "metric-label", pair[0]));
      cell.appendChild(el("span", "metric-value", pair[1]));
      grid.appendChild(cell);
    }
    metricSection.appendChild(grid);
    panel.appendChild(metricSection);

    const cycles = state.health.cycles || [];
    const cycleSection = el("div", "section-block");
    cycleSection.appendChild(
      el("h3", "section-heading", "Circular Dependencies (" + cycles.length + ")")
    );
    if (cycles.length === 0) {
      cycleSection.appendChild(el("p", "smell-desc", "No circular dependencies detected."));
    } else {
      const stack = el("div", "list-stack");
      for (const cycle of cycles) {
        const btn = el("button", "row-button");
        btn.type = "button";
        const main = el("div", "row-main");
        main.appendChild(
          el("span", "row-title", cycle.display_path || (cycle.nodes || []).join(" -> "))
        );
        main.appendChild(
          el(
            "span",
            "row-subtitle",
            (cycle.nodes || []).length + " modules in strongly connected loop"
          )
        );
        btn.appendChild(main);
        btn.appendChild(el("span", "badge-tag is-critical", "Cycle"));
        btn.addEventListener("click", () => {
          if (cycle.nodes && cycle.nodes.length > 0) {
            navigateToModule(cycle.nodes[0], { fly: true });
          }
        });
        stack.appendChild(btn);
      }
      cycleSection.appendChild(stack);
    }
    panel.appendChild(cycleSection);

    const smells = state.health.smells || [];
    const smellSection = el("div", "section-block");
    smellSection.appendChild(
      el("h3", "section-heading", "Architectural Smells (" + smells.length + ")")
    );
    if (smells.length === 0) {
      smellSection.appendChild(el("p", "smell-desc", "No architectural smells detected."));
    } else {
      const stack = el("div", "list-stack");
      for (const smell of smells) {
        const card = el("div", "smell-row is-" + (smell.severity || "medium"));
        const head = el("div", "smell-head");
        head.appendChild(el("span", "smell-title", smell.title));
        const sevClass =
          smell.severity === "high"
            ? "is-critical"
            : smell.severity === "medium"
              ? "is-warning"
              : "is-info";
        head.appendChild(el("span", "badge-tag " + sevClass, smell.rule_id));
        card.appendChild(head);
        card.appendChild(el("p", "smell-desc", smell.description));
        if (smell.remediation) {
          card.appendChild(el("p", "smell-remedy", smell.remediation));
        }
        if (Array.isArray(smell.nodes) && smell.nodes.length > 0) {
          const chips = el("div", "chip-row");
          for (const nid of smell.nodes.slice(0, 6)) {
            const jump = el("button", "mono-chip", nid);
            jump.type = "button";
            jump.addEventListener("click", () => navigateToModule(nid, { fly: true }));
            chips.appendChild(jump);
          }
          card.appendChild(chips);
        }
        stack.appendChild(card);
      }
      smellSection.appendChild(stack);
    }
    panel.appendChild(smellSection);
  }

  function openInspectorForNode(node, requestedTab, targetLine) {
    const drawer = document.getElementById("inspector-drawer");
    const titleEl = document.getElementById("inspector-title");
    const pathEl = document.getElementById("inspector-path");
    const badgesEl = document.getElementById("inspector-badges");
    const actionsEl = document.getElementById("inspector-actions");
    const tabsEl = document.getElementById("inspector-tabs");

    if (!drawer || !titleEl || !pathEl || !badgesEl || !actionsEl || !tabsEl) {
      return;
    }

    drawer.classList.remove("hidden");
    actionsEl.classList.remove("hidden");
    tabsEl.classList.remove("hidden");

    titleEl.textContent = node.name || node.id;
    pathEl.textContent = node.path || node.id;

    clearNode(badgesEl);
    badgesEl.appendChild(el("span", "badge-tag is-info", node.layer || "shared"));
    badgesEl.appendChild(el("span", "badge-tag", node.language || "unknown"));

    const hs = node.health_status || "healthy";
    badgesEl.appendChild(el("span", "badge-tag " + getHealthBadgeClass(hs), hs));
    if (node.is_entrypoint || (node.routes && node.routes.length > 0)) {
      badgesEl.appendChild(el("span", "badge-tag is-info", "Entrypoint"));
    }
    if (node.has_db_access) {
      badgesEl.appendChild(el("span", "badge-tag is-healthy", "DB Access"));
    }

    syncActionButtons();
    renderOverviewTab(node);
    renderImpactTab(node);
    renderSymbolsTab(node);

    const nextTab = requestedTab || state.activeTab || "overview";
    switchInspectorTab(nextTab);
    if (nextTab === "source") {
      renderSourceTab(node, targetLine);
    } else {
      const srcPanel = document.getElementById("tab-panel-source");
      if (srcPanel) {
        clearNode(srcPanel);
        srcPanel.setAttribute("data-loaded-node", "");
      }
    }
  }

  function renderOverviewTab(node) {
    const panel = document.getElementById("tab-panel-overview");
    if (!panel) {
      return;
    }
    clearNode(panel);

    const metricsSection = el("div", "section-block");
    metricsSection.appendChild(el("h3", "section-heading", "Module Telemetry"));
    const grid = el("div", "metric-grid");
    const metrics = [
      ["Total LOC", String(node.loc || 0)],
      ["Complexity", String(node.complexity || 1)],
      ["Fan-In (Callers)", String(node.fan_in || 0)],
      ["Fan-Out (Deps)", String(node.fan_out || 0)],
      ["Code Lines", String(node.code_lines || 0)],
      ["Instability", Number(node.instability || 0).toFixed(2)],
    ];
    for (const pair of metrics) {
      const cell = el("div", "metric-cell");
      cell.appendChild(el("span", "metric-label", pair[0]));
      cell.appendChild(el("span", "metric-value", pair[1]));
      grid.appendChild(cell);
    }
    metricsSection.appendChild(grid);
    panel.appendChild(metricsSection);

    const relatedSmells = ((state.health && state.health.smells) || []).filter(
      (s) => Array.isArray(s.nodes) && s.nodes.includes(node.id)
    );
    const relatedCycles = ((state.health && state.health.cycles) || []).filter(
      (c) => Array.isArray(c.nodes) && c.nodes.includes(node.id)
    );

    const healthSection = el("div", "section-block");
    healthSection.appendChild(el("h3", "section-heading", "Architectural Findings"));
    if (relatedSmells.length === 0 && relatedCycles.length === 0) {
      healthSection.appendChild(
        el("p", "smell-desc", "No architectural smells or cycles affect this module.")
      );
    } else {
      const stack = el("div", "list-stack");
      for (const cycle of relatedCycles) {
        const row = el("div", "smell-row is-high");
        const head = el("div", "smell-head");
        head.appendChild(el("span", "smell-title", "Cycle Membership"));
        head.appendChild(el("span", "badge-tag is-critical", "Cycle"));
        row.appendChild(head);
        row.appendChild(
          el("p", "smell-desc", cycle.display_path || (cycle.nodes || []).join(" -> "))
        );
        stack.appendChild(row);
      }
      for (const smell of relatedSmells) {
        const row = el("div", "smell-row is-" + (smell.severity || "medium"));
        const head = el("div", "smell-head");
        head.appendChild(el("span", "smell-title", smell.title));
        head.appendChild(el("span", "badge-tag", smell.rule_id));
        row.appendChild(head);
        row.appendChild(el("p", "smell-desc", smell.description));
        if (smell.remediation) {
          row.appendChild(el("p", "smell-remedy", smell.remediation));
        }
        stack.appendChild(row);
      }
      healthSection.appendChild(stack);
    }
    panel.appendChild(healthSection);

    const extDeps = Array.isArray(node.external_deps) ? node.external_deps : [];
    const depsSection = el("div", "section-block");
    depsSection.appendChild(
      el("h3", "section-heading", "External Packages (" + extDeps.length + ")")
    );
    if (extDeps.length === 0) {
      depsSection.appendChild(
        el("p", "smell-desc", "No external third-party packages imported.")
      );
    } else {
      const chips = el("div", "chip-row");
      for (const dep of extDeps) {
        chips.appendChild(el("span", "mono-chip", dep));
      }
      depsSection.appendChild(chips);
    }
    panel.appendChild(depsSection);
  }

  async function renderImpactTab(node) {
    const panel = document.getElementById("tab-panel-impact");
    if (!panel || !node) {
      return;
    }
    panel.setAttribute("data-loaded-node", node.id);
    clearNode(panel);

    const loading = el("p", "smell-desc", "Computing multi-hop blast radius...");
    panel.appendChild(loading);

    const blast = await loadBlastRadius(node.id);
    if (state.selectedNodeId !== node.id) {
      return;
    }
    clearNode(panel);

    const summarySection = el("div", "section-block");
    summarySection.appendChild(el("h3", "section-heading", "Blast Radius Summary"));
    const grid = el("div", "metric-grid");
    const upCell = el("div", "metric-cell");
    upCell.appendChild(el("span", "metric-label", "Upstream Callers"));
    upCell.appendChild(el("span", "metric-value", String(blast.upstream_count || 0)));
    const downCell = el("div", "metric-cell");
    downCell.appendChild(el("span", "metric-label", "Downstream Deps"));
    downCell.appendChild(el("span", "metric-value", String(blast.downstream_count || 0)));
    grid.appendChild(upCell);
    grid.appendChild(downCell);
    summarySection.appendChild(grid);
    panel.appendChild(summarySection);

    function appendImpactList(title, items, directionClass, prefix) {
      const sec = el("div", "section-block");
      sec.appendChild(el("h3", "section-heading", title + " (" + items.length + ")"));
      if (items.length === 0) {
        sec.appendChild(el("p", "smell-desc", "None discovered within depth 5."));
      } else {
        const stack = el("div", "list-stack");
        for (const item of items) {
          const row = el("button", "row-button");
          row.type = "button";
          const main = el("div", "row-main");
          const targetNode = state.nodeById.get(item.node_id);
          main.appendChild(el("span", "row-title", item.node_id));
          if (targetNode) {
            main.appendChild(el("span", "row-subtitle", targetNode.layer + " module"));
          }
          row.appendChild(main);
          row.appendChild(el("span", "hop-badge " + directionClass, prefix + item.depth));
          row.addEventListener("click", () => navigateToModule(item.node_id, { fly: true }));
          stack.appendChild(row);
        }
        sec.appendChild(stack);
      }
      panel.appendChild(sec);
    }

    appendImpactList("Upstream Callers", blast.upstream || [], "is-upstream", "-");
    appendImpactList("Downstream Dependencies", blast.downstream || [], "is-downstream", "+");

    if (state.traceFlow && Array.isArray(state.traceFlow.paths) && state.traceFlow.paths.length > 0) {
      const traceSec = el("div", "section-block");
      traceSec.appendChild(
        el(
          "h3",
          "section-heading",
          "Request-to-Data Trace Paths (" + state.traceFlow.paths.length + ")"
        )
      );
      const stack = el("div", "list-stack");
      for (const pathList of state.traceFlow.paths) {
        const row = el("div", "smell-row");
        const chips = el("div", "chip-row");
        for (const stepId of pathList) {
          const stepBtn = el("button", "mono-chip", stepId);
          stepBtn.type = "button";
          stepBtn.addEventListener("click", () => navigateToModule(stepId, { fly: true }));
          chips.appendChild(stepBtn);
        }
        row.appendChild(chips);
        stack.appendChild(row);
      }
      traceSec.appendChild(stack);
      panel.appendChild(traceSec);
    }
  }

  function renderSymbolsTab(node) {
    const panel = document.getElementById("tab-panel-symbols");
    if (!panel) {
      return;
    }
    clearNode(panel);

    const routes = Array.isArray(node.routes) ? node.routes : [];
    const routesSection = el("div", "section-block");
    const routesHead = el("div", "section-heading-row");
    routesHead.appendChild(
      el("h3", "section-heading", "HTTP / Framework Routes (" + routes.length + ")")
    );
    if (routes.length > 0 || node.is_entrypoint || node.layer === "entrypoint") {
      const traceBtn = el("button", "inline-trace-btn", "Trace Flow");
      traceBtn.type = "button";
      traceBtn.addEventListener("click", () => {
        activateTraceFlow(node.id);
      });
      routesHead.appendChild(traceBtn);
    }
    routesSection.appendChild(routesHead);

    if (routes.length === 0) {
      routesSection.appendChild(el("p", "smell-desc", "No HTTP routes defined in this module."));
    } else {
      const stack = el("div", "list-stack");
      for (const r of routes) {
        const row = el("button", "row-button");
        row.type = "button";
        const main = el("div", "row-main");
        main.appendChild(el("span", "row-title", r.method + " " + r.path));
        const subText = (r.handler || "handler") + (r.framework ? " (" + r.framework + ")" : "");
        main.appendChild(el("span", "row-subtitle", subText));
        row.appendChild(main);
        row.appendChild(el("span", "badge-tag is-info", "L" + r.line));
        row.addEventListener("click", () => jumpToSourceLine(node, r.line));
        stack.appendChild(row);
      }
      routesSection.appendChild(stack);
    }
    panel.appendChild(routesSection);

    const symbols = Array.isArray(node.symbols) ? node.symbols : [];
    const symSection = el("div", "section-block");
    symSection.appendChild(el("h3", "section-heading", "Symbols (" + symbols.length + ")"));
    if (symbols.length === 0) {
      symSection.appendChild(el("p", "smell-desc", "No top-level symbols extracted."));
    } else {
      const stack = el("div", "list-stack");
      for (const sym of symbols) {
        const row = el("button", "row-button");
        row.type = "button";
        const main = el("div", "row-main");
        main.appendChild(el("span", "row-title", sym.signature || sym.name));
        const kindLabel = sym.kind + (sym.exported ? " exported" : " internal");
        main.appendChild(el("span", "row-subtitle", kindLabel));
        row.appendChild(main);
        row.appendChild(el("span", "badge-tag", "L" + sym.line));
        row.addEventListener("click", () => jumpToSourceLine(node, sym.line));
        stack.appendChild(row);
      }
      symSection.appendChild(stack);
    }
    panel.appendChild(symSection);
  }

  function appendHighlightedTokens(parentEl, lineText) {
    TOKEN_REGEX.lastIndex = 0;
    let lastIndex = 0;
    let match = TOKEN_REGEX.exec(lineText);

    while (match !== null) {
      if (match.index > lastIndex) {
        parentEl.appendChild(document.createTextNode(lineText.slice(lastIndex, match.index)));
      }
      const token = match[0];
      let cls = "";
      if (token.startsWith("//") || (token.startsWith("#") && !token.startsWith("#["))) {
        cls = "tok-com";
      } else if (
        token.startsWith('"') ||
        token.startsWith("'") ||
        token.startsWith("`")
      ) {
        cls = "tok-str";
      } else if (token.startsWith("@") || token.startsWith("#[")) {
        cls = "tok-dec";
      } else if (/^(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)$/.test(token)) {
        cls = "tok-num";
      } else if (KEYWORDS_SET.has(token)) {
        cls = "tok-kw";
      } else if (/^[A-Z][A-Za-z0-9_]*$/.test(token)) {
        cls = "tok-type";
      }

      if (cls) {
        parentEl.appendChild(el("span", cls, token));
      } else {
        parentEl.appendChild(document.createTextNode(token));
      }
      lastIndex = TOKEN_REGEX.lastIndex;
      match = TOKEN_REGEX.exec(lineText);
    }

    if (lastIndex < lineText.length) {
      parentEl.appendChild(document.createTextNode(lineText.slice(lastIndex)));
    }
  }

  async function renderSourceTab(node, targetLine) {
    const panel = document.getElementById("tab-panel-source");
    if (!panel || !node) {
      return;
    }
    panel.setAttribute("data-loaded-node", node.id);
    clearNode(panel);

    if (state.isStatic) {
      const notice = el("div", "source-notice");
      notice.textContent =
        "Source Preview is disabled in Static Snapshot mode. Run codeatlas serve on the repository to inspect live syntax-highlighted source files.";
      panel.appendChild(notice);
      return;
    }

    let fileData = state.fileCache.get(node.id);
    if (!fileData) {
      panel.appendChild(el("p", "smell-desc", "Loading source file..."));
      try {
        const resp = await fetch("/api/file?path=" + encodeURIComponent(node.id));
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status);
        }
        fileData = await resp.json();
        state.fileCache.set(node.id, fileData);
      } catch (_err) {
        if (state.selectedNodeId !== node.id) {
          return;
        }
        clearNode(panel);
        const errBox = el(
          "div",
          "source-notice",
          "Unable to load source preview for " + node.id
        );
        panel.appendChild(errBox);
        return;
      }
    }

    if (state.selectedNodeId !== node.id) {
      return;
    }

    clearNode(panel);
    const viewer = el("div", "source-viewer");
    const lines = Array.isArray(fileData.lines) ? fileData.lines : [];
    const maxRender = Math.min(lines.length, 2500);
    let targetEl = null;

    for (let i = 0; i < maxRender; i += 1) {
      const lineNumber = i + 1;
      const isTarget =
        targetLine !== null && targetLine !== undefined && lineNumber === Number(targetLine);
      const lineRow = el("div", "code-line" + (isTarget ? " is-target" : ""));
      lineRow.setAttribute("data-line", String(lineNumber));
      lineRow.appendChild(el("span", "code-ln", String(lineNumber)));
      const textSpan = el("span", "code-text");
      appendHighlightedTokens(textSpan, lines[i]);
      lineRow.appendChild(textSpan);
      viewer.appendChild(lineRow);
      if (isTarget) {
        targetEl = lineRow;
      }
    }

    if (lines.length > maxRender) {
      viewer.appendChild(
        el(
          "div",
          "source-notice",
          "Truncated preview at " + maxRender + " of " + lines.length + " lines."
        )
      );
    }

    panel.appendChild(viewer);

    if (targetEl) {
      window.requestAnimationFrame(() => {
        targetEl.scrollIntoView({ block: "center", behavior: "auto" });
      });
    }
  }

  function jumpToSourceLine(node, line) {
    switchInspectorTab("source");
    renderSourceTab(node, line);
  }

  /* Left Architecture Explorer Sidebar */
  function syncExplorerVisibility() {
    const sidebar = document.getElementById("explorer-sidebar");
    const toggleBtn = document.getElementById("explorer-toggle-btn");
    if (sidebar) {
      sidebar.classList.toggle("hidden", !state.explorerOpen);
    }
    if (toggleBtn) {
      toggleBtn.classList.toggle("is-active", state.explorerOpen);
      toggleBtn.setAttribute("aria-expanded", String(state.explorerOpen));
    }
  }

  function toggleExplorer(forceOpen) {
    const next = typeof forceOpen === "boolean" ? forceOpen : !state.explorerOpen;
    state.explorerOpen = next;
    syncExplorerVisibility();
    if (next) {
      renderExplorer();
    }
  }

  function syncPresetButtons() {
    const chips = document.querySelectorAll(".preset-chip");
    for (const chip of chips) {
      const active = chip.getAttribute("data-preset") === state.focusPreset;
      chip.classList.toggle("is-active", active);
      chip.setAttribute("aria-pressed", String(active));
    }
  }

  function setFocusPreset(preset) {
    if (!["all", "core", "issues", "routes"].includes(preset)) {
      return;
    }
    state.focusPreset = preset;
    if (preset === "core" && state.activeLayerFilter === "test") {
      state.activeLayerFilter = null;
    }
    syncPresetButtons();
    renderLayerPills();
    updateCanvasAndStats(true);
    if (state.explorerOpen) {
      renderExplorer();
    }
  }

  function switchExplorerTab(tabName) {
    const allowed = ["folders", "routes", "hotspots"];
    const next = allowed.includes(tabName) ? tabName : "folders";
    state.explorerTab = next;

    const tabs = document.querySelectorAll(".explorer-tab");
    for (const tab of tabs) {
      const active = tab.getAttribute("data-explorer-tab") === next;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    }
    for (const key of allowed) {
      const panel = document.getElementById("explorer-panel-" + key);
      if (panel) {
        panel.classList.toggle("hidden", key !== next);
      }
    }
    renderActiveExplorerPanel();
  }

  function getMethodBadgeClass(method) {
    const m = String(method || "GET").toUpperCase();
    if (m === "DELETE") {
      return "is-danger";
    }
    if (m === "POST" || m === "PUT" || m === "PATCH") {
      return "is-write";
    }
    return "";
  }

  function buildFolderTree(nodes, healthSets) {
    const root = {
      name: "",
      path: "",
      children: new Map(),
      files: [],
      totalFiles: 0,
      criticalCount: 0,
      warningCount: 0,
    };

    for (const node of nodes) {
      const norm = normalizePosixPath(node.path || node.id);
      const parts = norm.split("/").filter(Boolean);
      const hs = node.health_status || "healthy";
      const isCrit =
        healthSets.cycleSet.has(node.id) ||
        healthSets.violationSet.has(node.id) ||
        hs === "critical" ||
        hs === "cycle" ||
        hs === "violation";
      const isWarn =
        !isCrit &&
        (healthSets.godSet.has(node.id) ||
          hs === "warning" ||
          hs === "hotspot" ||
          hs === "god" ||
          hs === "dead");

      if (parts.length <= 1) {
        if (!root.children.has("(root)")) {
          root.children.set("(root)", {
            name: "(root)",
            path: "(root)",
            children: new Map(),
            files: [],
            totalFiles: 0,
            criticalCount: 0,
            warningCount: 0,
          });
        }
        const rootBucket = root.children.get("(root)");
        rootBucket.files.push({ node, isCrit, isWarn });
        rootBucket.totalFiles += 1;
        if (isCrit) rootBucket.criticalCount += 1;
        if (isWarn) rootBucket.warningCount += 1;
        continue;
      }

      let cursor = root;
      let accPath = "";
      for (let i = 0; i < parts.length - 1; i += 1) {
        const seg = parts[i];
        accPath = accPath ? accPath + "/" + seg : seg;
        if (!cursor.children.has(seg)) {
          cursor.children.set(seg, {
            name: seg,
            path: accPath,
            children: new Map(),
            files: [],
            totalFiles: 0,
            criticalCount: 0,
            warningCount: 0,
          });
        }
        cursor = cursor.children.get(seg);
        cursor.totalFiles += 1;
        if (isCrit) cursor.criticalCount += 1;
        if (isWarn) cursor.warningCount += 1;
      }
      cursor.files.push({ node, isCrit, isWarn });
    }

    return root;
  }

  function renderExplorerFoldersPanel(presetNodes, healthSets, queryLower) {
    const panel = document.getElementById("explorer-panel-folders");
    if (!panel) {
      return;
    }
    clearNode(panel);

    const matchingNodes = queryLower
      ? presetNodes.filter((n) => {
          const pathStr = normalizePosixPath(n.path || n.id).toLowerCase();
          const nameStr = String(n.name || "").toLowerCase();
          return pathStr.includes(queryLower) || nameStr.includes(queryLower);
        })
      : presetNodes;

    if (matchingNodes.length === 0) {
      panel.appendChild(el("div", "source-notice", "No folders or modules match the filter."));
      return;
    }

    const tree = buildFolderTree(matchingNodes, healthSets);
    const forceExpand = queryLower.length > 0;

    function renderFolderBranch(folderNode, depth) {
      const folderKeys = Array.from(folderNode.children.keys()).sort((a, b) => {
        if (a === "(root)") return 1;
        if (b === "(root)") return -1;
        return a.localeCompare(b);
      });

      const parentEl = document.createDocumentFragment();

      for (const key of folderKeys) {
        const sub = folderNode.children.get(key);
        const group = el("div", "tree-folder-group");
        const isExpanded =
          forceExpand ||
          state.expandedFolders.has(sub.path) ||
          (depth === 0 && folderKeys.length <= 6);
        const isDrilled =
          state.activeClusterKey &&
          normalizePosixPath(state.activeClusterKey) === sub.path;

        const row = el("div", "tree-folder-row" + (isDrilled ? " is-drilled" : ""));

        const chevBtn = el(
          "button",
          "tree-chevron-btn" + (isExpanded ? " is-expanded" : "")
        );
        chevBtn.type = "button";
        chevBtn.setAttribute("aria-label", (isExpanded ? "Collapse " : "Expand ") + sub.name);
        chevBtn.setAttribute("aria-expanded", String(isExpanded));
        chevBtn.appendChild(createChevronSvg());
        chevBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (state.expandedFolders.has(sub.path)) {
            state.expandedFolders.delete(sub.path);
          } else {
            state.expandedFolders.add(sub.path);
          }
          renderExplorerFoldersPanel(presetNodes, healthSets, queryLower);
        });
        row.appendChild(chevBtn);

        const folderBtn = el("button", "tree-folder-btn");
        folderBtn.type = "button";
        folderBtn.title = "Drill into " + sub.path;
        const left = el("span", "tree-item-left");
        left.appendChild(createFolderSvg());
        left.appendChild(el("span", "tree-folder-name", sub.name));
        folderBtn.appendChild(left);

        const meta = el("span", "tree-item-meta");
        if (sub.criticalCount > 0) {
          const dot = el("span", "issue-dot is-critical");
          dot.title = String(sub.criticalCount) + " critical issues";
          meta.appendChild(dot);
        } else if (sub.warningCount > 0) {
          const dot = el("span", "issue-dot is-warning");
          dot.title = String(sub.warningCount) + " warnings";
          meta.appendChild(dot);
        }
        meta.appendChild(el("span", "tree-count", String(sub.totalFiles)));
        folderBtn.appendChild(meta);

        folderBtn.addEventListener("click", () => {
          state.expandedFolders.add(sub.path);
          enterClusterDrilldown(sub.path, {
            kind: "folder",
            label: sub.path,
          });
        });

        row.appendChild(folderBtn);
        group.appendChild(row);

        if (isExpanded) {
          const childrenBox = el("div", "tree-children");
          childrenBox.appendChild(renderFolderBranch(sub, depth + 1));

          const sortedFiles = sub.files
            .slice()
            .sort((a, b) => (a.node.name || a.node.id).localeCompare(b.node.name || b.node.id));
          for (const entry of sortedFiles) {
            const n = entry.node;
            const fileBtn = el(
              "button",
              "tree-file-btn" + (state.selectedNodeId === n.id ? " is-selected" : "")
            );
            fileBtn.type = "button";
            fileBtn.title = n.path || n.id;

            const fLeft = el("span", "tree-item-left");
            const lDot = el("span", "layer-dot");
            lDot.setAttribute("data-layer", n.layer || "shared");
            lDot.setAttribute("aria-hidden", "true");
            fLeft.appendChild(lDot);
            fLeft.appendChild(el("span", "tree-file-name", n.name || n.id));
            fileBtn.appendChild(fLeft);

            const fMeta = el("span", "tree-item-meta");
            if (entry.isCrit) {
              fMeta.appendChild(el("span", "issue-dot is-critical"));
            } else if (entry.isWarn) {
              fMeta.appendChild(el("span", "issue-dot is-warning"));
            }
            fMeta.appendChild(el("span", "tree-count", String(n.loc || 0)));
            fileBtn.appendChild(fMeta);

            fileBtn.addEventListener("click", () => {
              navigateToModule(n.id, { fly: true, zoom: 1.15, tab: "overview" });
            });
            childrenBox.appendChild(fileBtn);
          }

          group.appendChild(childrenBox);
        }

        parentEl.appendChild(group);
      }

      return parentEl;
    }

    panel.appendChild(renderFolderBranch(tree, 0));
  }

  function renderExplorerRoutesPanel(routeEntries, queryLower) {
    const panel = document.getElementById("explorer-panel-routes");
    if (!panel) {
      return;
    }
    clearNode(panel);

    const matches = queryLower
      ? routeEntries.filter((entry) => entry.searchText.includes(queryLower))
      : routeEntries;

    if (matches.length === 0) {
      panel.appendChild(
        el("div", "source-notice", "No HTTP routes match the active preset or filter.")
      );
      return;
    }

    const stack = el("div", "list-stack");
    for (const entry of matches.slice(0, 250)) {
      const r = entry.route;
      const n = entry.node;
      const row = el("div", "explorer-route-row");

      const mainBtn = el("button", "explorer-route-btn");
      mainBtn.type = "button";
      const head = el("div", "explorer-route-head");
      head.appendChild(
        el("span", "method-badge " + getMethodBadgeClass(r.method), r.method || "GET")
      );
      head.appendChild(el("span", "explorer-route-path", r.path || "/"));
      mainBtn.appendChild(head);

      const subText = (r.handler ? "-> " + r.handler + " · " : "") + (n.path || n.id);
      mainBtn.appendChild(el("span", "explorer-route-sub", subText));
      mainBtn.addEventListener("click", () => {
        navigateToModule(n.id, {
          fly: true,
          zoom: 1.15,
          tab: "symbols",
          line: r.line,
        });
      });
      row.appendChild(mainBtn);

      const traceBtn = el("button", "explorer-trace-btn", "Trace");
      traceBtn.type = "button";
      traceBtn.title = "Trace downstream data flow from " + n.id;
      traceBtn.addEventListener("click", () => {
        navigateToModule(n.id, {
          fly: true,
          zoom: 1.1,
          tab: "symbols",
          line: r.line,
        });
        activateTraceFlow(n.id);
      });
      row.appendChild(traceBtn);

      stack.appendChild(row);
    }

    if (matches.length > 250) {
      stack.appendChild(
        el(
          "div",
          "source-notice",
          "Showing top 250 of " + matches.length + " routes. Refine filter to narrow results."
        )
      );
    }

    panel.appendChild(stack);
  }

  function renderExplorerHotspotsPanel(presetNodes, healthSets, queryLower) {
    const panel = document.getElementById("explorer-panel-hotspots");
    if (!panel) {
      return;
    }
    clearNode(panel);

    const presetSet = new Set(presetNodes.map((n) => n.id));
    const matchesText = (txt) => !queryLower || String(txt || "").toLowerCase().includes(queryLower);

    // 1. Cycles
    const cycles = ((state.health && state.health.cycles) || []).filter((c) => {
      const nodesInCycle = c.nodes || [];
      const anyInPreset = nodesInCycle.some((id) => presetSet.has(id));
      const hit = matchesText(c.display_path || nodesInCycle.join(" -> "));
      return anyInPreset && hit;
    });

    const cycleSec = el("div", "explorer-section");
    const cycleHead = el("div", "explorer-section-head");
    cycleHead.appendChild(el("h3", "section-heading", "Cycles (" + cycles.length + ")"));
    cycleSec.appendChild(cycleHead);
    if (cycles.length === 0) {
      cycleSec.appendChild(el("p", "smell-desc", "No matching circular dependencies."));
    } else {
      const stack = el("div", "list-stack");
      for (const c of cycles.slice(0, 30)) {
        const btn = el("button", "row-button");
        btn.type = "button";
        const main = el("div", "row-main");
        main.appendChild(
          el("span", "row-title", c.display_path || (c.nodes || []).join(" -> "))
        );
        main.appendChild(
          el("span", "row-subtitle", String((c.nodes || []).length) + " modules in cycle")
        );
        btn.appendChild(main);
        btn.appendChild(el("span", "badge-tag is-critical", "Cycle"));
        btn.addEventListener("click", () => {
          const targetId = (c.nodes || []).find((id) => state.nodeById.has(id));
          if (targetId) {
            navigateToModule(targetId, { fly: true, zoom: 1.15, tab: "overview" });
            activateBlastSpotlight(targetId, false);
          }
        });
        stack.appendChild(btn);
      }
      cycleSec.appendChild(stack);
    }
    panel.appendChild(cycleSec);

    // 2. High Violations
    const violations = ((state.health && state.health.smells) || [])
      .filter((s) => s.kind === "layer_violation")
      .filter((s) => {
        const anyInPreset = (s.nodes || []).some((id) => presetSet.has(id));
        const hit =
          matchesText(s.title) ||
          matchesText(s.description) ||
          (s.nodes || []).some((id) => matchesText(id));
        return anyInPreset && hit;
      })
      .sort((a, b) => {
        const sevRank = (v) => (v === "high" ? 0 : v === "medium" ? 1 : 2);
        return sevRank(a.severity) - sevRank(b.severity);
      });

    const violSec = el("div", "explorer-section");
    const violHead = el("div", "explorer-section-head");
    violHead.appendChild(
      el("h3", "section-heading", "Layer Violations (" + violations.length + ")")
    );
    violSec.appendChild(violHead);
    if (violations.length === 0) {
      violSec.appendChild(el("p", "smell-desc", "No matching layer violations."));
    } else {
      const stack = el("div", "list-stack");
      for (const v of violations.slice(0, 40)) {
        const btn = el("button", "row-button");
        btn.type = "button";
        const main = el("div", "row-main");
        const primaryId = (v.nodes && v.nodes[0]) || "";
        main.appendChild(el("span", "row-title", primaryId || v.title));
        main.appendChild(el("span", "row-subtitle", v.description || v.title));
        btn.appendChild(main);
        const sevCls =
          v.severity === "high"
            ? "is-critical"
            : v.severity === "medium"
              ? "is-warning"
              : "is-info";
        btn.appendChild(el("span", "badge-tag " + sevCls, v.severity || "warn"));
        btn.addEventListener("click", () => {
          const targetId = (v.nodes || []).find((id) => state.nodeById.has(id));
          if (targetId) {
            navigateToModule(targetId, { fly: true, zoom: 1.15, tab: "overview" });
            activateBlastSpotlight(targetId, false);
          }
        });
        stack.appendChild(btn);
      }
      violSec.appendChild(stack);
    }
    panel.appendChild(violSec);

    // 3. Top Hotspots
    const rankedHotspots = presetNodes
      .filter((n) => {
        const isGod =
          healthSets.godSet.has(n.id) ||
          n.health_status === "hotspot" ||
          n.health_status === "god" ||
          n.health_status === "critical" ||
          n.health_status === "warning" ||
          Number(n.complexity || 0) >= 15 ||
          Number(n.loc || 0) >= 280;
        return isGod && (matchesText(n.path || n.id) || matchesText(n.name));
      })
      .slice()
      .sort((a, b) => {
        const score = (n) => {
          let s = Number(n.complexity || 0) * 8 + Number(n.loc || 0) * 0.5 + (Number(n.fan_in || 0) + Number(n.fan_out || 0)) * 12;
          if (healthSets.godSet.has(n.id)) s += 500;
          if (healthSets.cycleSet.has(n.id)) s += 300;
          if (healthSets.violationSet.has(n.id)) s += 200;
          return s;
        };
        return score(b) - score(a);
      });

    const hotSec = el("div", "explorer-section");
    const hotHead = el("div", "explorer-section-head");
    hotHead.appendChild(
      el("h3", "section-heading", "Top Hotspots (" + rankedHotspots.length + ")")
    );
    hotSec.appendChild(hotHead);
    if (rankedHotspots.length === 0) {
      hotSec.appendChild(el("p", "smell-desc", "No matching complexity or God hotspots."));
    } else {
      const stack = el("div", "list-stack");
      for (const n of rankedHotspots.slice(0, 40)) {
        const btn = el("button", "row-button");
        btn.type = "button";
        const main = el("div", "row-main");
        main.appendChild(el("span", "row-title", n.name || n.id));
        main.appendChild(
          el(
            "span",
            "row-subtitle",
            (n.path || n.id) + " · " + String(n.loc || 0) + " LOC · CC " + String(n.complexity || 1)
          )
        );
        btn.appendChild(main);
        const hs = n.health_status || "warning";
        btn.appendChild(el("span", "badge-tag " + getHealthBadgeClass(hs), "CC " + String(n.complexity || 1)));
        btn.addEventListener("click", () => {
          navigateToModule(n.id, { fly: true, zoom: 1.15, tab: "overview" });
          activateBlastSpotlight(n.id, false);
        });
        stack.appendChild(btn);
      }
      hotSec.appendChild(stack);
    }
    panel.appendChild(hotSec);
  }

  function renderExplorer() {
    if (!state.graph) {
      return;
    }
    const presetGraph = getPresetFilteredGraph();
    const presetNodes = presetGraph.nodes || [];
    const healthSets = computeHealthSets(state.graph, state.health);

    const routeEntries = [];
    for (const n of presetNodes) {
      for (const r of n.routes || []) {
        routeEntries.push({
          route: r,
          node: n,
          searchText: (
            String(r.method || "") +
            " " +
            String(r.path || "") +
            " " +
            String(r.handler || "") +
            " " +
            String(n.path || n.id)
          ).toLowerCase(),
        });
      }
    }
    routeEntries.sort(
      (a, b) =>
        String(a.route.path || "").localeCompare(String(b.route.path || "")) ||
        String(a.route.method || "").localeCompare(String(b.route.method || ""))
    );

    const totalIssues =
      ((state.health && state.health.cycles) || []).length +
      ((state.health && state.health.smells) || []).filter((s) => s.kind === "layer_violation")
        .length +
      ((state.health && state.health.hotspots) || []).length;

    const totalEl = document.getElementById("explorer-total-count");
    const foldersCountEl = document.getElementById("explorer-count-folders");
    const routesCountEl = document.getElementById("explorer-count-routes");
    const hotspotsCountEl = document.getElementById("explorer-count-hotspots");
    if (totalEl) totalEl.textContent = String(presetNodes.length);
    if (foldersCountEl) foldersCountEl.textContent = String(presetNodes.length);
    if (routesCountEl) routesCountEl.textContent = String(routeEntries.length);
    if (hotspotsCountEl) hotspotsCountEl.textContent = String(totalIssues);

    renderActiveExplorerPanel(presetNodes, routeEntries, healthSets);
  }

  function renderActiveExplorerPanel(cachedNodes, cachedRoutes, cachedHealthSets) {
    if (!state.graph) {
      return;
    }
    const presetNodes = cachedNodes || getPresetFilteredGraph().nodes || [];
    const healthSets = cachedHealthSets || computeHealthSets(state.graph, state.health);
    const queryLower = (state.explorerQuery || "").trim().toLowerCase();

    if (state.explorerTab === "folders") {
      renderExplorerFoldersPanel(presetNodes, healthSets, queryLower);
    } else if (state.explorerTab === "routes") {
      let routeEntries = cachedRoutes;
      if (!routeEntries) {
        routeEntries = [];
        for (const n of presetNodes) {
          for (const r of n.routes || []) {
            routeEntries.push({
              route: r,
              node: n,
              searchText: (
                String(r.method || "") +
                " " +
                String(r.path || "") +
                " " +
                String(r.handler || "") +
                " " +
                String(n.path || n.id)
              ).toLowerCase(),
            });
          }
        }
        routeEntries.sort(
          (a, b) =>
            String(a.route.path || "").localeCompare(String(b.route.path || "")) ||
            String(a.route.method || "").localeCompare(String(b.route.method || ""))
        );
      }
      renderExplorerRoutesPanel(routeEntries, queryLower);
    } else {
      renderExplorerHotspotsPanel(presetNodes, healthSets, queryLower);
    }
  }

  /* Command Palette Fuzzy Search */
  function buildPaletteIndex() {
    const items = [];
    if (!state.graph) {
      state.paletteItems = items;
      return;
    }

    for (const node of state.graph.nodes || []) {
      items.push({
        kind: "Module",
        title: node.name || node.id,
        subtitle: node.path + " (" + node.layer + ")",
        searchText: (
          node.name +
          " " +
          node.path +
          " " +
          node.layer +
          " " +
          node.language
        ).toLowerCase(),
        nodeId: node.id,
        tab: "overview",
        line: null,
      });

      for (const route of node.routes || []) {
        items.push({
          kind: "Route",
          title: route.method + " " + route.path,
          subtitle: node.path + " line " + route.line,
          searchText: (
            route.method +
            " " +
            route.path +
            " " +
            (route.handler || "") +
            " " +
            node.path
          ).toLowerCase(),
          nodeId: node.id,
          tab: "symbols",
          line: route.line,
        });
      }

      for (const sym of node.symbols || []) {
        items.push({
          kind: "Symbol",
          title: sym.name,
          subtitle: sym.kind + " in " + node.path + " line " + sym.line,
          searchText: (sym.name + " " + sym.kind + " " + node.path).toLowerCase(),
          nodeId: node.id,
          tab: "source",
          line: sym.line,
        });
      }
    }

    for (const smell of (state.health && state.health.smells) || []) {
      const targetId =
        Array.isArray(smell.nodes) && smell.nodes.length > 0 ? smell.nodes[0] : null;
      items.push({
        kind: "Smell",
        title: smell.title,
        subtitle: smell.rule_id + " (" + smell.severity + ")",
        searchText: (
          smell.rule_id +
          " " +
          smell.title +
          " " +
          smell.description
        ).toLowerCase(),
        nodeId: targetId,
        openHealth: !targetId,
        tab: "overview",
        line: null,
      });
    }

    state.paletteItems = items;
  }

  function fuzzyScore(query, target) {
    if (!query) {
      return { score: 1, indices: [] };
    }
    const q = query.toLowerCase();
    const t = target.toLowerCase();
    const subIdx = t.indexOf(q);
    if (subIdx !== -1) {
      const indices = [];
      for (let i = 0; i < q.length; i += 1) {
        indices.push(subIdx + i);
      }
      return { score: 200 - subIdx + (subIdx === 0 ? 50 : 0), indices };
    }

    let qi = 0;
    const indices = [];
    let gaps = 0;
    let prevIdx = -1;
    for (let ti = 0; ti < t.length && qi < q.length; ti += 1) {
      if (t[ti] === q[qi]) {
        if (prevIdx !== -1) {
          gaps += ti - prevIdx - 1;
        }
        prevIdx = ti;
        indices.push(ti);
        qi += 1;
      }
    }
    if (qi < q.length) {
      return null;
    }
    return { score: Math.max(1, 90 - gaps), indices };
  }

  function renderHighlightedTitle(container, text, matchIndices) {
    if (!matchIndices || matchIndices.length === 0) {
      container.textContent = text;
      return;
    }
    const idxSet = new Set(matchIndices);
    let buffer = "";
    let inMatch = false;
    for (let i = 0; i < text.length; i += 1) {
      const matched = idxSet.has(i);
      if (matched !== inMatch && buffer.length > 0) {
        container.appendChild(
          inMatch ? el("span", "palette-match", buffer) : document.createTextNode(buffer)
        );
        buffer = "";
      }
      inMatch = matched;
      buffer += text[i];
    }
    if (buffer.length > 0) {
      container.appendChild(
        inMatch ? el("span", "palette-match", buffer) : document.createTextNode(buffer)
      );
    }
  }

  function renderPaletteResults(query) {
    const listEl = document.getElementById("palette-results");
    if (!listEl) {
      return;
    }
    clearNode(listEl);

    const trimmed = (query || "").trim();
    const scored = [];
    for (const item of state.paletteItems) {
      const titleHit = fuzzyScore(trimmed, item.title);
      const fullHit = titleHit || fuzzyScore(trimmed, item.searchText);
      if (fullHit) {
        scored.push({
          item,
          score:
            (titleHit ? titleHit.score + 60 : fullHit.score) +
            (item.kind === "Module" ? 10 : 0),
          titleIndices: titleHit ? titleHit.indices : [],
        });
      }
    }

    scored.sort((a, b) => b.score - a.score);
    state.paletteMatches = scored.slice(0, 36);
    state.paletteIndex = 0;

    if (state.paletteMatches.length === 0) {
      listEl.appendChild(
        el("div", "source-notice", "No matching modules, symbols, routes, or smells.")
      );
      return;
    }

    state.paletteMatches.forEach((entry, idx) => {
      const row = el("button", "palette-item" + (idx === 0 ? " is-selected" : ""));
      row.type = "button";
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(idx === 0));

      const left = el("div", "palette-item-left");
      const titleSpan = el("span", "palette-item-title");
      renderHighlightedTitle(titleSpan, entry.item.title, entry.titleIndices);
      left.appendChild(titleSpan);
      left.appendChild(el("span", "palette-item-sub", entry.item.subtitle));

      row.appendChild(left);
      row.appendChild(el("span", "badge-tag", entry.item.kind));

      row.addEventListener("click", () => executePaletteSelection(idx));
      listEl.appendChild(row);
    });
  }

  function updatePaletteSelection(nextIdx) {
    const listEl = document.getElementById("palette-results");
    if (!listEl || state.paletteMatches.length === 0) {
      return;
    }
    const maxIdx = state.paletteMatches.length - 1;
    state.paletteIndex = Math.max(0, Math.min(maxIdx, nextIdx));
    const items = listEl.querySelectorAll(".palette-item");
    items.forEach((itemEl, idx) => {
      const selected = idx === state.paletteIndex;
      itemEl.classList.toggle("is-selected", selected);
      itemEl.setAttribute("aria-selected", String(selected));
      if (selected) {
        itemEl.scrollIntoView({ block: "nearest" });
      }
    });
  }

  function executePaletteSelection(idx) {
    const entry = state.paletteMatches[idx !== undefined ? idx : state.paletteIndex];
    if (!entry) {
      return;
    }
    closePalette();
    const item = entry.item;
    if (item.openHealth) {
      openHealthDiagnostics();
      return;
    }
    if (item.nodeId) {
      navigateToModule(item.nodeId, {
        fly: true,
        zoom: 1.18,
        tab: item.tab || "overview",
        line: item.line,
      });
    }
  }

  function openPalette() {
    const backdrop = document.getElementById("palette-backdrop");
    const input = document.getElementById("palette-input");
    if (!backdrop || !input) {
      return;
    }
    backdrop.classList.remove("hidden");
    input.value = "";
    renderPaletteResults("");
    window.requestAnimationFrame(() => input.focus());
  }

  function closePalette() {
    const backdrop = document.getElementById("palette-backdrop");
    if (backdrop) {
      backdrop.classList.add("hidden");
    }
  }

  /* Export Studio (Live & Static Client Blob Generation) */
  function triggerBlobDownload(content, mimeType, filename) {
    const blob = content instanceof Blob ? content : new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function triggerDataUrlDownload(dataUrl, filename) {
    const link = document.createElement("a");
    link.href = dataUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  function sanitizeMermaidId(id) {
    return "n_" + String(id).replace(/[^A-Za-z0-9_]/g, "_");
  }

  function generateClientMermaid(graph) {
    const lines = ["flowchart LR"];
    const byLayer = new Map();
    for (const l of LAYER_ORDER) {
      byLayer.set(l, []);
    }
    for (const n of graph.nodes || []) {
      const layer = byLayer.has(n.layer) ? n.layer : "shared";
      byLayer.get(layer).push(n);
    }
    for (const layer of LAYER_ORDER) {
      const list = byLayer.get(layer);
      if (list.length === 0) {
        continue;
      }
      lines.push("  subgraph " + layer + "[" + (LAYER_LABELS[layer] || layer) + "]");
      for (const n of list) {
        lines.push('    ' + sanitizeMermaidId(n.id) + '["' + n.id.replace(/"/g, "'") + '"]');
      }
      lines.push("  end");
    }
    for (const e of graph.edges || []) {
      const arrow = e.is_cycle
        ? "-. cycle .->"
        : e.is_violation
          ? "-. violation .->"
          : "-->";
      lines.push(
        "  " + sanitizeMermaidId(e.source) + " " + arrow + " " + sanitizeMermaidId(e.target)
      );
    }
    return lines.join("\n") + "\n";
  }

  function renderExportMenu() {
    const menu = document.getElementById("export-menu");
    if (!menu) {
      return;
    }
    clearNode(menu);

    const slug = (state.repoName || "codeatlas")
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .toLowerCase();
    const items = [
      {
        title: "PNG Snapshot",
        tag: "Image",
        enabled: true,
        action: () => {
          if (!engine) {
            return;
          }
          const dataUrl = engine.exportPngDataUrl();
          triggerDataUrlDownload(dataUrl, slug + "-architecture.png");
          showToast("PNG Snapshot exported");
        },
      },
      {
        title: "ARCHITECTURE.md",
        tag: state.isStatic ? "Live only" : "Markdown",
        enabled: !state.isStatic,
        action: () => triggerServerExport("markdown", "ARCHITECTURE.md"),
      },
      {
        title: "llms.txt",
        tag: state.isStatic ? "Live only" : "AI Context",
        enabled: !state.isStatic,
        action: () => triggerServerExport("llms", "llms.txt"),
      },
      {
        title: "Mermaid Diagram",
        tag: ".mmd",
        enabled: true,
        action: () => {
          if (state.isStatic) {
            triggerBlobDownload(
              generateClientMermaid(state.graph),
              "text/plain;charset=utf-8",
              slug + "-architecture.mmd"
            );
            showToast("Mermaid diagram exported");
          } else {
            triggerServerExport("mermaid", slug + "-architecture.mmd");
          }
        },
      },
      {
        title: "JSON Graph",
        tag: ".json",
        enabled: true,
        action: () => {
          if (state.isStatic) {
            const body = JSON.stringify(
              { graph: state.graph, health: state.health },
              null,
              2
            );
            triggerBlobDownload(
              body,
              "application/json;charset=utf-8",
              slug + "-architecture.json"
            );
            showToast("JSON graph exported");
          } else {
            triggerServerExport("json", slug + "-architecture.json");
          }
        },
      },
      {
        title: "Standalone HTML",
        tag: state.isStatic ? "Live only" : ".html",
        enabled: !state.isStatic,
        action: () => triggerServerExport("html", slug + "-atlas.html"),
      },
    ];

    for (const item of items) {
      const btn = el("button", "export-item");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.disabled = !item.enabled;
      btn.appendChild(el("span", "export-item-title", item.title));
      btn.appendChild(el("span", "export-item-tag", item.tag));
      btn.addEventListener("click", () => {
        closeExportMenu();
        if (item.enabled) {
          item.action();
        }
      });
      menu.appendChild(btn);
    }
  }

  async function triggerServerExport(format, fallbackFilename) {
    try {
      const resp = await fetch("/api/export?format=" + encodeURIComponent(format));
      if (!resp.ok) {
        throw new Error("HTTP " + resp.status);
      }
      const blob = await resp.blob();
      triggerBlobDownload(blob, blob.type || "application/octet-stream", fallbackFilename);
      showToast("Exported " + fallbackFilename);
    } catch (_err) {
      showToast("Export failed for " + format);
    }
  }

  function closeExportMenu() {
    const menu = document.getElementById("export-menu");
    const trigger = document.getElementById("export-trigger");
    if (menu) {
      menu.classList.add("hidden");
    }
    if (trigger) {
      trigger.setAttribute("aria-expanded", "false");
    }
  }

  function toggleExportMenu() {
    const menu = document.getElementById("export-menu");
    const trigger = document.getElementById("export-trigger");
    if (!menu || !trigger) {
      return;
    }
    const willOpen = menu.classList.contains("hidden");
    if (willOpen) {
      renderExportMenu();
      menu.classList.remove("hidden");
      trigger.setAttribute("aria-expanded", "true");
    } else {
      closeExportMenu();
    }
  }

  function updateWatchBadge(watching, isStatic) {
    const badge = document.getElementById("watch-badge");
    const label = document.getElementById("watch-label");
    if (!badge || !label) {
      return;
    }
    badge.classList.remove("is-watching", "is-static");
    if (isStatic) {
      badge.classList.add("is-static");
      label.textContent = "Static Snapshot";
    } else if (watching) {
      badge.classList.add("is-watching");
      label.textContent = "Watching";
    } else {
      label.textContent = "Live";
    }
  }

  function ingestPayload(payload, changedFiles) {
    state.graph = payload.graph || {
      repo_name: "Repository",
      nodes: [],
      edges: [],
      clusters: [],
    };
    state.health = payload.health || {
      score: 100,
      grade: "A",
      cycles: [],
      smells: [],
      hotspots: [],
      dead_files: [],
    };
    state.repoName = payload.repo_name || state.graph.repo_name || "Repository";
    if (typeof payload.watch_enabled === "boolean") {
      state.watchEnabled = payload.watch_enabled;
    }

    const totalNodes = (state.graph.nodes || []).length;
    if (!state.hasInitializedViewDefaults) {
      state.hasInitializedViewDefaults = true;
      state.viewMode = totalNodes > 60 ? "overview" : "modules";
      state.explorerOpen = totalNodes > 60 && window.innerWidth >= 1180;
    }

    state.nodeById = new Map((state.graph.nodes || []).map((n) => [n.id, n]));
    state.blastCache.clear();
    state.traceCache.clear();
    state.fileCache.clear();

    const repoEl = document.getElementById("repo-name");
    if (repoEl) {
      repoEl.textContent = state.repoName;
    }

    updateWatchBadge(state.watchEnabled, state.isStatic);
    syncExplorerVisibility();
    syncPresetButtons();
    renderHealthRing();
    renderLayerPills();
    buildPaletteIndex();
    updateCanvasAndStats(!state.hasFittedOnce);
    if (state.explorerOpen) {
      renderExplorer();
    }

    if (Array.isArray(changedFiles) && changedFiles.length > 0 && engine) {
      engine.triggerFileRipples(changedFiles);
      showToast(
        "Updated " + changedFiles.length + (changedFiles.length === 1 ? " file" : " files")
      );
    }

    if (state.selectedNodeId && state.nodeById.has(state.selectedNodeId)) {
      openInspectorForNode(state.nodeById.get(state.selectedNodeId), state.activeTab, null);
    } else if (
      state.selectedClusterId &&
      state.currentClusterGraph &&
      state.currentClusterGraph.clusterLookup &&
      state.currentClusterGraph.clusterLookup.has(state.selectedClusterId)
    ) {
      openInspectorForCluster(
        state.currentClusterGraph.clusterLookup.get(state.selectedClusterId),
        { fly: false }
      );
    } else if (
      state.inspectorMode === "health" &&
      !document.getElementById("inspector-drawer").classList.contains("hidden")
    ) {
      renderHealthOverviewPanel();
    }
  }

  function updateTooltip(nodeId, pointerInfo) {
    const tooltip = document.getElementById("canvas-tooltip");
    if (!tooltip) {
      return;
    }
    if (!nodeId || !pointerInfo || !pointerInfo.node) {
      tooltip.classList.add("hidden");
      return;
    }

    const node = pointerInfo.node;
    clearNode(tooltip);
    if (node.isClusterNode) {
      tooltip.appendChild(el("div", "tooltip-title", node.clusterKey || node.name || node.id));
      const meta = el("div", "tooltip-meta");
      meta.appendChild(el("span", null, String(node.fileCount || 0) + " files"));
      meta.appendChild(el("span", null, String(node.loc || 0) + " LOC"));
      if (node.routesCount) {
        meta.appendChild(el("span", null, String(node.routesCount) + " routes"));
      }
      if (node.issueCount) {
        meta.appendChild(el("span", null, String(node.issueCount) + " issues"));
      }
      tooltip.appendChild(meta);
    } else {
      tooltip.appendChild(el("div", "tooltip-title", node.path || node.id));
      const meta = el("div", "tooltip-meta");
      meta.appendChild(el("span", null, node.layer));
      meta.appendChild(el("span", null, String(node.loc || 0) + " LOC"));
      meta.appendChild(el("span", null, "CC " + String(node.complexity || 1)));
      meta.appendChild(el("span", null, "In " + String(node.fan_in || 0)));
      meta.appendChild(el("span", null, "Out " + String(node.fan_out || 0)));
      tooltip.appendChild(meta);
    }

    tooltip.classList.remove("hidden");
    const tx = Math.max(12, Math.round(pointerInfo.x + 16));
    const ty = Math.max(12, Math.round(pointerInfo.y + 16));
    tooltip.style.setProperty("transform", "translate(" + tx + "px, " + ty + "px)");
  }

  function connectSse() {
    if (state.isStatic || typeof EventSource === "undefined") {
      return;
    }
    const es = new EventSource("/api/events");
    state.eventSource = es;

    es.addEventListener("graph_updated", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        ingestPayload(data, data.changed_files || []);
      } catch (_err) {
        /* ignore malformed SSE payload */
      }
    });

    es.addEventListener("watch_status", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (typeof data.watch_enabled === "boolean") {
          state.watchEnabled = data.watch_enabled;
          updateWatchBadge(state.watchEnabled, false);
        }
      } catch (_err) {
        /* ignore */
      }
    });
  }

  async function triggerRescan() {
    if (state.isStatic) {
      return;
    }
    const btn = document.getElementById("rescan-btn");
    if (btn) {
      btn.disabled = true;
      btn.classList.add("is-spinning");
    }
    try {
      const resp = await fetch("/api/rescan", { method: "POST" });
      if (!resp.ok) {
        throw new Error("HTTP " + resp.status);
      }
      const data = await resp.json();
      ingestPayload(data, []);
      showToast("Repository rescan complete");
    } catch (_err) {
      showToast("Rescan failed");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("is-spinning");
      }
    }
  }

  function bindUiEvents() {
    document.querySelectorAll(".layout-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        setLayoutMode(btn.getAttribute("data-layout"));
      });
    });

    document.querySelectorAll(".view-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        setViewMode(btn.getAttribute("data-view-mode"));
      });
    });

    const explorerToggleBtn = document.getElementById("explorer-toggle-btn");
    if (explorerToggleBtn) {
      explorerToggleBtn.addEventListener("click", () => toggleExplorer());
    }
    const explorerCloseBtn = document.getElementById("explorer-close-btn");
    if (explorerCloseBtn) {
      explorerCloseBtn.addEventListener("click", () => toggleExplorer(false));
    }

    document.querySelectorAll(".preset-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        setFocusPreset(btn.getAttribute("data-preset"));
      });
    });

    document.querySelectorAll(".explorer-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        switchExplorerTab(tab.getAttribute("data-explorer-tab"));
      });
    });

    const explorerFilterInput = document.getElementById("explorer-filter-input");
    if (explorerFilterInput) {
      explorerFilterInput.addEventListener("input", () => {
        state.explorerQuery = explorerFilterInput.value;
        renderActiveExplorerPanel();
      });
    }

    const breadcrumbAllBtn = document.getElementById("breadcrumb-all-btn");
    const breadcrumbBackBtn = document.getElementById("breadcrumb-back-btn");
    const breadcrumbNeighborsBtn = document.getElementById("breadcrumb-neighbors-btn");
    if (breadcrumbAllBtn) {
      breadcrumbAllBtn.addEventListener("click", exitDrilldownToOverview);
    }
    if (breadcrumbBackBtn) {
      breadcrumbBackBtn.addEventListener("click", exitDrilldownToOverview);
    }
    if (breadcrumbNeighborsBtn) {
      breadcrumbNeighborsBtn.addEventListener("click", () => {
        state.drillIncludeNeighbors = !state.drillIncludeNeighbors;
        updateCanvasAndStats(true);
      });
    }

    const toggleTestsBtn = document.getElementById("toggle-tests-btn");
    if (toggleTestsBtn) {
      toggleTestsBtn.addEventListener("click", () => {
        state.showTests = !state.showTests;
        if (!state.showTests && state.activeLayerFilter === "test") {
          state.activeLayerFilter = null;
          renderLayerPills();
        }
        syncToggleButtons();
        updateCanvasAndStats(false);
      });
    }

    const toggleCyclesBtn = document.getElementById("toggle-cycles-btn");
    if (toggleCyclesBtn) {
      toggleCyclesBtn.addEventListener("click", () => {
        state.showCyclesOnly = !state.showCyclesOnly;
        syncToggleButtons();
        if (engine) {
          engine.setShowCyclesOnly(state.showCyclesOnly);
        }
      });
    }

    const toggleParticlesBtn = document.getElementById("toggle-particles-btn");
    if (toggleParticlesBtn) {
      toggleParticlesBtn.addEventListener("click", () => {
        state.showParticles = !state.showParticles;
        syncToggleButtons();
        if (engine) {
          engine.setShowParticles(state.showParticles);
        }
      });
    }

    const themeBtn = document.getElementById("theme-toggle-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", toggleTheme);
    }

    const rescanBtn = document.getElementById("rescan-btn");
    if (rescanBtn) {
      rescanBtn.addEventListener("click", triggerRescan);
    }

    const healthBtn = document.getElementById("health-ring-btn");
    if (healthBtn) {
      healthBtn.addEventListener("click", openHealthDiagnostics);
    }

    const searchBtn = document.getElementById("search-trigger");
    if (searchBtn) {
      searchBtn.addEventListener("click", openPalette);
    }

    const exportTrigger = document.getElementById("export-trigger");
    if (exportTrigger) {
      exportTrigger.addEventListener("click", (ev) => {
        ev.stopPropagation();
        toggleExportMenu();
      });
    }

    document.addEventListener("click", (ev) => {
      const wrap = document.getElementById("export-dropdown-wrap");
      if (wrap && !wrap.contains(ev.target)) {
        closeExportMenu();
      }
    });

    const zoomInBtn = document.getElementById("zoom-in-btn");
    const zoomOutBtn = document.getElementById("zoom-out-btn");
    const fitBtn = document.getElementById("fit-view-btn");
    if (zoomInBtn) {
      zoomInBtn.addEventListener("click", () => engine && engine.zoomBy(1.25));
    }
    if (zoomOutBtn) {
      zoomOutBtn.addEventListener("click", () => engine && engine.zoomBy(0.8));
    }
    if (fitBtn) {
      fitBtn.addEventListener("click", () => engine && engine.fitToView(78, 450));
    }

    const resetFiltersBtn = document.getElementById("reset-filters-btn");
    if (resetFiltersBtn) {
      resetFiltersBtn.addEventListener("click", () => {
        state.activeLayerFilter = null;
        state.focusPreset = "all";
        state.activeClusterKey = null;
        state.drillIncludeNeighbors = false;
        state.showTests = true;
        state.showCyclesOnly = false;
        syncToggleButtons();
        syncPresetButtons();
        renderLayerPills();
        if (engine) {
          engine.setShowCyclesOnly(false);
        }
        updateCanvasAndStats(true);
        if (state.explorerOpen) {
          renderExplorer();
        }
      });
    }

    const bannerClearBtn = document.getElementById("mode-banner-clear");
    if (bannerClearBtn) {
      bannerClearBtn.addEventListener("click", clearSpotlightAndTrace);
    }

    const closeBtn = document.getElementById("inspector-close-btn");
    if (closeBtn) {
      closeBtn.addEventListener("click", closeInspector);
    }

    document.querySelectorAll(".drawer-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        const nextTab = tab.getAttribute("data-tab");
        switchInspectorTab(nextTab);
        if (
          nextTab === "source" &&
          state.selectedNodeId &&
          state.nodeById.has(state.selectedNodeId)
        ) {
          const srcPanel = document.getElementById("tab-panel-source");
          if (
            !srcPanel ||
            srcPanel.getAttribute("data-loaded-node") !== state.selectedNodeId
          ) {
            renderSourceTab(state.nodeById.get(state.selectedNodeId), null);
          }
        }
      });
    });

    const actionDrillBtn = document.getElementById("action-drill-btn");
    if (actionDrillBtn) {
      actionDrillBtn.addEventListener("click", () => {
        if (
          state.selectedClusterId &&
          state.currentClusterGraph &&
          state.currentClusterGraph.clusterLookup
        ) {
          const cNode = state.currentClusterGraph.clusterLookup.get(state.selectedClusterId);
          if (cNode) {
            enterClusterDrilldown(cNode.clusterKey, {
              kind: "cluster",
              label: cNode.name || cNode.clusterKey,
            });
          }
        }
      });
    }

    const actionBlastBtn = document.getElementById("action-blast-btn");
    if (actionBlastBtn) {
      actionBlastBtn.addEventListener("click", () => {
        const activeId =
          state.inspectorMode === "cluster"
            ? state.selectedClusterId
            : state.selectedNodeId;
        if (!activeId) {
          return;
        }
        if (state.spotlight && state.spotlight.targetId === activeId) {
          clearSpotlightAndTrace();
        } else {
          activateBlastSpotlight(activeId, false);
        }
      });
    }

    const actionTraceBtn = document.getElementById("action-trace-btn");
    if (actionTraceBtn) {
      actionTraceBtn.addEventListener("click", () => {
        const activeId =
          state.inspectorMode === "cluster"
            ? state.selectedClusterId
            : state.selectedNodeId;
        if (!activeId) {
          return;
        }
        if (state.traceFlow && state.traceFlow.entrypoint_id === activeId) {
          clearSpotlightAndTrace();
        } else {
          activateTraceFlow(activeId);
        }
      });
    }

    const actionFocusBtn = document.getElementById("action-focus-btn");
    if (actionFocusBtn) {
      actionFocusBtn.addEventListener("click", () => {
        const activeId =
          state.inspectorMode === "cluster"
            ? state.selectedClusterId
            : state.selectedNodeId;
        if (activeId && engine) {
          engine.flyTo(activeId, 1.2);
        }
      });
    }

    const canvasEl = document.getElementById("atlas-canvas");
    if (canvasEl) {
      canvasEl.addEventListener("dblclick", (ev) => {
        if (!engine) {
          return;
        }
        const rect = canvasEl.getBoundingClientRect();
        const sx = ev.clientX - rect.left;
        const sy = ev.clientY - rect.top;
        const hit = engine.hitTestScreen(sx, sy);
        if (hit && hit.isClusterNode && hit.clusterKey) {
          enterClusterDrilldown(hit.clusterKey, {
            kind: "cluster",
            label: hit.name || hit.clusterKey,
          });
        }
      });
    }

    const paletteBackdrop = document.getElementById("palette-backdrop");
    const paletteInput = document.getElementById("palette-input");
    if (paletteBackdrop) {
      paletteBackdrop.addEventListener("click", (ev) => {
        if (ev.target === paletteBackdrop) {
          closePalette();
        }
      });
    }
    if (paletteInput) {
      paletteInput.addEventListener("input", () => {
        renderPaletteResults(paletteInput.value);
      });
      paletteInput.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowDown") {
          ev.preventDefault();
          updatePaletteSelection(state.paletteIndex + 1);
        } else if (ev.key === "ArrowUp") {
          ev.preventDefault();
          updatePaletteSelection(state.paletteIndex - 1);
        } else if (ev.key === "Enter") {
          ev.preventDefault();
          executePaletteSelection(state.paletteIndex);
        } else if (ev.key === "Escape") {
          ev.preventDefault();
          closePalette();
        }
      });
    }

    document.addEventListener("keydown", (ev) => {
      const paletteOpen = !document
        .getElementById("palette-backdrop")
        .classList.contains("hidden");
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        if (paletteOpen) {
          closePalette();
        } else {
          openPalette();
        }
        return;
      }

      const tag = ev.target && ev.target.tagName ? ev.target.tagName.toLowerCase() : "";
      const isTyping = tag === "input" || tag === "textarea" || tag === "select";

      if (ev.key === "Escape") {
        if (paletteOpen) {
          closePalette();
          return;
        }
        const exportMenu = document.getElementById("export-menu");
        if (exportMenu && !exportMenu.classList.contains("hidden")) {
          closeExportMenu();
          return;
        }
        if (state.spotlight || state.traceFlow) {
          clearSpotlightAndTrace();
          return;
        }
        closeInspector();
        return;
      }

      if (isTyping || paletteOpen || ev.ctrlKey || ev.metaKey || ev.altKey) {
        return;
      }

      if (ev.key === "/") {
        ev.preventDefault();
        openPalette();
      } else if (ev.key === "1") {
        setLayoutMode("layers");
      } else if (ev.key === "2") {
        setLayoutMode("constellation");
      } else if (ev.key === "3") {
        setLayoutMode("blast");
      } else if (ev.key === "4") {
        setLayoutMode("health");
      } else if (ev.key === "v" || ev.key === "V") {
        setViewMode(state.viewMode === "overview" && !state.activeClusterKey ? "modules" : "overview");
      } else if (ev.key === "e" || ev.key === "E") {
        toggleExplorer();
      } else if (ev.key === "f" || ev.key === "F") {
        if (engine) {
          engine.fitToView(78, 450);
        }
      } else if (ev.key === "t" || ev.key === "T") {
        toggleTheme();
      }
    });
  }

  async function boot() {
    applyTheme(getSavedOrDefaultTheme());

    const canvasEl = document.getElementById("atlas-canvas");
    const containerEl = document.getElementById("canvas-stage");
    const minimapEl = document.getElementById("minimap-canvas");
    const zoomLabel = document.getElementById("minimap-zoom-label");

    engine = new CanvasEngine({
      canvas: canvasEl,
      container: containerEl,
      minimapCanvas: minimapEl,
      callbacks: {
        onSelectNode: (nodeId) => {
          if (nodeId) {
            selectAndInspectNode(nodeId, { fly: false });
          } else {
            closeInspector();
          }
        },
        onDoubleClickNode: (nodeId) => {
          if (
            nodeId &&
            String(nodeId).startsWith("cluster:") &&
            state.currentClusterGraph &&
            state.currentClusterGraph.clusterLookup &&
            state.currentClusterGraph.clusterLookup.has(nodeId)
          ) {
            const cNode = state.currentClusterGraph.clusterLookup.get(nodeId);
            enterClusterDrilldown(cNode.clusterKey, {
              kind: "cluster",
              label: cNode.name || cNode.clusterKey,
            });
          }
        },
        onHoverNode: (nodeId, pointerInfo) => {
          updateTooltip(nodeId, pointerInfo);
        },
        onZoomChange: (zoom) => {
          if (zoomLabel) {
            zoomLabel.textContent = String(Math.round(zoom * 100)) + "%";
          }
        },
      },
    });
    engine.setTheme(state.theme);

    bindUiEvents();

    const bootstrapEl = document.getElementById("codeatlas-bootstrap");
    if (bootstrapEl && bootstrapEl.textContent && bootstrapEl.textContent.trim().length > 0) {
      state.isStatic = true;
      const rescanBtn = document.getElementById("rescan-btn");
      if (rescanBtn) {
        rescanBtn.disabled = true;
        rescanBtn.title = "Rescan unavailable in static snapshot";
      }
      try {
        const parsed = JSON.parse(bootstrapEl.textContent);
        ingestPayload(parsed, []);
      } catch (_err) {
        showToast("Invalid embedded snapshot data");
      }
      return;
    }

    state.isStatic = false;
    try {
      const resp = await fetch("/api/graph");
      if (!resp.ok) {
        throw new Error("HTTP " + resp.status);
      }
      const data = await resp.json();
      ingestPayload(data, []);
      connectSse();
    } catch (_err) {
      updateWatchBadge(false, false);
      showToast("Failed to connect to CodeAtlas API");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
