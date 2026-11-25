/* CodeAtlas Deterministic Graph Layout Generators & Client Graph Algorithms */
(function (rootScope) {
  "use strict";

  const LAYER_ORDER = [
    "frontend",
    "entrypoint",
    "service",
    "data",
    "shared",
    "config",
    "test",
  ];

  const LAYER_LABELS = {
    frontend: "Frontend",
    entrypoint: "Entrypoint",
    service: "Service",
    data: "Data",
    shared: "Shared",
    config: "Config",
    test: "Test",
  };

  function compareStrings(a, b) {
    if (a === b) {
      return 0;
    }
    return a < b ? -1 : 1;
  }

  function hashString(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i += 1) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function createRng(seed) {
    let state = (seed >>> 0) || 0x9e3779b9;
    return function nextRandom() {
      state = (state + 0x6d2b79f5) >>> 0;
      let t = Math.imul(state ^ (state >>> 15), 1 | state);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function computeNodeRadius(node, emphasizeHealth) {
    if (node && node.isClusterNode) {
      const fileFactor = clamp(Number(node.fileCount || 1) / 45, 0, 1);
      const locFactor = clamp(Number(node.loc || 0) / 5000, 0, 1);
      const issueFactor = emphasizeHealth ? clamp(Number(node.issueCount || 0) / 8, 0, 1) : 0;
      const weight = emphasizeHealth
        ? fileFactor * 0.35 + locFactor * 0.35 + issueFactor * 0.3
        : fileFactor * 0.55 + locFactor * 0.45;
      return Math.round(44 + weight * 12);
    }
    const loc = Number(node.loc || 0);
    const complexity = Number(node.complexity || 1);
    const fanIn = Number(node.fan_in || 0);
    const fanOut = Number(node.fan_out || 0);
    if (emphasizeHealth) {
      const locFactor = clamp(loc / 420, 0, 1);
      const cpxFactor = clamp(complexity / 35, 0, 1);
      const degFactor = clamp((fanIn + fanOut) / 18, 0, 1);
      return Math.round(16 + (locFactor * 0.45 + cpxFactor * 0.4 + degFactor * 0.15) * 26);
    }
    const base = 16 + clamp(loc / 480, 0, 1) * 8 + clamp((fanIn + fanOut) / 20, 0, 1) * 7;
    return Math.round(base);
  }

  function collectHealthSets(graph, health) {
    const edges = Array.isArray(graph && graph.edges) ? graph.edges : [];
    const cycleNodes = new Set();
    const violationNodes = new Set();
    const godNodes = new Set((health && health.hotspots) || []);
    const deadNodes = new Set((health && health.dead_files) || []);

    for (const e of edges) {
      if (e.is_cycle) {
        cycleNodes.add(e.source);
        cycleNodes.add(e.target);
      }
      if (e.is_violation) {
        violationNodes.add(e.source);
        violationNodes.add(e.target);
      }
    }

    for (const c of (health && health.cycles) || []) {
      for (const nid of c.nodes || []) {
        cycleNodes.add(nid);
      }
    }

    for (const smell of (health && health.smells) || []) {
      for (const nid of smell.nodes || []) {
        if (smell.kind === "circular_dependency") {
          cycleNodes.add(nid);
        } else if (smell.kind === "layer_violation") {
          violationNodes.add(nid);
        } else if (smell.kind === "god_module") {
          godNodes.add(nid);
        } else if (smell.kind === "dead_code") {
          deadNodes.add(nid);
        }
      }
    }

    return { cycleNodes, violationNodes, godNodes, deadNodes };
  }

  function normalizePosixDir(node) {
    const rawDir = typeof node.directory === "string" ? node.directory.trim() : "";
    const rawPath = typeof (node.path || node.id) === "string" ? (node.path || node.id).trim() : "";
    let dir = rawDir.replace(/\\/g, "/").replace(/^\.\/+/, "").replace(/^\/+|\/+$/g, "");
    if (!dir && rawPath) {
      const cleaned = rawPath.replace(/\\/g, "/").replace(/^\.\/+/, "").replace(/^\/+|\/+$/g, "");
      const idx = cleaned.lastIndexOf("/");
      dir = idx > 0 ? cleaned.slice(0, idx) : "";
    }
    if (!dir || dir === "." || dir === "(root)") {
      return [];
    }
    return dir.split("/").filter((seg) => seg && seg !== ".");
  }

  const DEEP_DOMAIN_PREFIXES = new Set([
    "app/Http",
    "app/Console",
    "app/Actions",
    "app/Domain",
    "public/js",
    "public/css",
    "public/assets",
  ]);

  const CONTAINER_ROOTS = new Set(["src", "lib", "packages", "apps", "internal", "modules"]);

  function deriveInitialClusterKey(parts, expandedContainerRoots) {
    if (!parts || parts.length === 0) {
      return "(root)";
    }
    if (parts.length === 1) {
      return parts[0];
    }
    const depth2 = parts[0] + "/" + parts[1];
    if (parts.length >= 3) {
      if (DEEP_DOMAIN_PREFIXES.has(depth2) || (CONTAINER_ROOTS.has(parts[0]) && expandedContainerRoots.has(depth2))) {
        return depth2 + "/" + parts[2];
      }
    }
    return depth2;
  }

  function pickModeByCountAndLoc(items, keyFn, fallbackValue) {
    const stats = new Map();
    for (const item of items) {
      const k = keyFn(item) || fallbackValue;
      const cur = stats.get(k) || { count: 0, loc: 0 };
      cur.count += 1;
      cur.loc += Number(item.loc || 0);
      stats.set(k, cur);
    }
    let bestKey = fallbackValue;
    let bestCount = -1;
    let bestLoc = -1;
    const keys = Array.from(stats.keys()).sort(compareStrings);
    for (const k of keys) {
      const s = stats.get(k);
      if (s.count > bestCount || (s.count === bestCount && s.loc > bestLoc)) {
        bestKey = k;
        bestCount = s.count;
        bestLoc = s.loc;
      }
    }
    return bestKey;
  }

  /**
   * Aggregate a module graph into architectural directory/domain cluster super-nodes
   * and weighted cross-cluster edges for readable macro overviews on large repos.
   */
  function buildClusterGraph(graph, health, opts) {
    const options = opts || {};
    const maxClusters = Math.max(8, Number(options.maxClusters || 28));
    const targetClusters = Math.max(6, Math.min(maxClusters, Number(options.targetClusters || 24)));
    const rawNodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const rawEdges = Array.isArray(graph && graph.edges) ? graph.edges : [];

    const clusterMembers = new Map();
    const clusterLookup = new Map();
    if (rawNodes.length === 0) {
      return { nodes: [], edges: [], clusterMembers, clusterLookup };
    }

    const sortedNodes = rawNodes.slice().sort((a, b) => compareStrings(a.id, b.id));
    const nodePartsMap = new Map();
    const depth2Stats = new Map();
    for (const node of sortedNodes) {
      const parts = normalizePosixDir(node);
      nodePartsMap.set(node.id, parts);
      if (parts.length >= 3 && CONTAINER_ROOTS.has(parts[0])) {
        const d2 = parts[0] + "/" + parts[1];
        let entry = depth2Stats.get(d2);
        if (!entry) {
          entry = { count: 0, subdirs: new Set() };
          depth2Stats.set(d2, entry);
        }
        entry.count += 1;
        entry.subdirs.add(parts[2]);
      }
    }

    const expandedContainerRoots = new Set();
    for (const [d2, info] of depth2Stats.entries()) {
      if (info.subdirs.size >= 2 && (info.count >= 10 || info.count >= sortedNodes.length * 0.25)) {
        expandedContainerRoots.add(d2);
      }
    }

    const grouped = new Map();
    for (const node of sortedNodes) {
      const key = deriveInitialClusterKey(nodePartsMap.get(node.id), expandedContainerRoots);
      if (!grouped.has(key)) {
        grouped.set(key, []);
      }
      grouped.get(key).push(node);
    }

    // If > maxClusters, merge tiny sibling folders under top-level prefix down to ~targetClusters
    if (grouped.size > maxClusters) {
      while (grouped.size > targetClusters) {
        const byTopPrefix = new Map();
        for (const [key, members] of grouped.entries()) {
          if (key === "(root)") {
            continue;
          }
          const slashIdx = key.indexOf("/");
          const top = slashIdx >= 0 ? key.slice(0, slashIdx) : key;
          if (!byTopPrefix.has(top)) {
            byTopPrefix.set(top, []);
          }
          byTopPrefix.get(top).push({ key, count: members.length });
        }

        let bestPair = null;
        const tops = Array.from(byTopPrefix.keys()).sort(compareStrings);
        for (const top of tops) {
          const siblings = byTopPrefix.get(top);
          if (siblings.length < 2) {
            continue;
          }
          siblings.sort((a, b) => (a.count - b.count) || compareStrings(a.key, b.key));
          const mergedCount = siblings[0].count + siblings[1].count;
          if (
            !bestPair ||
            mergedCount < bestPair.mergedCount ||
            (mergedCount === bestPair.mergedCount && compareStrings(top, bestPair.top) < 0)
          ) {
            bestPair = {
              top,
              firstKey: siblings[0].key,
              secondKey: siblings[1].key,
              mergedCount,
            };
          }
        }

        if (!bestPair) {
          break;
        }

        const mergedKey = bestPair.top;
        const listA = grouped.get(bestPair.firstKey) || [];
        const listB = grouped.get(bestPair.secondKey) || [];
        grouped.delete(bestPair.firstKey);
        grouped.delete(bestPair.secondKey);
        const existing = grouped.get(mergedKey) || [];
        grouped.set(mergedKey, existing.concat(listA, listB));
      }
    }

    const { cycleNodes, violationNodes, godNodes, deadNodes } = collectHealthSets(graph, health);
    const nodeToClusterId = new Map();
    const sortedKeys = Array.from(grouped.keys()).sort(compareStrings);

    for (const key of sortedKeys) {
      const members = grouped.get(key).slice().sort((a, b) => compareStrings(a.id, b.id));
      const clusterId = "cluster:" + key;
      const memberSet = new Set();
      for (const m of members) {
        memberSet.add(m.id);
        nodeToClusterId.set(m.id, clusterId);
      }
      clusterMembers.set(clusterId, memberSet);
    }

    const extInCounts = new Map();
    const extOutCounts = new Map();
    const internalEdgeCounts = new Map();
    const aggEdgesMap = new Map();

    for (const key of sortedKeys) {
      const cid = "cluster:" + key;
      extInCounts.set(cid, 0);
      extOutCounts.set(cid, 0);
      internalEdgeCounts.set(cid, 0);
    }

    for (const edge of rawEdges) {
      const srcCid = nodeToClusterId.get(edge.source);
      const dstCid = nodeToClusterId.get(edge.target);
      if (!srcCid || !dstCid) {
        continue;
      }
      if (srcCid === dstCid) {
        internalEdgeCounts.set(srcCid, (internalEdgeCounts.get(srcCid) || 0) + 1);
        continue;
      }
      extOutCounts.set(srcCid, (extOutCounts.get(srcCid) || 0) + 1);
      extInCounts.set(dstCid, (extInCounts.get(dstCid) || 0) + 1);

      const pairKey = srcCid + "->" + dstCid;
      let agg = aggEdgesMap.get(pairKey);
      if (!agg) {
        agg = {
          source: srcCid,
          target: dstCid,
          edgeCount: 0,
          symbolCounts: new Map(),
          kind: "cluster",
          is_cycle: false,
          is_violation: false,
          isAggregatedEdge: true,
        };
        aggEdgesMap.set(pairKey, agg);
      }
      agg.edgeCount += 1;
      if (edge.is_cycle) {
        agg.is_cycle = true;
      }
      if (edge.is_violation) {
        agg.is_violation = true;
      }
      if (Array.isArray(edge.symbols)) {
        for (const sym of edge.symbols) {
          if (sym) {
            agg.symbolCounts.set(sym, (agg.symbolCounts.get(sym) || 0) + 1);
          }
        }
      }
    }

    const clusterNodes = [];
    for (const key of sortedKeys) {
      const clusterId = "cluster:" + key;
      const members = grouped.get(key).slice().sort((a, b) => compareStrings(a.id, b.id));
      let loc = 0;
      let complexity = 0;
      let routesCount = 0;
      let criticalCount = 0;
      let warningCount = 0;
      let hasDbAccess = false;
      let isEntrypoint = false;
      const layerBreakdown = {};

      for (const m of members) {
        loc += Number(m.loc || 0);
        complexity += Number(m.complexity || 1);
        const mRoutes = Array.isArray(m.routes) ? m.routes.length : 0;
        routesCount += mRoutes;
        if (m.has_db_access) {
          hasDbAccess = true;
        }
        if (m.is_entrypoint || mRoutes > 0) {
          isEntrypoint = true;
        }
        const mLayer = LAYER_ORDER.includes(m.layer) ? m.layer : "shared";
        layerBreakdown[mLayer] = (layerBreakdown[mLayer] || 0) + 1;

        const isCrit =
          cycleNodes.has(m.id) ||
          violationNodes.has(m.id) ||
          m.health_status === "cycle" ||
          m.health_status === "violation" ||
          m.health_status === "critical";
        const isWarn =
          godNodes.has(m.id) ||
          deadNodes.has(m.id) ||
          m.health_status === "hotspot" ||
          m.health_status === "god" ||
          m.health_status === "dead" ||
          m.health_status === "warning";

        if (isCrit) {
          criticalCount += 1;
        } else if (isWarn) {
          warningCount += 1;
        }
      }

      const issueCount = criticalCount + warningCount;
      const healthStatus = criticalCount > 0 ? "critical" : warningCount > 0 ? "warning" : "healthy";
      const fanIn = extInCounts.get(clusterId) || 0;
      const fanOut = extOutCounts.get(clusterId) || 0;
      const instability = fanIn + fanOut > 0 ? Number((fanOut / (fanIn + fanOut)).toFixed(3)) : 0;
      const dominantLang = pickModeByCountAndLoc(members, (m) => m.language, "unknown");
      const dominantLayer = pickModeByCountAndLoc(
        members,
        (m) => (LAYER_ORDER.includes(m.layer) ? m.layer : "shared"),
        "shared"
      );

      const parts = key === "(root)" ? [] : key.split("/");
      const shortLabel =
        parts.length > 3 ? ".../" + parts.slice(-2).join("/") : key;

      const summaryNode = {
        id: clusterId,
        isClusterNode: true,
        clusterKey: key,
        path: key,
        name: shortLabel,
        directory: key,
        cluster: key,
        language: dominantLang,
        layer: dominantLayer,
        fileCount: members.length,
        loc,
        complexity,
        fan_in: fanIn,
        fan_out: fanOut,
        instability,
        routesCount,
        issueCount,
        criticalCount,
        warningCount,
        health_status: healthStatus,
        has_db_access: hasDbAccess,
        is_entrypoint: isEntrypoint,
        internalEdgeCount: internalEdgeCounts.get(clusterId) || 0,
        layers: layerBreakdown,
        memberIds: members.map((m) => m.id),
      };

      clusterNodes.push(summaryNode);
      clusterLookup.set(clusterId, summaryNode);
    }

    const clusterEdges = Array.from(aggEdgesMap.values())
      .map((agg) => {
        const topSymbols = Array.from(agg.symbolCounts.entries())
          .sort((a, b) => (b[1] - a[1]) || compareStrings(a[0], b[0]))
          .slice(0, 8)
          .map((entry) => entry[0]);
        return {
          source: agg.source,
          target: agg.target,
          edgeCount: agg.edgeCount,
          symbols: topSymbols,
          kind: "cluster",
          is_cycle: agg.is_cycle,
          is_violation: agg.is_violation,
          isAggregatedEdge: true,
        };
      })
      .sort((a, b) => compareStrings(a.source, b.source) || compareStrings(a.target, b.target));

    return {
      nodes: clusterNodes,
      edges: clusterEdges,
      clusterMembers,
      clusterLookup,
    };
  }

  function buildAdjacency(nodes, edges) {
    const nodeIds = new Set(nodes.map((n) => n.id));
    const outgoing = new Map();
    const incoming = new Map();
    for (const n of nodes) {
      outgoing.set(n.id, []);
      incoming.set(n.id, []);
    }
    const sortedEdges = edges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target)
      .slice()
      .sort((a, b) => compareStrings(a.source, b.source) || compareStrings(a.target, b.target));

    for (const e of sortedEdges) {
      outgoing.get(e.source).push(e.target);
      incoming.get(e.target).push(e.source);
    }
    return { outgoing, incoming, sortedEdges };
  }

  function computeBlastRadiusBFS(graph, targetId, maxDepth) {
    const depthLimit = Math.max(1, Number(maxDepth || 5));
    const nodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph && graph.edges) ? graph.edges : [];
    const nodeSet = new Set(nodes.map((n) => n.id));
    if (!targetId || !nodeSet.has(targetId)) {
      return {
        target_id: targetId || "",
        upstream: [],
        downstream: [],
        upstream_count: 0,
        downstream_count: 0,
      };
    }

    const { outgoing, incoming } = buildAdjacency(nodes, edges);

    function runBfs(adjMap) {
      const visited = new Set([targetId]);
      const queue = [{ id: targetId, depth: 0, path: [targetId] }];
      const results = [];
      let head = 0;
      while (head < queue.length) {
        const current = queue[head];
        head += 1;
        if (current.depth >= depthLimit) {
          continue;
        }
        const neighbors = (adjMap.get(current.id) || []).slice().sort(compareStrings);
        for (const nextId of neighbors) {
          if (visited.has(nextId)) {
            continue;
          }
          visited.add(nextId);
          const nextDepth = current.depth + 1;
          const nextPath = current.path.concat([nextId]);
          results.push({ node_id: nextId, depth: nextDepth, path: nextPath });
          queue.push({ id: nextId, depth: nextDepth, path: nextPath });
        }
      }
      results.sort((a, b) => (a.depth - b.depth) || compareStrings(a.node_id, b.node_id));
      return results;
    }

    const upstream = runBfs(incoming);
    const downstream = runBfs(outgoing);
    return {
      target_id: targetId,
      upstream,
      downstream,
      upstream_count: upstream.length,
      downstream_count: downstream.length,
    };
  }

  function computeTraceFlowBFS(graph, entrypointId, maxDepth) {
    const depthLimit = Math.max(1, Number(maxDepth || 8));
    const nodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph && graph.edges) ? graph.edges : [];
    const nodeById = new Map(nodes.map((n) => [n.id, n]));
    if (!entrypointId || !nodeById.has(entrypointId)) {
      return {
        entrypoint_id: entrypointId || "",
        data_targets: [],
        paths: [],
        highlighted_nodes: [],
        highlighted_edges: [],
      };
    }

    const { outgoing } = buildAdjacency(nodes, edges);
    const visitedDepth = new Map([[entrypointId, 0]]);
    const queue = [{ id: entrypointId, depth: 0, path: [entrypointId] }];
    const paths = [];
    const dataTargetsSet = new Set();
    let head = 0;

    while (head < queue.length) {
      const current = queue[head];
      head += 1;
      const node = nodeById.get(current.id);
      const isDataTarget = Boolean(node && (node.layer === "data" || node.has_db_access));
      if (current.depth > 0 && isDataTarget) {
        dataTargetsSet.add(current.id);
        paths.push(current.path);
      }
      if (current.depth >= depthLimit) {
        continue;
      }
      const neighbors = (outgoing.get(current.id) || []).slice().sort(compareStrings);
      for (const nextId of neighbors) {
        const nextDepth = current.depth + 1;
        const prevDepth = visitedDepth.get(nextId);
        if (prevDepth !== undefined && prevDepth <= nextDepth) {
          continue;
        }
        visitedDepth.set(nextId, nextDepth);
        queue.push({ id: nextId, depth: nextDepth, path: current.path.concat([nextId]) });
      }
    }

    const highlightedNodesSet = new Set();
    const edgePairKeys = new Set();
    const highlightedEdges = [];
    for (const p of paths) {
      for (let i = 0; i < p.length; i += 1) {
        highlightedNodesSet.add(p[i]);
        if (i + 1 < p.length) {
          const key = p[i] + "->" + p[i + 1];
          if (!edgePairKeys.has(key)) {
            edgePairKeys.add(key);
            highlightedEdges.push([p[i], p[i + 1]]);
          }
        }
      }
    }
    if (paths.length === 0) {
      highlightedNodesSet.add(entrypointId);
    }

    return {
      entrypoint_id: entrypointId,
      data_targets: Array.from(dataTargetsSet).sort(compareStrings),
      paths,
      highlighted_nodes: Array.from(highlightedNodesSet).sort(compareStrings),
      highlighted_edges: highlightedEdges,
    };
  }

  function computeConvexHull(points) {
    if (points.length <= 2) {
      return points.slice();
    }
    const sorted = points
      .slice()
      .sort((a, b) => (a.x - b.x) || (a.y - b.y));

    function cross(o, a, b) {
      return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    }

    const lower = [];
    for (const p of sorted) {
      while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
        lower.pop();
      }
      lower.push(p);
    }

    const upper = [];
    for (let i = sorted.length - 1; i >= 0; i -= 1) {
      const p = sorted[i];
      while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
        upper.pop();
      }
      upper.push(p);
    }

    lower.pop();
    upper.pop();
    return lower.concat(upper);
  }

  function buildPaddedHull(id, label, directory, primaryLayer, items, padding) {
    const samplePoints = [];
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    let sumX = 0;
    let sumY = 0;

    for (const item of items) {
      const rx = item.isClusterNode ? Math.max(item.radius, 86) + padding : item.radius + padding;
      const ry = item.isClusterNode ? Math.max(item.radius, 34) + padding : item.radius + padding;
      sumX += item.x;
      sumY += item.y;
      minX = Math.min(minX, item.x - rx);
      maxX = Math.max(maxX, item.x + rx);
      minY = Math.min(minY, item.y - ry);
      maxY = Math.max(maxY, item.y + ry);
      for (let k = 0; k < 8; k += 1) {
        const angle = (k * Math.PI) / 4;
        samplePoints.push({
          x: item.x + Math.cos(angle) * rx,
          y: item.y + Math.sin(angle) * ry,
        });
      }
    }

    const hullPoints = computeConvexHull(samplePoints);
    return {
      id,
      label,
      directory,
      primaryLayer,
      points: hullPoints,
      center: {
        x: items.length > 0 ? sumX / items.length : 0,
        y: items.length > 0 ? sumY / items.length : 0,
      },
      bounds: { minX, maxX, minY, maxY },
      count: items.length,
    };
  }

  /* 1. Layers Layout: 7-column swimlane with barycenter crossing minimization */
  function computeLayersLayout(graph) {
    const nodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph && graph.edges) ? graph.edges : [];
    const positions = new Map();
    if (nodes.length === 0) {
      return { mode: "layers", positions, lanes: [] };
    }

    const { outgoing, incoming } = buildAdjacency(nodes, edges);
    const layerBuckets = new Map();
    for (const layer of LAYER_ORDER) {
      layerBuckets.set(layer, []);
    }

    for (const node of nodes) {
      const layer = LAYER_ORDER.includes(node.layer) ? node.layer : "shared";
      layerBuckets.get(layer).push(node);
    }

    for (const layer of LAYER_ORDER) {
      layerBuckets.get(layer).sort((a, b) => {
        return (
          compareStrings(a.cluster || "", b.cluster || "") ||
          compareStrings(a.id, b.id)
        );
      });
    }

    const activeLayers = LAYER_ORDER.filter((layer) => layerBuckets.get(layer).length > 0);
    const laneIndexOfNode = new Map();
    const orderInLane = new Map();

    function refreshIndices() {
      for (let c = 0; c < activeLayers.length; c += 1) {
        const list = layerBuckets.get(activeLayers[c]);
        const denom = Math.max(1, list.length - 1);
        for (let r = 0; r < list.length; r += 1) {
          laneIndexOfNode.set(list[r].id, c);
          orderInLane.set(list[r].id, list.length === 1 ? 0.5 : r / denom);
        }
      }
    }

    refreshIndices();

    function sortColumnByBarycenter(colIdx, direction) {
      const layer = activeLayers[colIdx];
      const list = layerBuckets.get(layer);
      if (list.length <= 1) {
        return;
      }
      const scores = new Map();
      for (const node of list) {
        const neighbors = []
          .concat(outgoing.get(node.id) || [])
          .concat(incoming.get(node.id) || []);
        let sum = 0;
        let weightSum = 0;
        for (const nbrId of neighbors) {
          const nbrCol = laneIndexOfNode.get(nbrId);
          if (nbrCol === undefined || nbrCol === colIdx) {
            continue;
          }
          const isPreferredSide =
            (direction > 0 && nbrCol < colIdx) || (direction < 0 && nbrCol > colIdx);
          const dist = Math.abs(nbrCol - colIdx);
          const w = isPreferredSide ? 2.0 / dist : 0.45 / dist;
          sum += orderInLane.get(nbrId) * w;
          weightSum += w;
        }
        const currentPos = orderInLane.get(node.id);
        scores.set(node.id, weightSum > 0 ? sum / weightSum : currentPos);
      }

      list.sort((a, b) => {
        const diff = scores.get(a.id) - scores.get(b.id);
        if (Math.abs(diff) > 1e-6) {
          return diff;
        }
        return compareStrings(a.id, b.id);
      });
    }

    for (let sweep = 0; sweep < 5; sweep += 1) {
      for (let c = 1; c < activeLayers.length; c += 1) {
        sortColumnByBarycenter(c, 1);
        refreshIndices();
      }
      for (let c = activeLayers.length - 2; c >= 0; c -= 1) {
        sortColumnByBarycenter(c, -1);
        refreshIndices();
      }
    }

    const hasClusters = nodes.some((n) => Boolean(n && n.isClusterNode));
    const rowGap = hasClusters ? 116 : 88;
    const laneGap = hasClusters ? 64 : 56;
    const subColGap = hasClusters ? 210 : 160;
    const laneSpecs = [];
    let cursorX = 0;

    for (const layer of activeLayers) {
      const list = layerBuckets.get(layer);
      const subCols = list.length > 60 ? 4 : list.length > 30 ? 3 : list.length > 14 ? 2 : 1;
      const rowsPerCol = Math.ceil(list.length / subCols);
      const laneWidth = hasClusters
        ? Math.max(232, subCols * 210 + 24)
        : Math.max(220, subCols * 164 + 48);
      const laneHeight = Math.max(240, rowsPerCol * rowGap + 96);
      laneSpecs.push({
        layer,
        list,
        subCols,
        rowsPerCol,
        xLeft: cursorX,
        width: laneWidth,
        height: laneHeight,
      });
      cursorX += laneWidth + laneGap;
    }

    const totalWidth = Math.max(0, cursorX - laneGap);
    const offsetX = -totalWidth / 2;
    const maxLaneHeight = laneSpecs.reduce((acc, s) => Math.max(acc, s.height), 280);
    const lanes = [];

    for (const spec of laneSpecs) {
      const xStart = spec.xLeft + offsetX;
      const centerX = xStart + spec.width / 2;
      const colHeight = (spec.rowsPerCol - 1) * rowGap;
      const yStart = -colHeight / 2;

      for (let idx = 0; idx < spec.list.length; idx += 1) {
        const node = spec.list[idx];
        const subCol = Math.floor(idx / spec.rowsPerCol);
        const row = idx % spec.rowsPerCol;
        const x =
          spec.subCols === 1
            ? centerX
            : centerX + (subCol - (spec.subCols - 1) / 2) * subColGap;
        const y = yStart + row * rowGap;
        positions.set(node.id, {
          x,
          y,
          radius: computeNodeRadius(node, false),
        });
      }

      lanes.push({
        id: spec.layer,
        layer: spec.layer,
        label: LAYER_LABELS[spec.layer] || spec.layer,
        x: xStart,
        y: -maxLaneHeight / 2 - 28,
        width: spec.width,
        height: maxLaneHeight + 56,
        count: spec.list.length,
      });
    }

    return { mode: "layers", positions, lanes };
  }

  /* 2. Constellation Layout: Seeded deterministic force-directed grouped by cluster */
  function computeConstellationLayout(graph) {
    const nodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph && graph.edges) ? graph.edges : [];
    const positions = new Map();
    if (nodes.length === 0) {
      return { mode: "constellation", positions, hulls: [] };
    }

    const clusterMap = new Map();
    const clusterMeta = new Map();
    if (Array.isArray(graph && graph.clusters)) {
      for (const c of graph.clusters) {
        clusterMeta.set(c.id, c);
      }
    }

    const hasClusters = nodes.some((n) => Boolean(n && n.isClusterNode));
    const sortedNodes = nodes.slice().sort((a, b) => compareStrings(a.id, b.id));
    const seedVal = sortedNodes.reduce((acc, n) => (acc ^ hashString(n.id)) >>> 0, 0x517cc1b7);
    const rng = createRng(seedVal);

    for (const n of sortedNodes) {
      let cid;
      if (n.isClusterNode) {
        const key = n.clusterKey || n.directory || n.id;
        const slashIdx = key.indexOf("/");
        cid = slashIdx >= 0 ? key.slice(0, slashIdx) : key;
      } else {
        cid = n.cluster || n.directory || "root";
      }
      if (!clusterMap.has(cid)) {
        clusterMap.set(cid, []);
      }
      clusterMap.get(cid).push(n);
    }

    const clusterIds = Array.from(clusterMap.keys()).sort(compareStrings);
    const clusterCenters = new Map();
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const baseClusterSpread = hasClusters
      ? Math.max(320, 230 + Math.sqrt(sortedNodes.length) * 68)
      : Math.max(250, 175 + Math.sqrt(sortedNodes.length) * 38);

    for (let i = 0; i < clusterIds.length; i += 1) {
      const cid = clusterIds[i];
      if (clusterIds.length === 1) {
        clusterCenters.set(cid, { x: 0, y: 0 });
      } else {
        const angle = i * goldenAngle + 0.35;
        const radius = baseClusterSpread * Math.sqrt(i + 0.65);
        clusterCenters.set(cid, {
          x: Math.cos(angle) * radius,
          y: Math.sin(angle) * radius,
        });
      }
    }

    const state = new Map();
    for (const cid of clusterIds) {
      const cNodes = clusterMap.get(cid);
      const center = clusterCenters.get(cid);
      for (let i = 0; i < cNodes.length; i += 1) {
        const n = cNodes[i];
        const a = i * goldenAngle + rng() * 0.15;
        const r = cNodes.length === 1 ? 0 : (hasClusters ? 95 : 34) + Math.sqrt(i + 1) * (hasClusters ? 76 : 34);
        state.set(n.id, {
          node: n,
          isClusterNode: Boolean(n.isClusterNode),
          cid,
          x: center.x + Math.cos(a) * r,
          y: center.y + Math.sin(a) * r,
          vx: 0,
          vy: 0,
          radius: computeNodeRadius(n, false),
        });
      }
    }

    const items = Array.from(state.values());
    const validEdges = edges.filter(
      (e) => state.has(e.source) && state.has(e.target) && e.source !== e.target
    );

    const iterations = items.length > 260 ? 75 : 120;
    const repulsionK = hasClusters ? 24000 : 5400;
    const idealEdgeLen = hasClusters ? 250 : 118;

    for (let iter = 0; iter < iterations; iter += 1) {
      const cool = 1 - iter / iterations;
      const stepScale = 0.25 + 0.75 * cool;

      // Update live cluster centroids
      const liveCenters = new Map();
      for (const cid of clusterIds) {
        liveCenters.set(cid, { x: 0, y: 0, count: 0 });
      }
      for (const item of items) {
        const c = liveCenters.get(item.cid);
        c.x += item.x;
        c.y += item.y;
        c.count += 1;
      }
      for (const cid of clusterIds) {
        const c = liveCenters.get(cid);
        if (c.count > 0) {
          c.x /= c.count;
          c.y /= c.count;
        }
      }

      // Node-node repulsion (bounded cutoff on large repos)
      const maxDistSq = items.length > 220 ? 320 * 320 : Infinity;
      for (let i = 0; i < items.length; i += 1) {
        const a = items[i];
        for (let j = i + 1; j < items.length; j += 1) {
          const b = items[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distSq = dx * dx + dy * dy + 0.01;
          if (distSq > maxDistSq) {
            continue;
          }
          const sameCluster = a.cid === b.cid;
          const dist = Math.sqrt(distSq);
          const repulse = ((sameCluster ? repulsionK : repulsionK * 1.45) / distSq) * stepScale;
          const fx = (dx / dist) * repulse;
          const fy = (dy / dist) * repulse;
          a.vx += fx;
          a.vy += fy;
          b.vx -= fx;
          b.vy -= fy;
        }
      }

      // Edge spring attraction
      for (const e of validEdges) {
        const a = state.get(e.source);
        const b = state.get(e.target);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const sameCluster = a.cid === b.cid;
        const targetLen = sameCluster ? idealEdgeLen : idealEdgeLen * 2.1;
        const k = sameCluster ? 0.045 : 0.014;
        const force = (dist - targetLen) * k * stepScale;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      // Cluster anchor + gentle origin gravity
      for (const item of items) {
        const anchor = clusterCenters.get(item.cid);
        const liveC = liveCenters.get(item.cid);
        item.vx += (anchor.x - item.x) * 0.03 * stepScale;
        item.vy += (anchor.y - item.y) * 0.03 * stepScale;
        item.vx += (liveC.x - item.x) * 0.055 * stepScale;
        item.vy += (liveC.y - item.y) * 0.055 * stepScale;
        item.vx *= 0.72;
        item.vy *= 0.72;

        const maxStep = 28 * stepScale + 4;
        const vLen = Math.sqrt(item.vx * item.vx + item.vy * item.vy);
        if (vLen > maxStep) {
          item.vx = (item.vx / vLen) * maxStep;
          item.vy = (item.vy / vLen) * maxStep;
        }
        item.x += item.vx;
        item.y += item.vy;
      }
    }

    // Final overlap resolution
    const overlapPasses = hasClusters ? 24 : 16;
    for (let pass = 0; pass < overlapPasses; pass += 1) {
      for (let i = 0; i < items.length; i += 1) {
        const a = items[i];
        for (let j = i + 1; j < items.length; j += 1) {
          const b = items[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          if (a.isClusterNode || b.isClusterNode) {
            const pad = a.cid === b.cid ? 24 : 36;
            const reqW = (a.isClusterNode ? 86 : a.radius) + (b.isClusterNode ? 86 : b.radius) + pad;
            const reqH = (a.isClusterNode ? 34 : a.radius) + (b.isClusterNode ? 34 : b.radius) + pad;
            const ox = reqW - Math.abs(dx);
            const oy = reqH - Math.abs(dy);
            if (ox > 0 && oy > 0) {
              if (ox < oy) {
                const signX = Math.abs(dx) > 0.01 ? Math.sign(dx) : ((i + j) % 2 === 0 ? 1 : -1);
                const shiftX = ox * 0.52 * signX;
                a.x -= shiftX;
                b.x += shiftX;
              } else {
                const signY = Math.abs(dy) > 0.01 ? Math.sign(dy) : ((i + j) % 2 === 0 ? 1 : -1);
                const shiftY = oy * 0.52 * signY;
                a.y -= shiftY;
                b.y += shiftY;
              }
            }
            continue;
          }
          const minDist = a.radius + b.radius + (a.cid === b.cid ? 38 : 66);
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < minDist) {
            const safeDist = dist || 0.01;
            const overlap = (minDist - safeDist) * 0.5;
            const nx = dist > 0.01 ? dx / safeDist : Math.cos((i + j) * 0.7);
            const ny = dist > 0.01 ? dy / safeDist : Math.sin((i + j) * 0.7);
            a.x -= nx * overlap;
            a.y -= ny * overlap;
            b.x += nx * overlap;
            b.y += ny * overlap;
          }
        }
      }
    }

    // Center constellation at (0, 0)
    let meanX = 0;
    let meanY = 0;
    for (const item of items) {
      meanX += item.x;
      meanY += item.y;
    }
    meanX /= items.length;
    meanY /= items.length;

    const clusterItems = new Map();
    for (const cid of clusterIds) {
      clusterItems.set(cid, []);
    }

    for (const item of items) {
      item.x -= meanX;
      item.y -= meanY;
      positions.set(item.node.id, {
        x: item.x,
        y: item.y,
        radius: item.radius,
      });
      clusterItems.get(item.cid).push(item);
    }

    const hulls = [];
    for (const cid of clusterIds) {
      const cItems = clusterItems.get(cid);
      if (cItems.length === 0) {
        continue;
      }
      const meta = clusterMeta.get(cid);
      const label = hasClusters ? cid : ((meta && meta.label) || cItems[0].node.directory || cid);
      const directory = hasClusters ? cid : ((meta && meta.directory) || cItems[0].node.directory || ".");
      const primaryLayer = (meta && meta.primary_layer) || cItems[0].node.layer || "shared";
      hulls.push(buildPaddedHull(cid, label, directory, primaryLayer, cItems, 40));
    }

    return { mode: "constellation", positions, hulls };
  }

  /* 3. Blast Layout: Radial concentric rings around selected or hub node */
  function computeBlastLayout(graph, health, options) {
    const nodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const positions = new Map();
    if (nodes.length === 0) {
      return {
        mode: "blast",
        positions,
        rings: { centerId: "", radii: [210, 370, 530], upstreamMap: new Map(), downstreamMap: new Map() },
      };
    }

    const nodeById = new Map(nodes.map((n) => [n.id, n]));
    let focusId = options && options.focusNodeId && nodeById.has(options.focusNodeId)
      ? options.focusNodeId
      : "";

    if (!focusId) {
      const sortedHubs = nodes.slice().sort((a, b) => {
        const degA = Number(a.fan_in || 0) + Number(a.fan_out || 0);
        const degB = Number(b.fan_in || 0) + Number(b.fan_out || 0);
        if (degB !== degA) {
          return degB - degA;
        }
        if (Number(b.loc || 0) !== Number(a.loc || 0)) {
          return Number(b.loc || 0) - Number(a.loc || 0);
        }
        return compareStrings(a.id, b.id);
      });
      focusId = sortedHubs[0].id;
    }

    const blastData =
      options && options.blastRadius && options.blastRadius.target_id === focusId
        ? options.blastRadius
        : computeBlastRadiusBFS(graph, focusId, 5);

    const upstreamMap = new Map();
    const downstreamMap = new Map();
    for (const item of blastData.upstream || []) {
      if (nodeById.has(item.node_id)) {
        upstreamMap.set(item.node_id, item.depth);
      }
    }
    for (const item of blastData.downstream || []) {
      if (nodeById.has(item.node_id)) {
        downstreamMap.set(item.node_id, item.depth);
      }
    }

    const hasClusters = nodes.some((n) => Boolean(n && n.isClusterNode));
    const centerNode = nodeById.get(focusId);
    positions.set(focusId, {
      x: 0,
      y: 0,
      radius: hasClusters
        ? clamp(computeNodeRadius(centerNode, false) + 2, 44, 56)
        : Math.max(24, computeNodeRadius(centerNode, false) + 6),
      depth: 0,
      direction: "center",
    });

    const ringRadii = hasClusters ? [280, 500, 720] : [210, 370, 530];
    function ringRadiusForDepth(depth) {
      if (depth <= 3) {
        return ringRadii[depth - 1];
      }
      return ringRadii[2] + (depth - 3) * (hasClusters ? 200 : 145);
    }

    const upstreamByDepth = new Map();
    const downstreamByDepth = new Map();
    const unrelated = [];

    for (const node of nodes) {
      if (node.id === focusId) {
        continue;
      }
      const uDepth = upstreamMap.get(node.id);
      const dDepth = downstreamMap.get(node.id);
      if (uDepth !== undefined && (dDepth === undefined || uDepth <= dDepth)) {
        if (!upstreamByDepth.has(uDepth)) {
          upstreamByDepth.set(uDepth, []);
        }
        upstreamByDepth.get(uDepth).push(node);
      } else if (dDepth !== undefined) {
        if (!downstreamByDepth.has(dDepth)) {
          downstreamByDepth.set(dDepth, []);
        }
        downstreamByDepth.get(dDepth).push(node);
      } else {
        unrelated.push(node);
      }
    }

    function placeArcGroup(groupMap, centerAngle, directionName) {
      const depths = Array.from(groupMap.keys()).sort((a, b) => a - b);
      const chordStep = hasClusters ? 190 : 76;
      const bandGap = hasClusters ? 104 : 66;
      for (const depth of depths) {
        const group = groupMap.get(depth).slice().sort((a, b) => compareStrings(a.id, b.id));
        const rBase = ringRadiusForDepth(depth);
        const maxPerBand = Math.max(hasClusters ? 4 : 5, Math.floor((rBase * 2.1) / chordStep));
        const bands = Math.ceil(group.length / maxPerBand);

        for (let idx = 0; idx < group.length; idx += 1) {
          const bandIdx = Math.floor(idx / maxPerBand);
          const posInBand = idx % maxPerBand;
          const bandCount =
            bandIdx === bands - 1 ? group.length - bandIdx * maxPerBand : maxPerBand;
          const r = rBase + bandIdx * bandGap;
          const span = Math.min(Math.PI * 0.82, 0.34 + bandCount * (hasClusters ? 0.24 : 0.16));
          const angle =
            bandCount === 1
              ? centerAngle
              : centerAngle - span / 2 + (posInBand / (bandCount - 1)) * span;
          const node = group[idx];
          positions.set(node.id, {
            x: Math.cos(angle) * r,
            y: Math.sin(angle) * r,
            radius: computeNodeRadius(node, false),
            depth,
            direction: directionName,
          });
        }
      }
    }

    placeArcGroup(upstreamByDepth, Math.PI, "upstream");
    placeArcGroup(downstreamByDepth, 0, "downstream");

    // Place unrelated nodes cleanly in outer parking perimeter
    unrelated.sort((a, b) => compareStrings(a.id, b.id));
    if (unrelated.length > 0) {
      const maxUsedDepth = Math.max(
        3,
        ...Array.from(upstreamByDepth.keys()),
        ...Array.from(downstreamByDepth.keys())
      );
      const outerBase = ringRadiusForDepth(maxUsedDepth) + (hasClusters ? 210 : 185);
      const cols = Math.max(4, Math.ceil(Math.sqrt(unrelated.length) * 1.6));
      const spacingX = hasClusters ? 208 : 110;
      const spacingY = hasClusters ? 106 : 86;
      const startX = -((cols - 1) * spacingX) / 2;
      const startY = outerBase + 40;
      for (let i = 0; i < unrelated.length; i += 1) {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const node = unrelated[i];
        positions.set(node.id, {
          x: startX + col * spacingX,
          y: startY + row * spacingY,
          radius: node.isClusterNode ? computeNodeRadius(node, false) : Math.max(14, computeNodeRadius(node, false) - 2),
          depth: null,
          direction: "unrelated",
        });
      }
    }

    return {
      mode: "blast",
      positions,
      rings: {
        centerId: focusId,
        radii: ringRadii,
        upstreamMap,
        downstreamMap,
      },
    };
  }

  /* 4. Health Layout: Isolates problem clusters, scales radius by LOC/complexity */
  function computeHealthLayout(graph, health) {
    const nodes = Array.isArray(graph && graph.nodes) ? graph.nodes : [];
    const positions = new Map();
    if (nodes.length === 0) {
      return { mode: "health", positions, hulls: [] };
    }

    const hasClusters = nodes.some((n) => Boolean(n && n.isClusterNode));
    const { cycleNodes, violationNodes, godNodes, deadNodes } = collectHealthSets(graph, health);

    const buckets = {
      cycle: [],
      violation: [],
      god: [],
      dead: [],
      healthy: [],
    };

    const nodeMeta = new Map();
    const sortedNodes = nodes.slice().sort((a, b) => compareStrings(a.id, b.id));

    for (const n of sortedNodes) {
      const issues = [];
      let score = 0;
      if (cycleNodes.has(n.id) || n.health_status === "cycle") {
        issues.push("cycle");
        score = Math.max(score, 0.95);
      }
      if (
        violationNodes.has(n.id) ||
        n.health_status === "violation" ||
        (n.isClusterNode && (Number(n.criticalCount || 0) > 0 || n.health_status === "critical"))
      ) {
        issues.push("violation");
        score = Math.max(score, 0.9);
      }
      if (
        godNodes.has(n.id) ||
        n.health_status === "hotspot" ||
        n.health_status === "god" ||
        (n.isClusterNode && issues.length === 0 && (Number(n.warningCount || 0) > 0 || n.health_status === "warning"))
      ) {
        issues.push("god");
        score = Math.max(score, 0.65);
      }
      if (deadNodes.has(n.id) || n.health_status === "dead") {
        issues.push("dead");
        score = Math.max(score, 0.45);
      }
      if (issues.length === 0) {
        if (n.health_status === "critical") {
          issues.push("violation");
          score = Math.max(score, 0.9);
        } else if (n.health_status === "warning") {
          issues.push("god");
          score = Math.max(score, 0.58);
        } else {
          const cpxDenom = n.isClusterNode ? Math.max(1, Number(n.fileCount || 1)) * 25 : 35;
          const locDenom = n.isClusterNode ? Math.max(1, Number(n.fileCount || 1)) * 350 : 450;
          const cpxScore = clamp(Number(n.complexity || 1) / cpxDenom, 0, 0.42);
          const locScore = clamp(Number(n.loc || 0) / locDenom, 0, 0.35);
          score = Math.max(cpxScore, locScore);
        }
      }

      const radius = computeNodeRadius(n, true);
      nodeMeta.set(n.id, { node: n, issues, healthScore: score, radius });

      if (issues.includes("cycle")) {
        buckets.cycle.push(n);
      } else if (issues.includes("violation")) {
        buckets.violation.push(n);
      } else if (issues.includes("god")) {
        buckets.god.push(n);
      } else if (issues.includes("dead")) {
        buckets.dead.push(n);
      } else {
        buckets.healthy.push(n);
      }
    }

    const zoneDefs = [
      { key: "cycle", label: "Circular Dependencies", layer: "entrypoint", row: 0, side: -1 },
      { key: "violation", label: "Layer Violations", layer: "config", row: 0, side: 1 },
      { key: "god", label: hasClusters ? "Complexity & Warning Clusters" : "God & Complexity Hotspots", layer: "service", row: 1, side: -1 },
      { key: "dead", label: "Orphan / Dead Modules", layer: "shared", row: 1, side: 1 },
      { key: "healthy", label: "Healthy Architecture", layer: "data", row: 2, side: 0 },
    ];

    const zoneSpecs = [];
    for (const zone of zoneDefs) {
      const list = buckets[zone.key];
      if (list.length === 0) {
        continue;
      }
      list.sort((a, b) => {
        const sa = nodeMeta.get(a.id).healthScore;
        const sb = nodeMeta.get(b.id).healthScore;
        return (sb - sa) || compareStrings(a.id, b.id);
      });

      const cols = zone.key === "healthy"
        ? Math.min(hasClusters ? 5 : 8, Math.max(hasClusters ? 2 : 4, Math.ceil(Math.sqrt(list.length) * 1.5)))
        : Math.min(hasClusters ? 3 : 4, Math.max(hasClusters ? 1 : 2, Math.ceil(Math.sqrt(list.length))));
      const cellX = hasClusters ? 208 : (zone.key === "healthy" ? 116 : 138);
      const cellY = hasClusters ? 108 : (zone.key === "healthy" ? 96 : 118);
      const rows = Math.ceil(list.length / cols);
      const itemPadX = hasClusters ? 172 : 84;
      const itemPadY = hasClusters ? 68 : 84;
      const boxW = (cols - 1) * cellX + itemPadX + 88;
      const boxH = (rows - 1) * cellY + itemPadY + 88;
      zoneSpecs.push({ zone, list, cols, rows, cellX, cellY, boxW, boxH });
    }

    // Position zone rows dynamically so hulls never overlap even on 300+ modules or cluster cards
    const rowBuckets = [0, 1, 2].map((r) => zoneSpecs.filter((s) => s.zone.row === r));
    const zoneGapX = 110;
    const zoneGapY = 110;
    let cursorY = 0;

    for (const rowList of rowBuckets) {
      if (rowList.length === 0) {
        continue;
      }
      const rowH = rowList.reduce((acc, s) => Math.max(acc, s.boxH), 0);
      const centerY = cursorY + rowH / 2;
      if (rowList.length === 1) {
        rowList[0].anchorX = 0;
        rowList[0].anchorY = centerY;
      } else {
        const leftSpec = rowList[0];
        const rightSpec = rowList[1];
        const span = (leftSpec.boxW + rightSpec.boxW) / 2 + zoneGapX;
        leftSpec.anchorX = -span / 2;
        leftSpec.anchorY = centerY;
        rightSpec.anchorX = span / 2;
        rightSpec.anchorY = centerY;
      }
      cursorY += rowH + zoneGapY;
    }

    const hulls = [];
    const itemsPlaced = [];

    for (const spec of zoneSpecs) {
      const { zone, list, cols, rows, cellX, cellY, anchorX, anchorY } = spec;
      const startX = anchorX - ((cols - 1) * cellX) / 2;
      const startY = anchorY - ((rows - 1) * cellY) / 2;

      const zoneItems = [];
      for (let i = 0; i < list.length; i += 1) {
        const n = list[i];
        const meta = nodeMeta.get(n.id);
        const col = i % cols;
        const row = Math.floor(i / cols);
        const x = startX + col * cellX;
        const y = startY + row * cellY;
        const item = {
          id: n.id,
          isClusterNode: Boolean(n.isClusterNode),
          x,
          y,
          radius: meta.radius,
          healthScore: meta.healthScore,
          issues: meta.issues,
        };
        zoneItems.push(item);
        itemsPlaced.push(item);
      }

      hulls.push(
        buildPaddedHull(
          "health-" + zone.key,
          zone.label,
          zone.key,
          zone.layer,
          zoneItems,
          44
        )
      );
    }

    // Center overall diagram vertically & horizontally
    let minY = Infinity;
    let maxY = -Infinity;
    let minX = Infinity;
    let maxX = -Infinity;
    for (const it of itemsPlaced) {
      minX = Math.min(minX, it.x - it.radius);
      maxX = Math.max(maxX, it.x + it.radius);
      minY = Math.min(minY, it.y - it.radius);
      maxY = Math.max(maxY, it.y + it.radius);
    }
    const shiftX = (minX + maxX) / 2;
    const shiftY = (minY + maxY) / 2;

    for (const it of itemsPlaced) {
      positions.set(it.id, {
        x: it.x - shiftX,
        y: it.y - shiftY,
        radius: it.radius,
        healthScore: it.healthScore,
        issues: it.issues,
      });
    }

    for (const h of hulls) {
      for (const p of h.points) {
        p.x -= shiftX;
        p.y -= shiftY;
      }
      h.center.x -= shiftX;
      h.center.y -= shiftY;
      h.bounds.minX -= shiftX;
      h.bounds.maxX -= shiftX;
      h.bounds.minY -= shiftY;
      h.bounds.maxY -= shiftY;
    }

    return { mode: "health", positions, hulls };
  }

  function computeLayout(mode, graph, health, options) {
    if (mode === "constellation") {
      return computeConstellationLayout(graph, health, options);
    }
    if (mode === "blast") {
      return computeBlastLayout(graph, health, options);
    }
    if (mode === "health") {
      return computeHealthLayout(graph, health, options);
    }
    return computeLayersLayout(graph, health, options);
  }

  const api = {
    LAYER_ORDER,
    LAYER_LABELS,
    buildClusterGraph,
    computeLayout,
    computeLayersLayout,
    computeConstellationLayout,
    computeBlastLayout,
    computeRadialBlastLayout: computeBlastLayout,
    computeHealthLayout,
    computeHealthLensLayout: computeHealthLayout,
    computeBlastRadiusBFS,
    computeTraceFlowBFS,
  };

  if (typeof window !== "undefined") {
    window.CodeAtlasLayouts = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
