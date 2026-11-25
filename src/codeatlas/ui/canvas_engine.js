/* CodeAtlas 60fps HiDPI Canvas 2D Architecture Studio Engine */
(function (rootScope) {
  "use strict";

  const THEMES = {
    obsidian: {
      bg: "#060913",
      gridDot: "rgba(148, 163, 184, 0.20)",
      gridMajor: "rgba(56, 189, 248, 0.28)",
      laneBg: "rgba(15, 22, 38, 0.45)",
      laneBorder: "rgba(148, 163, 184, 0.16)",
      laneHeaderBg: "rgba(15, 22, 38, 0.88)",
      hullFill: "rgba(30, 41, 59, 0.24)",
      hullStroke: "rgba(148, 163, 184, 0.26)",
      nodeFill: "#0f172a",
      nodeInner: "#162038",
      nodeStroke: "rgba(148, 163, 184, 0.48)",
      labelBg: "rgba(6, 9, 19, 0.84)",
      labelText: "#f1f5f9",
      labelMuted: "#94a3b8",
      edgeDefault: "rgba(148, 163, 184, 0.26)",
      edgeActive: "#38bdf8",
      edgeCycle: "#f43f5e",
      edgeViolation: "#f59e0b",
      edgeTrace: "#2dd4bf",
      upstream1: "#38bdf8",
      upstreamDeep: "#f59e0b",
      downstream: "#10b981",
      emerald: "#10b981",
      amber: "#f59e0b",
      crimson: "#f43f5e",
      cyan: "#38bdf8",
      minimapBg: "rgba(6, 9, 19, 0.92)",
      minimapViewport: "#38bdf8",
      layers: {
        frontend: "#38bdf8",
        entrypoint: "#2dd4bf",
        service: "#60a5fa",
        data: "#10b981",
        shared: "#94a3b8",
        config: "#f59e0b",
        test: "#64748b",
      },
    },
    paper: {
      bg: "#f8fafc",
      gridDot: "rgba(15, 23, 42, 0.15)",
      gridMajor: "rgba(2, 132, 199, 0.28)",
      laneBg: "rgba(241, 245, 249, 0.72)",
      laneBorder: "rgba(15, 23, 42, 0.12)",
      laneHeaderBg: "rgba(255, 255, 255, 0.92)",
      hullFill: "rgba(226, 232, 240, 0.36)",
      hullStroke: "rgba(15, 23, 42, 0.20)",
      nodeFill: "#ffffff",
      nodeInner: "#f1f5f9",
      nodeStroke: "rgba(15, 23, 42, 0.34)",
      labelBg: "rgba(255, 255, 255, 0.92)",
      labelText: "#0f172a",
      labelMuted: "#475569",
      edgeDefault: "rgba(71, 85, 105, 0.28)",
      edgeActive: "#0284c7",
      edgeCycle: "#e11d48",
      edgeViolation: "#d97706",
      edgeTrace: "#0d9488",
      upstream1: "#0284c7",
      upstreamDeep: "#d97706",
      downstream: "#059669",
      emerald: "#059669",
      amber: "#d97706",
      crimson: "#e11d48",
      cyan: "#0284c7",
      minimapBg: "rgba(248, 250, 252, 0.95)",
      minimapViewport: "#0284c7",
      layers: {
        frontend: "#0284c7",
        entrypoint: "#0d9488",
        service: "#2563eb",
        data: "#059669",
        shared: "#475569",
        config: "#d97706",
        test: "#64748b",
      },
    },
  };

  function easeOutCubic(t) {
    const inv = 1 - Math.max(0, Math.min(1, t));
    return 1 - inv * inv * inv;
  }

  function easeOutSpringSoft(t) {
    const c = Math.max(0, Math.min(1, t));
    if (c === 1) {
      return 1;
    }
    const c4 = (2 * Math.PI) / 3.4;
    return 1 - Math.pow(2, -9 * c) * Math.cos(c * c4 * 1.4);
  }

  function hexToRgb(hex) {
    const clean = hex.replace("#", "");
    const num = parseInt(clean, 16);
    return {
      r: (num >> 16) & 255,
      g: (num >> 8) & 255,
      b: num & 255,
    };
  }

  function lerpColor(hexA, hexB, t) {
    const a = hexToRgb(hexA);
    const b = hexToRgb(hexB);
    const k = Math.max(0, Math.min(1, t));
    const r = Math.round(a.r + (b.r - a.r) * k);
    const g = Math.round(a.g + (b.g - a.g) * k);
    const bl = Math.round(a.b + (b.b - a.b) * k);
    return "rgb(" + r + ", " + g + ", " + bl + ")";
  }

  function colorWithAlpha(hex, alpha) {
    const rgb = hexToRgb(hex);
    return "rgba(" + rgb.r + ", " + rgb.g + ", " + rgb.b + ", " + alpha + ")";
  }

  function roundedRectPath(ctx, x, y, w, h, r) {
    if (typeof ctx.roundRect === "function") {
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      return;
    }
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + w - radius, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
    ctx.lineTo(x + w, y + h - radius);
    ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    ctx.lineTo(x + radius, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  function readPos(positions, id) {
    if (!positions) {
      return null;
    }
    if (positions instanceof Map) {
      return positions.get(id) || null;
    }
    return Object.prototype.hasOwnProperty.call(positions, id) ? positions[id] : null;
  }

  const CLUSTER_CARD_W = 172;
  const CLUSTER_CARD_H = 68;
  const CLUSTER_HALF_W = CLUSTER_CARD_W / 2;
  const CLUSTER_HALF_H = CLUSTER_CARD_H / 2;

  function formatCompactLoc(loc) {
    const n = Number(loc || 0);
    if (n >= 1000) {
      return (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k";
    }
    return String(Math.round(n));
  }

  function truncateCanvasText(ctx, text, maxWidth) {
    const str = String(text || "");
    if (maxWidth <= 8) {
      return "";
    }
    if (ctx.measureText(str).width <= maxWidth) {
      return str;
    }
    const ellipsis = "...";
    const ellW = ctx.measureText(ellipsis).width;
    if (ellW >= maxWidth) {
      return "";
    }
    let lo = 0;
    let hi = str.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (ctx.measureText(str.slice(0, mid)).width + ellW <= maxWidth) {
        lo = mid;
      } else {
        hi = mid - 1;
      }
    }
    return str.slice(0, lo) + ellipsis;
  }

  function anchorPointOnNode(st, dirX, dirY, pad) {
    if (!st.node || !st.node.isClusterNode) {
      const r = st.radius + pad;
      return { x: st.x + dirX * r, y: st.y + dirY * r };
    }
    const hw = CLUSTER_HALF_W + pad;
    const hh = CLUSTER_HALF_H + pad;
    const ax = Math.abs(dirX);
    const ay = Math.abs(dirY);
    const scaleX = ax > 1e-6 ? hw / ax : Infinity;
    const scaleY = ay > 1e-6 ? hh / ay : Infinity;
    const t = Math.min(scaleX, scaleY);
    return { x: st.x + dirX * t, y: st.y + dirY * t };
  }

  class CodeAtlasCanvas {
    constructor(options) {
      this.canvas = options.canvas;
      this.container = options.container;
      this.minimapCanvas = options.minimapCanvas || null;
      this.callbacks = options.callbacks || {};

      this.ctx = this.canvas.getContext("2d");
      this.miniCtx = this.minimapCanvas ? this.minimapCanvas.getContext("2d") : null;

      this.themeName = "obsidian";
      this.palette = THEMES.obsidian;
      this.width = 960;
      this.height = 640;
      this.dpr = 1;

      this.camera = { x: 0, y: 0, zoom: 1 };
      this.inertia = { vx: 0, vy: 0, active: false };
      this.flyAnim = null;

      this.nodes = [];
      this.nodeById = new Map();
      this.edges = [];
      this.edgeBidirectional = new Set();
      this.layoutResult = { mode: "layers", positions: new Map(), lanes: [] };
      this.nodeStates = new Map();

      this.selectedId = null;
      this.hoveredId = null;
      this.spotlight = null;
      this.traceFlow = null;
      this.showCyclesOnly = false;
      this.showParticles = true;
      this.repoName = "";

      this.ripples = [];
      this.particleClock = 0;
      this.lastFrameTime = performance.now();
      this.rafId = 0;
      this.needsRedraw = true;
      this.isPointerPanning = false;
      this.isMinimapDragging = false;
      this.dragMoved = false;
      this.dragSamples = [];
      this.lastPointer = { x: 0, y: 0 };
      this.minimapBounds = { minX: -400, maxX: 400, minY: -300, maxY: 300, scale: 1, offX: 0, offY: 0 };

      this.motionQuery =
        typeof window !== "undefined" && window.matchMedia
          ? window.matchMedia("(prefers-reduced-motion: reduce)")
          : null;
      this.reducedMotion = Boolean(this.motionQuery && this.motionQuery.matches);

      this._bindEvents();
      this._measureAndResize();
      this._scheduleFrame();
    }

    _bindEvents() {
      this._onResizeObserver = () => {
        this._measureAndResize();
        this.renderNow();
      };

      if (typeof ResizeObserver !== "undefined" && this.container) {
        this.resizeObserver = new ResizeObserver(this._onResizeObserver);
        this.resizeObserver.observe(this.container);
      }
      if (typeof window !== "undefined") {
        window.addEventListener("resize", this._onResizeObserver);
      }

      if (this.motionQuery) {
        this._onMotionChange = (ev) => {
          this.reducedMotion = Boolean(ev.matches);
          this._scheduleFrame();
        };
        if (this.motionQuery.addEventListener) {
          this.motionQuery.addEventListener("change", this._onMotionChange);
        }
      }

      this._onPointerDown = (ev) => {
        if (ev.button !== 0) {
          return;
        }
        this.canvas.setPointerCapture(ev.pointerId);
        this.isPointerPanning = true;
        this.dragMoved = false;
        this.inertia.active = false;
        this.flyAnim = null;
        this.lastPointer = { x: ev.clientX, y: ev.clientY };
        this.dragSamples = [{ x: ev.clientX, y: ev.clientY, t: performance.now() }];
        this.canvas.classList.add("is-panning");
      };

      this._onPointerMove = (ev) => {
        const rect = this.canvas.getBoundingClientRect();
        const sx = ev.clientX - rect.left;
        const sy = ev.clientY - rect.top;

        if (this.isPointerPanning) {
          const dx = ev.clientX - this.lastPointer.x;
          const dy = ev.clientY - this.lastPointer.y;
          if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
            this.dragMoved = true;
          }
          this.camera.x -= dx / this.camera.zoom;
          this.camera.y -= dy / this.camera.zoom;
          this.lastPointer = { x: ev.clientX, y: ev.clientY };
          const now = performance.now();
          this.dragSamples.push({ x: ev.clientX, y: ev.clientY, t: now });
          while (this.dragSamples.length > 6 || (this.dragSamples.length > 2 && now - this.dragSamples[0].t > 120)) {
            this.dragSamples.shift();
          }
          this._notifyViewportChange();
          this._scheduleFrame();
          return;
        }

        const hit = this.hitTestScreen(sx, sy);
        const hitId = hit ? hit.id : null;
        if (hitId !== this.hoveredId) {
          this.hoveredId = hitId;
          this.canvas.classList.toggle("is-hovering", Boolean(hitId));
          this._scheduleFrame();
        }
        if (this.callbacks.onHoverNode) {
          this.callbacks.onHoverNode(hitId, { x: sx, y: sy, node: hit });
        }
      };

      this._onPointerUp = (ev) => {
        if (!this.isPointerPanning) {
          return;
        }
        this.isPointerPanning = false;
        this.canvas.classList.remove("is-panning");
        if (this.canvas.hasPointerCapture && this.canvas.hasPointerCapture(ev.pointerId)) {
          this.canvas.releasePointerCapture(ev.pointerId);
        }

        const rect = this.canvas.getBoundingClientRect();
        const sx = ev.clientX - rect.left;
        const sy = ev.clientY - rect.top;

        if (!this.dragMoved) {
          const hit = this.hitTestScreen(sx, sy);
          if (this.callbacks.onSelectNode) {
            this.callbacks.onSelectNode(hit ? hit.id : null);
          }
          return;
        }

        if (!this.reducedMotion && this.dragSamples.length >= 2) {
          const first = this.dragSamples[0];
          const last = this.dragSamples[this.dragSamples.length - 1];
          const dt = Math.max(12, last.t - first.t);
          const vx = ((last.x - first.x) / dt) * 16;
          const vy = ((last.y - first.y) / dt) * 16;
          if (Math.hypot(vx, vy) > 1.2) {
            this.inertia = {
              vx: -vx / this.camera.zoom,
              vy: -vy / this.camera.zoom,
              active: true,
            };
            this._scheduleFrame();
          }
        }
      };

      this._onPointerLeave = () => {
        if (!this.isPointerPanning && this.hoveredId !== null) {
          this.hoveredId = null;
          this.canvas.classList.remove("is-hovering");
          if (this.callbacks.onHoverNode) {
            this.callbacks.onHoverNode(null, null);
          }
          this._scheduleFrame();
        }
      };

      this._onWheel = (ev) => {
        ev.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        const sx = ev.clientX - rect.left;
        const sy = ev.clientY - rect.top;
        const factor = Math.exp(-ev.deltaY * 0.00115);
        this.zoomAtScreenPoint(factor, sx, sy);
      };

      this.canvas.addEventListener("pointerdown", this._onPointerDown);
      this.canvas.addEventListener("pointermove", this._onPointerMove);
      this.canvas.addEventListener("pointerup", this._onPointerUp);
      this.canvas.addEventListener("pointercancel", this._onPointerUp);
      this.canvas.addEventListener("pointerleave", this._onPointerLeave);
      this.canvas.addEventListener("wheel", this._onWheel, { passive: false });

      if (this.minimapCanvas) {
        this._onMiniDown = (ev) => {
          if (ev.button !== 0) {
            return;
          }
          this.isMinimapDragging = true;
          this.flyAnim = null;
          this.inertia.active = false;
          this.minimapCanvas.setPointerCapture(ev.pointerId);
          this._panFromMinimapPointer(ev);
        };
        this._onMiniMove = (ev) => {
          if (this.isMinimapDragging) {
            this._panFromMinimapPointer(ev);
          }
        };
        this._onMiniUp = (ev) => {
          if (this.isMinimapDragging) {
            this.isMinimapDragging = false;
            if (this.minimapCanvas.hasPointerCapture && this.minimapCanvas.hasPointerCapture(ev.pointerId)) {
              this.minimapCanvas.releasePointerCapture(ev.pointerId);
            }
          }
        };
        this.minimapCanvas.addEventListener("pointerdown", this._onMiniDown);
        this.minimapCanvas.addEventListener("pointermove", this._onMiniMove);
        this.minimapCanvas.addEventListener("pointerup", this._onMiniUp);
        this.minimapCanvas.addEventListener("pointercancel", this._onMiniUp);
      }
    }

    _panFromMinimapPointer(ev) {
      if (!this.minimapCanvas) {
        return;
      }
      const rect = this.minimapCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      const mb = this.minimapBounds;
      if (mb.scale <= 0) {
        return;
      }
      this.camera.x = (mx - mb.offX) / mb.scale + mb.minX;
      this.camera.y = (my - mb.offY) / mb.scale + mb.minY;
      this._notifyViewportChange();
      this._scheduleFrame();
    }

    _measureAndResize() {
      if (!this.container) {
        return;
      }
      const rect = this.container.getBoundingClientRect();
      const w = Math.max(120, Math.round(rect.width));
      const h = Math.max(120, Math.round(rect.height));
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      this.width = w;
      this.height = h;
      this.dpr = dpr;
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);

      if (this.minimapCanvas) {
        const mRect = this.minimapCanvas.getBoundingClientRect();
        const mw = Math.max(120, Math.round(mRect.width || 212));
        const mh = Math.max(80, Math.round(mRect.height || 128));
        this.minimapCanvas.width = Math.round(mw * dpr);
        this.minimapCanvas.height = Math.round(mh * dpr);
      }
      this.needsRedraw = true;
    }

    setTheme(themeName) {
      this.themeName = themeName === "paper" ? "paper" : "obsidian";
      this.palette = THEMES[this.themeName];
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    setGraphData(payload) {
      const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
      const edges = Array.isArray(payload.edges) ? payload.edges : [];
      const layoutResult = payload.layoutResult || { mode: "layers", positions: new Map() };
      const animate = payload.animate !== false && !this.reducedMotion;
      this.repoName = payload.repoName || "";

      this.nodes = nodes;
      this.nodeById = new Map(nodes.map((n) => [n.id, n]));
      this.edges = edges;
      this.layoutResult = layoutResult;
      this.cycleNodeIds = new Set();
      this.violationNodeIds = new Set();

      const pairSet = new Set();
      for (const e of edges) {
        pairSet.add(e.source + "->" + e.target);
        if (e.is_cycle) {
          this.cycleNodeIds.add(e.source);
          this.cycleNodeIds.add(e.target);
        }
        if (e.is_violation) {
          this.violationNodeIds.add(e.source);
          this.violationNodeIds.add(e.target);
        }
      }
      this.edgeBidirectional = new Set();
      for (const e of edges) {
        if (pairSet.has(e.target + "->" + e.source)) {
          this.edgeBidirectional.add(e.source + "->" + e.target);
        }
      }

      const now = performance.now();
      const nextStates = new Map();
      const posSource = layoutResult.positions;

      nodes.forEach((node, index) => {
        const targetPos = readPos(posSource, node.id) || { x: 0, y: 0, radius: 18 };
        const prev = this.nodeStates.get(node.id);
        const startX = prev ? prev.x : targetPos.x;
        const startY = prev ? prev.y : targetPos.y;
        const startR = prev ? prev.radius : Math.max(6, targetPos.radius * 0.6);
        const startOpacity = prev ? prev.opacity : 0.1;
        const stagger = animate ? Math.min(130, index * 4) : 0;

        nextStates.set(node.id, {
          node,
          x: animate ? startX : targetPos.x,
          y: animate ? startY : targetPos.y,
          radius: animate ? startR : targetPos.radius,
          fromX: startX,
          fromY: startY,
          fromR: startR,
          toX: targetPos.x,
          toY: targetPos.y,
          toR: targetPos.radius,
          morphStart: now + stagger,
          morphDuration: animate ? 580 : 0,
          opacity: animate ? startOpacity : 1,
          targetOpacity: 1,
          healthScore: targetPos.healthScore,
          issues: targetPos.issues || [],
          depth: targetPos.depth,
          direction: targetPos.direction,
        });
      });

      this.nodeStates = nextStates;
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    setSelection(nodeId) {
      this.selectedId = nodeId || null;
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    setSpotlight(spotlight) {
      this.spotlight = spotlight || null;
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    setTraceFlow(traceFlow) {
      this.traceFlow = traceFlow || null;
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    setShowCyclesOnly(enabled) {
      this.showCyclesOnly = Boolean(enabled);
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    setShowParticles(enabled) {
      this.showParticles = Boolean(enabled);
      this.needsRedraw = true;
      this._scheduleFrame();
    }

    triggerFileRipples(filePaths) {
      if (!Array.isArray(filePaths) || filePaths.length === 0) {
        return;
      }
      const now = performance.now();
      for (const id of filePaths) {
        if (!this.nodeStates.has(id)) {
          continue;
        }
        this.ripples.push({ nodeId: id, start: now, duration: 1150, delay: 0 });
        if (!this.reducedMotion) {
          this.ripples.push({ nodeId: id, start: now, duration: 1150, delay: 220 });
        }
      }
      this._scheduleFrame();
    }

    zoomAtScreenPoint(factor, sx, sy) {
      const nextZoom = Math.max(0.14, Math.min(3.5, this.camera.zoom * factor));
      const worldX = (sx - this.width / 2) / this.camera.zoom + this.camera.x;
      const worldY = (sy - this.height / 2) / this.camera.zoom + this.camera.y;
      this.camera.zoom = nextZoom;
      this.camera.x = worldX - (sx - this.width / 2) / nextZoom;
      this.camera.y = worldY - (sy - this.height / 2) / nextZoom;
      this.flyAnim = null;
      this._notifyViewportChange();
      this._scheduleFrame();
    }

    zoomBy(factor) {
      this.zoomAtScreenPoint(factor, this.width / 2, this.height / 2);
    }

    flyTo(nodeId, targetZoom, durationMs) {
      const st = this.nodeStates.get(nodeId);
      if (!st) {
        return;
      }
      const tx = st.toX;
      const ty = st.toY;
      const tz = targetZoom !== undefined ? targetZoom : Math.max(this.camera.zoom, 1.05);
      const dur = this.reducedMotion ? 0 : (durationMs !== undefined ? durationMs : 450);

      if (dur <= 0) {
        this.camera.x = tx;
        this.camera.y = ty;
        this.camera.zoom = tz;
        this.flyAnim = null;
        this._notifyViewportChange();
        this.renderNow();
        return;
      }

      this.inertia.active = false;
      this.flyAnim = {
        start: performance.now(),
        duration: dur,
        fromX: this.camera.x,
        fromY: this.camera.y,
        fromZoom: this.camera.zoom,
        toX: tx,
        toY: ty,
        toZoom: tz,
      };
      this._scheduleFrame();
    }

    fitToView(padding, durationMs) {
      const bounds = this._computeWorldBounds();
      if (!bounds) {
        return;
      }
      const pad = padding !== undefined ? padding : 76;
      const topHudClearance = 104;
      const bottomHudClearance = 56;
      const availW = Math.max(180, this.width - pad * 2);
      const availH = Math.max(160, this.height - topHudClearance - bottomHudClearance - pad);
      const spanW = Math.max(140, bounds.maxX - bounds.minX);
      const spanH = Math.max(140, bounds.maxY - bounds.minY);
      const targetZoom = Math.max(0.16, Math.min(1.6, Math.min(availW / spanW, availH / spanH)));
      const centerX = (bounds.minX + bounds.maxX) / 2;
      const centerY = (bounds.minY + bounds.maxY) / 2 - (topHudClearance - bottomHudClearance) / (2 * targetZoom);
      const dur = this.reducedMotion ? 0 : (durationMs !== undefined ? durationMs : 450);

      if (dur <= 0) {
        this.camera.x = centerX;
        this.camera.y = centerY;
        this.camera.zoom = targetZoom;
        this.flyAnim = null;
        this._notifyViewportChange();
        this.renderNow();
        return;
      }

      this.inertia.active = false;
      this.flyAnim = {
        start: performance.now(),
        duration: dur,
        fromX: this.camera.x,
        fromY: this.camera.y,
        fromZoom: this.camera.zoom,
        toX: centerX,
        toY: centerY,
        toZoom: targetZoom,
      };
      this._scheduleFrame();
    }

    _computeWorldBounds() {
      if (this.nodeStates.size === 0) {
        return null;
      }
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (const st of this.nodeStates.values()) {
        const rx = st.node && st.node.isClusterNode ? Math.max(st.toR, CLUSTER_HALF_W) : st.toR;
        const ry = st.node && st.node.isClusterNode ? Math.max(st.toR, CLUSTER_HALF_H) : st.toR;
        minX = Math.min(minX, st.toX - rx - 40);
        maxX = Math.max(maxX, st.toX + rx + 40);
        minY = Math.min(minY, st.toY - ry - 40);
        maxY = Math.max(maxY, st.toY + ry + 40);
      }
      if (this.layoutResult && Array.isArray(this.layoutResult.lanes)) {
        for (const lane of this.layoutResult.lanes) {
          minX = Math.min(minX, lane.x);
          maxX = Math.max(maxX, lane.x + lane.width);
          minY = Math.min(minY, lane.y);
          maxY = Math.max(maxY, lane.y + lane.height);
        }
      }
      if (this.layoutResult && Array.isArray(this.layoutResult.hulls)) {
        for (const hull of this.layoutResult.hulls) {
          if (hull.bounds) {
            minX = Math.min(minX, hull.bounds.minX);
            maxX = Math.max(maxX, hull.bounds.maxX);
            minY = Math.min(minY, hull.bounds.minY);
            maxY = Math.max(maxY, hull.bounds.maxY);
          }
        }
      }
      return { minX, maxX, minY, maxY };
    }

    screenToWorld(sx, sy) {
      return {
        x: (sx - this.width / 2) / this.camera.zoom + this.camera.x,
        y: (sy - this.height / 2) / this.camera.zoom + this.camera.y,
      };
    }

    worldToScreen(wx, wy) {
      return {
        x: (wx - this.camera.x) * this.camera.zoom + this.width / 2,
        y: (wy - this.camera.y) * this.camera.zoom + this.height / 2,
      };
    }

    hitTestScreen(sx, sy) {
      const world = this.screenToWorld(sx, sy);
      let best = null;
      let bestScore = Infinity;
      const hitPad = Math.max(5, 8 / this.camera.zoom);
      for (const st of this.nodeStates.values()) {
        const dx = world.x - st.x;
        const dy = world.y - st.y;
        if (st.node && st.node.isClusterNode) {
          const hw = CLUSTER_HALF_W + hitPad;
          const hh = CLUSTER_HALF_H + hitPad;
          if (Math.abs(dx) <= hw && Math.abs(dy) <= hh) {
            const normDist = Math.hypot(dx / hw, dy / hh);
            if (normDist < bestScore) {
              bestScore = normDist;
              best = st.node;
            }
          }
          continue;
        }
        const dist = Math.hypot(dx, dy);
        const r = st.radius + hitPad;
        if (dist <= r) {
          const normDist = dist / Math.max(1, r);
          if (normDist < bestScore) {
            bestScore = normDist;
            best = st.node;
          }
        }
      }
      return best;
    }

    _notifyViewportChange() {
      if (this.callbacks.onZoomChange) {
        this.callbacks.onZoomChange(this.camera.zoom);
      }
    }

    _scheduleFrame() {
      if (!this.rafId && typeof requestAnimationFrame !== "undefined") {
        this.lastFrameTime = performance.now();
        this.rafId = requestAnimationFrame((ts) => this._tick(ts));
      }
    }

    _tick(now) {
      this.rafId = 0;
      const dt = Math.min(48, Math.max(4, now - this.lastFrameTime));
      this.lastFrameTime = now;

      let keepAnimating = false;

      if (this.flyAnim) {
        const elapsed = now - this.flyAnim.start;
        const t = this.flyAnim.duration <= 0 ? 1 : Math.min(1, elapsed / this.flyAnim.duration);
        const eased = easeOutCubic(t);
        this.camera.x = this.flyAnim.fromX + (this.flyAnim.toX - this.flyAnim.fromX) * eased;
        this.camera.y = this.flyAnim.fromY + (this.flyAnim.toY - this.flyAnim.fromY) * eased;
        this.camera.zoom = this.flyAnim.fromZoom + (this.flyAnim.toZoom - this.flyAnim.fromZoom) * eased;
        this._notifyViewportChange();
        if (t >= 1) {
          this.flyAnim = null;
        } else {
          keepAnimating = true;
        }
      }

      if (this.inertia.active) {
        const scale = dt / 16;
        this.camera.x += this.inertia.vx * scale;
        this.camera.y += this.inertia.vy * scale;
        const decay = Math.pow(0.90, scale);
        this.inertia.vx *= decay;
        this.inertia.vy *= decay;
        if (Math.hypot(this.inertia.vx, this.inertia.vy) * this.camera.zoom < 0.25) {
          this.inertia.active = false;
        } else {
          keepAnimating = true;
        }
      }

      const activeFocusId = this.hoveredId || this.selectedId;
      const hasTrace = Boolean(this.traceFlow && this.traceFlow.highlighted_nodes && this.traceFlow.highlighted_nodes.length > 0);
      const traceSet = hasTrace ? new Set(this.traceFlow.highlighted_nodes) : null;
      const spotTarget = this.spotlight ? this.spotlight.targetId : null;
      const spotUp = this.spotlight ? this.spotlight.upstreamMap : null;
      const spotDown = this.spotlight ? this.spotlight.downstreamMap : null;

      for (const st of this.nodeStates.values()) {
        if (st.morphDuration > 0 && now >= st.morphStart) {
          const t = Math.min(1, (now - st.morphStart) / st.morphDuration);
          const eased = easeOutSpringSoft(t);
          st.x = st.fromX + (st.toX - st.fromX) * eased;
          st.y = st.fromY + (st.toY - st.fromY) * eased;
          st.radius = st.fromR + (st.toR - st.fromR) * eased;
          if (t < 1) {
            keepAnimating = true;
          } else {
            st.morphDuration = 0;
          }
        } else if (st.morphDuration > 0 && now < st.morphStart) {
          keepAnimating = true;
        }

        let desiredOpacity = 1;
        if (this.spotlight) {
          const isMatch =
            st.node.id === spotTarget ||
            (spotUp && spotUp.has(st.node.id)) ||
            (spotDown && spotDown.has(st.node.id));
          desiredOpacity = isMatch ? 1 : 0.12;
        } else if (traceSet) {
          desiredOpacity = traceSet.has(st.node.id) ? 1 : 0.14;
        } else if (this.showCyclesOnly) {
          const isProblem =
            this.cycleNodeIds.has(st.node.id) ||
            this.violationNodeIds.has(st.node.id) ||
            st.node.health_status === "critical" ||
            st.node.health_status === "cycle" ||
            st.node.health_status === "violation" ||
            (st.issues && (st.issues.includes("cycle") || st.issues.includes("violation")));
          desiredOpacity = isProblem ? 1 : 0.16;
        }

        if (Math.abs(st.opacity - desiredOpacity) > 0.01) {
          if (this.reducedMotion) {
            st.opacity = desiredOpacity;
          } else {
            st.opacity += (desiredOpacity - st.opacity) * Math.min(1, dt * 0.014);
            keepAnimating = true;
          }
        } else {
          st.opacity = desiredOpacity;
        }
      }

      if (this.ripples.length > 0) {
        this.ripples = this.ripples.filter((r) => now - r.start < r.duration + r.delay);
        if (this.ripples.length > 0) {
          keepAnimating = true;
        }
      }

      const hasParticleWork =
        this.showParticles &&
        !this.reducedMotion &&
        (hasTrace ||
          Boolean(activeFocusId) ||
          this.edges.some((e) => e.is_cycle || e.is_violation));
      if (hasParticleWork) {
        this.particleClock = (this.particleClock + dt * 0.00055) % 1000;
        keepAnimating = true;
      }

      this.renderNow(now);

      if (keepAnimating) {
        this._scheduleFrame();
      }
    }

    renderNow(now) {
      const timestamp = now !== undefined ? now : performance.now();
      this._renderScene(this.ctx, this.width, this.height, this.dpr, this.camera, timestamp, false);
      this._renderMinimap();
      this.needsRedraw = false;
    }

    exportPngDataUrl() {
      const exportW = Math.max(1600, this.width * 2);
      const exportH = Math.max(960, this.height * 2);
      const offscreen = document.createElement("canvas");
      offscreen.width = exportW;
      offscreen.height = exportH;
      const offCtx = offscreen.getContext("2d");

      const bounds = this._computeWorldBounds();
      let snapCam = { x: this.camera.x, y: this.camera.y, zoom: this.camera.zoom };
      if (bounds) {
        const spanW = Math.max(180, bounds.maxX - bounds.minX);
        const spanH = Math.max(180, bounds.maxY - bounds.minY);
        const zoom = Math.max(0.25, Math.min(2.2, Math.min((exportW - 180) / spanW, (exportH - 180) / spanH)));
        snapCam = {
          x: (bounds.minX + bounds.maxX) / 2,
          y: (bounds.minY + bounds.maxY) / 2,
          zoom,
        };
      }

      // Fill solid theme background first so exported PNG is never transparent
      offCtx.fillStyle = this.palette.bg;
      offCtx.fillRect(0, 0, exportW, exportH);

      this._renderScene(offCtx, exportW, exportH, 1, snapCam, performance.now(), true);

      // Clean architectural footer stamp
      offCtx.save();
      offCtx.fillStyle = this.palette.labelMuted;
      offCtx.font = "600 13px ui-monospace, Menlo, Consolas, monospace";
      const footerLabel = this.repoName
        ? "CodeAtlas Architecture Studio — " + this.repoName
        : "CodeAtlas Architecture Studio";
      offCtx.fillText(footerLabel, 28, exportH - 24);
      offCtx.restore();

      return offscreen.toDataURL("image/png");
    }

    _getNodeColor(st) {
      if (this.layoutResult && this.layoutResult.mode === "health") {
        const score = st.healthScore !== undefined ? st.healthScore : 0;
        if (score <= 0.5) {
          return lerpColor(this.palette.emerald, this.palette.amber, score * 2);
        }
        return lerpColor(this.palette.amber, this.palette.crimson, (score - 0.5) * 2);
      }

      if (this.spotlight && st.node.id !== this.spotlight.targetId) {
        const uDepth = this.spotlight.upstreamMap && this.spotlight.upstreamMap.get(st.node.id);
        const dDepth = this.spotlight.downstreamMap && this.spotlight.downstreamMap.get(st.node.id);
        if (uDepth !== undefined) {
          return uDepth === 1 ? this.palette.upstream1 : this.palette.upstreamDeep;
        }
        if (dDepth !== undefined) {
          return this.palette.downstream;
        }
      }

      const layer = st.node.layer || "shared";
      return this.palette.layers[layer] || this.palette.layers.shared;
    }

    _renderScene(ctx, viewW, viewH, dpr, cam, now, isExport) {
      const p = this.palette;
      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Solid canvas background
      ctx.fillStyle = p.bg;
      ctx.fillRect(0, 0, viewW, viewH);

      this._drawBlueprintGrid(ctx, viewW, viewH, cam);

      ctx.save();
      ctx.translate(viewW / 2, viewH / 2);
      ctx.scale(cam.zoom, cam.zoom);
      ctx.translate(-cam.x, -cam.y);

      if (this.layoutResult) {
        if (this.layoutResult.mode === "layers" && Array.isArray(this.layoutResult.lanes)) {
          this._drawLanes(ctx, this.layoutResult.lanes);
        }
        if (
          (this.layoutResult.mode === "constellation" || this.layoutResult.mode === "health") &&
          Array.isArray(this.layoutResult.hulls)
        ) {
          this._drawHulls(ctx, this.layoutResult.hulls);
        }
        if (this.layoutResult.mode === "blast" && this.layoutResult.rings) {
          this._drawBlastRings(ctx, this.layoutResult.rings);
        }
      }

      this._drawEdges(ctx, cam, now, isExport);
      this._drawRipples(ctx, now);
      this._drawNodes(ctx, cam, isExport);

      ctx.restore();

      this._drawLabels(ctx, viewW, viewH, cam, isExport);
      ctx.restore();
    }

    _drawBlueprintGrid(ctx, viewW, viewH, cam) {
      const p = this.palette;
      let worldStep = 40;
      if (cam.zoom < 0.36) {
        worldStep = 160;
      } else if (cam.zoom < 0.72) {
        worldStep = 80;
      } else if (cam.zoom > 1.8) {
        worldStep = 20;
      }

      const left = cam.x - viewW / (2 * cam.zoom);
      const right = cam.x + viewW / (2 * cam.zoom);
      const top = cam.y - viewH / (2 * cam.zoom);
      const bottom = cam.y + viewH / (2 * cam.zoom);

      const startX = Math.floor(left / worldStep) * worldStep;
      const startY = Math.floor(top / worldStep) * worldStep;
      const majorStep = worldStep * 4;

      ctx.fillStyle = p.gridDot;
      for (let wx = startX; wx <= right; wx += worldStep) {
        const sx = (wx - cam.x) * cam.zoom + viewW / 2;
        const isMajorX = Math.abs(Math.round(wx / majorStep) * majorStep - wx) < 0.01;
        for (let wy = startY; wy <= bottom; wy += worldStep) {
          const sy = (wy - cam.y) * cam.zoom + viewH / 2;
          const isMajorY = Math.abs(Math.round(wy / majorStep) * majorStep - wy) < 0.01;
          if (isMajorX && isMajorY) {
            ctx.strokeStyle = p.gridMajor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(sx - 3.5, sy);
            ctx.lineTo(sx + 3.5, sy);
            ctx.moveTo(sx, sy - 3.5);
            ctx.lineTo(sx, sy + 3.5);
            ctx.stroke();
          } else {
            ctx.fillRect(sx - 0.85, sy - 0.85, 1.7, 1.7);
          }
        }
      }
    }

    _drawLanes(ctx, lanes) {
      const p = this.palette;
      for (const lane of lanes) {
        const layerColor = p.layers[lane.layer] || p.layers.shared;
        ctx.save();
        ctx.fillStyle = p.laneBg;
        ctx.strokeStyle = p.laneBorder;
        ctx.lineWidth = 1;
        roundedRectPath(ctx, lane.x, lane.y, lane.width, lane.height, 14);
        ctx.fill();
        ctx.stroke();

        // Top accent bar
        ctx.fillStyle = colorWithAlpha(layerColor, 0.72);
        roundedRectPath(ctx, lane.x + 12, lane.y + 10, lane.width - 24, 3, 2);
        ctx.fill();

        // Lane Header Pill
        ctx.fillStyle = p.laneHeaderBg;
        ctx.strokeStyle = colorWithAlpha(layerColor, 0.35);
        roundedRectPath(ctx, lane.x + 12, lane.y + 20, lane.width - 24, 28, 8);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = p.labelText;
        ctx.font = "700 11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(lane.label.toUpperCase(), lane.x + 22, lane.y + 34);

        ctx.fillStyle = layerColor;
        ctx.font = "700 11px ui-monospace, Menlo, Consolas, monospace";
        ctx.textAlign = "right";
        ctx.fillText(String(lane.count), lane.x + lane.width - 22, lane.y + 34);
        ctx.restore();
      }
    }

    _drawHulls(ctx, hulls) {
      const p = this.palette;
      for (const hull of hulls) {
        if (!hull.points || hull.points.length === 0) {
          continue;
        }
        const accent = p.layers[hull.primaryLayer] || p.layers.shared;
        ctx.save();
        ctx.fillStyle = colorWithAlpha(accent, this.themeName === "paper" ? 0.06 : 0.08);
        ctx.strokeStyle = colorWithAlpha(accent, 0.36);
        ctx.lineWidth = 1.3;
        ctx.setLineDash([7, 5]);

        if (hull.points.length >= 3) {
          ctx.beginPath();
          const pts = hull.points;
          const midLastX = (pts[pts.length - 1].x + pts[0].x) / 2;
          const midLastY = (pts[pts.length - 1].y + pts[0].y) / 2;
          ctx.moveTo(midLastX, midLastY);
          for (let i = 0; i < pts.length; i += 1) {
            const next = pts[(i + 1) % pts.length];
            const midX = (pts[i].x + next.x) / 2;
            const midY = (pts[i].y + next.y) / 2;
            ctx.quadraticCurveTo(pts[i].x, pts[i].y, midX, midY);
          }
          ctx.closePath();
          ctx.fill();
          ctx.stroke();
        } else {
          ctx.beginPath();
          ctx.arc(hull.center.x, hull.center.y, 70, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }

        ctx.setLineDash([]);
        if (hull.bounds) {
          const labelY = hull.bounds.minY - 12;
          ctx.fillStyle = accent;
          ctx.font = "700 11px ui-monospace, Menlo, Consolas, monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          ctx.fillText(hull.label + " (" + hull.count + ")", hull.center.x, labelY);
        }
        ctx.restore();
      }
    }

    _drawBlastRings(ctx, rings) {
      const p = this.palette;
      ctx.save();
      for (let i = 0; i < rings.radii.length; i += 1) {
        const r = rings.radii[i];
        ctx.strokeStyle = i === 0 ? colorWithAlpha(p.cyan, 0.38) : colorWithAlpha(p.labelMuted, 0.25);
        ctx.lineWidth = 1.2;
        ctx.setLineDash([6, 6]);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.stroke();

        ctx.setLineDash([]);
        ctx.fillStyle = p.labelMuted;
        ctx.font = "600 10px ui-monospace, Menlo, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText("RING " + (i + 1), 0, -r - 6);
      }

      ctx.fillStyle = p.upstream1;
      ctx.font = "700 11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText("UPSTREAM CALLERS", -120, -rings.radii[rings.radii.length - 1] - 24);

      ctx.fillStyle = p.downstream;
      ctx.textAlign = "left";
      ctx.fillText("DOWNSTREAM IMPACT", 120, -rings.radii[rings.radii.length - 1] - 24);
      ctx.restore();
    }

    _drawEdges(ctx, cam, now, isExport) {
      const p = this.palette;
      const activeFocus = this.hoveredId || this.selectedId;
      const traceEdgeDepth = new Map();
      if (this.traceFlow && Array.isArray(this.traceFlow.paths)) {
        for (const path of this.traceFlow.paths) {
          for (let i = 0; i + 1 < path.length; i += 1) {
            const key = path[i] + "->" + path[i + 1];
            if (!traceEdgeDepth.has(key) || i < traceEdgeDepth.get(key)) {
              traceEdgeDepth.set(key, i);
            }
          }
        }
      }
      if (this.traceFlow && Array.isArray(this.traceFlow.highlighted_edges)) {
        for (const pair of this.traceFlow.highlighted_edges) {
          const key = pair[0] + "->" + pair[1];
          if (!traceEdgeDepth.has(key)) {
            traceEdgeDepth.set(key, 0);
          }
        }
      }

      const spotTarget = this.spotlight ? this.spotlight.targetId : null;
      const spotUp = this.spotlight ? this.spotlight.upstreamMap : null;
      const spotDown = this.spotlight ? this.spotlight.downstreamMap : null;
      const edgePills = [];

      for (const edge of this.edges) {
        const src = this.nodeStates.get(edge.source);
        const dst = this.nodeStates.get(edge.target);
        if (!src || !dst) {
          continue;
        }
        const edgeKey = edge.source + "->" + edge.target;
        const isTraceEdge = traceEdgeDepth.has(edgeKey);
        const isConnectedToFocus =
          activeFocus && (edge.source === activeFocus || edge.target === activeFocus);

        let isSpotEdge = false;
        if (this.spotlight) {
          const srcIn = edge.source === spotTarget || (spotUp && spotUp.has(edge.source)) || (spotDown && spotDown.has(edge.source));
          const dstIn = edge.target === spotTarget || (spotUp && spotUp.has(edge.target)) || (spotDown && spotDown.has(edge.target));
          isSpotEdge = srcIn && dstIn;
        }

        let alpha = 0.72;
        if (this.spotlight && !isSpotEdge) {
          alpha = 0.08;
        } else if (traceEdgeDepth.size > 0 && !isTraceEdge) {
          alpha = 0.08;
        } else if (this.showCyclesOnly && !edge.is_cycle && !edge.is_violation) {
          alpha = 0.09;
        } else if (activeFocus && !this.spotlight && traceEdgeDepth.size === 0) {
          alpha = isConnectedToFocus ? 1 : 0.25;
        }

        const isAgg = Boolean(edge.isAggregatedEdge);
        const aggCount = isAgg ? Math.max(1, Number(edge.edgeCount || 1)) : 1;
        const aggBoost = isAgg ? Math.min(2.8, Math.log2(aggCount + 1) * 0.65) : 0;

        let strokeColor = p.edgeDefault;
        let lineWidth = isAgg ? 1.15 + aggBoost : 1.25;
        if (isTraceEdge) {
          strokeColor = p.edgeTrace;
          lineWidth = 2.6 + aggBoost * 0.45;
        } else if (edge.is_cycle) {
          strokeColor = p.edgeCycle;
          lineWidth = 2.2 + aggBoost * 0.45;
        } else if (edge.is_violation) {
          strokeColor = p.edgeViolation;
          lineWidth = 2.0 + aggBoost * 0.45;
        } else if (isConnectedToFocus) {
          strokeColor = edge.source === activeFocus ? p.downstream : p.edgeActive;
          lineWidth = 2.1 + aggBoost * 0.5;
        } else if (isSpotEdge && this.spotlight) {
          strokeColor =
            spotUp && spotUp.has(edge.source)
              ? p.upstream1
              : p.downstream;
          lineWidth = 2.0 + aggBoost * 0.45;
        }

        const dx = dst.x - src.x;
        const dy = dst.y - src.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 1) {
          continue;
        }
        const ux = dx / dist;
        const uy = dy / dist;
        const nx = -uy;
        const ny = ux;

        const isBi = this.edgeBidirectional.has(edgeKey);
        const curveSign = isBi ? 1 : ((edge.source < edge.target) ? 1 : -1);
        const curveOffset = Math.min(58, dist * (isBi ? 0.22 : 0.12)) * curveSign;
        const ctrlX = (src.x + dst.x) / 2 + nx * curveOffset;
        const ctrlY = (src.y + dst.y) / 2 + ny * curveOffset;

        const txEnd = dst.x - ctrlX;
        const tyEnd = dst.y - ctrlY;
        const tLen = Math.hypot(txEnd, tyEnd) || 1;
        const endAx = txEnd / tLen;
        const endAy = tyEnd / tLen;
        const endAnchor = anchorPointOnNode(dst, -endAx, -endAy, 4);
        const endX = endAnchor.x;
        const endY = endAnchor.y;

        const sxStart = ctrlX - src.x;
        const syStart = ctrlY - src.y;
        const sLen = Math.hypot(sxStart, syStart) || 1;
        const startAx = sxStart / sLen;
        const startAy = syStart / sLen;
        const startAnchor = anchorPointOnNode(src, startAx, startAy, 2);
        const startX = startAnchor.x;
        const startY = startAnchor.y;

        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = lineWidth / Math.max(0.65, Math.min(1.25, cam.zoom));
        if (edge.is_violation && !isTraceEdge) {
          ctx.setLineDash([6, 4]);
        }

        ctx.beginPath();
        ctx.moveTo(startX, startY);
        ctx.quadraticCurveTo(ctrlX, ctrlY, endX, endY);
        ctx.stroke();

        // Arrowhead
        ctx.setLineDash([]);
        const arrowLen = Math.max(6.5, 8.5 / Math.max(0.7, Math.min(1.3, cam.zoom)));
        const angle = Math.atan2(endAy, endAx);
        ctx.fillStyle = strokeColor;
        ctx.beginPath();
        ctx.moveTo(endX, endY);
        ctx.lineTo(
          endX - arrowLen * Math.cos(angle - Math.PI / 6),
          endY - arrowLen * Math.sin(angle - Math.PI / 6)
        );
        ctx.lineTo(
          endX - arrowLen * Math.cos(angle + Math.PI / 6),
          endY - arrowLen * Math.sin(angle + Math.PI / 6)
        );
        ctx.closePath();
        ctx.fill();

        // Animated flow particles
        const shouldPulse =
          this.showParticles &&
          !this.reducedMotion &&
          alpha > 0.35 &&
          (edge.is_cycle || isTraceEdge || isConnectedToFocus || isSpotEdge);

        if (shouldPulse) {
          const hopDelay = isTraceEdge ? (traceEdgeDepth.get(edgeKey) || 0) * 0.28 : 0;
          const speed = isTraceEdge ? 1.15 : edge.is_cycle ? 0.85 : 0.95;
          const phaseBase = (now * 0.00075 * speed - hopDelay) % 1;
          const t1 = phaseBase < 0 ? phaseBase + 1 : phaseBase;
          const particles = isTraceEdge || edge.is_cycle ? [t1, (t1 + 0.5) % 1] : [t1];
          for (const pt of particles) {
            const inv = 1 - pt;
            const px = inv * inv * startX + 2 * inv * pt * ctrlX + pt * pt * endX;
            const py = inv * inv * startY + 2 * inv * pt * ctrlY + pt * pt * endY;
            ctx.fillStyle = isTraceEdge
              ? p.cyan
              : edge.is_cycle
                ? p.crimson
                : strokeColor;
            ctx.beginPath();
            ctx.arc(px, py, isTraceEdge ? 4.2 : 3.2, 0, Math.PI * 2);
            ctx.fill();
          }
        }
        ctx.restore();

        if (
          isAgg &&
          alpha > 0.2 &&
          (isConnectedToFocus ||
            isSpotEdge ||
            isTraceEdge ||
            (aggCount >= 3 && (cam.zoom >= 0.55 || isExport)))
        ) {
          const midX = 0.25 * startX + 0.5 * ctrlX + 0.25 * endX;
          const midY = 0.25 * startY + 0.5 * ctrlY + 0.25 * endY;
          edgePills.push({
            x: midX,
            y: midY,
            text: String(aggCount),
            strokeColor,
            alpha: Math.min(1, alpha + 0.18),
            isHighlighted: isConnectedToFocus || isSpotEdge || isTraceEdge || edge.is_cycle || edge.is_violation,
          });
        }
      }

      if (edgePills.length > 0) {
        ctx.save();
        ctx.font = "700 10px ui-monospace, Menlo, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        for (const pill of edgePills) {
          const tw = ctx.measureText(pill.text).width;
          const pw = Math.max(18, tw + 10);
          const ph = 15;
          ctx.globalAlpha = pill.alpha;
          ctx.fillStyle = p.labelBg;
          ctx.strokeStyle = pill.isHighlighted ? pill.strokeColor : colorWithAlpha(p.labelMuted, 0.55);
          ctx.lineWidth = 1;
          roundedRectPath(ctx, pill.x - pw / 2, pill.y - ph / 2, pw, ph, 7.5);
          ctx.fill();
          ctx.stroke();
          ctx.fillStyle = pill.isHighlighted ? pill.strokeColor : p.labelText;
          ctx.fillText(pill.text, pill.x, pill.y + 0.5);
        }
        ctx.restore();
      }
    }

    _drawRipples(ctx, now) {
      if (this.ripples.length === 0) {
        return;
      }
      const p = this.palette;
      for (const rip of this.ripples) {
        const st = this.nodeStates.get(rip.nodeId);
        if (!st) {
          continue;
        }
        const elapsed = now - rip.start - rip.delay;
        if (elapsed < 0 || elapsed > rip.duration) {
          continue;
        }
        const t = elapsed / rip.duration;
        const alpha = (1 - t) * 0.85;
        ctx.save();
        ctx.strokeStyle = colorWithAlpha(p.cyan, alpha);
        ctx.lineWidth = 2.4 * (1 - t * 0.5);
        if (st.node && st.node.isClusterNode) {
          const expand = t * 52;
          roundedRectPath(
            ctx,
            st.x - CLUSTER_HALF_W - expand,
            st.y - CLUSTER_HALF_H - expand,
            CLUSTER_CARD_W + expand * 2,
            CLUSTER_CARD_H + expand * 2,
            10 + expand * 0.25
          );
          ctx.stroke();
        } else {
          const r = st.radius + t * 58;
          ctx.beginPath();
          ctx.arc(st.x, st.y, r, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.restore();
      }
    }

    _drawClusterCard(ctx, st, cam, isExport, stateFlags) {
      const p = this.palette;
      const node = st.node;
      const {
        isSelected,
        isHovered,
        isSpotTarget,
        isTraceNode,
        accent,
        isCycle,
        isCritical,
        isCycleOrViolation,
      } = stateFlags;

      const w = CLUSTER_CARD_W;
      const h = CLUSTER_CARD_H;
      const x = st.x - CLUSTER_HALF_W;
      const y = st.y - CLUSTER_HALF_H;
      const radius = 10;

      // Outer focus halo
      if (isSelected || isHovered || isSpotTarget || isTraceNode) {
        ctx.strokeStyle = isSpotTarget || isSelected ? p.cyan : accent;
        ctx.lineWidth = isSelected || isSpotTarget ? 2.8 : 2;
        roundedRectPath(ctx, x - 5, y - 5, w + 10, h + 10, 14);
        ctx.stroke();
      }

      // Cycle / critical / violation dashed ring
      if (isCycleOrViolation && !isSelected) {
        ctx.strokeStyle = isCycle || isCritical ? p.crimson : p.amber;
        ctx.lineWidth = 1.8;
        ctx.setLineDash([5, 3]);
        roundedRectPath(ctx, x - 3.5, y - 3.5, w + 7, h + 7, 12.5);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Card fill + subtle layer tint
      ctx.fillStyle = p.nodeFill;
      roundedRectPath(ctx, x, y, w, h, radius);
      ctx.fill();

      ctx.fillStyle = colorWithAlpha(accent, this.themeName === "paper" ? 0.06 : 0.11);
      roundedRectPath(ctx, x, y, w, h, radius);
      ctx.fill();

      // Colored left layer accent bar clipped cleanly inside card corner radius
      ctx.save();
      roundedRectPath(ctx, x, y, w, h, radius);
      ctx.clip();
      ctx.fillStyle = accent;
      ctx.fillRect(x, y, 5.5, h);
      ctx.restore();

      // Crisp card border
      ctx.strokeStyle =
        isSelected || isHovered
          ? p.cyan
          : isCycle || isCritical
            ? colorWithAlpha(p.crimson, 0.72)
            : colorWithAlpha(accent, 0.52);
      ctx.lineWidth = isSelected ? 2.4 : 1.5;
      roundedRectPath(ctx, x, y, w, h, radius);
      ctx.stroke();

      // Always keep cluster card titles visible at normal overview zoom levels (hide only if zoom < 0.25)
      if (isExport || cam.zoom >= 0.25 || isSelected || isHovered || isSpotTarget || isTraceNode) {
        const tx = x + 13;
        const rx = x + w - 9;
        const routesCount = Number(node.routesCount || 0);
        const issueCount = Number(node.issueCount || 0);
        const criticalCount = Number(node.criticalCount || 0);

        const pills = [];
        if (issueCount > 0) {
          const issueColor = criticalCount > 0 || isCycle ? p.crimson : p.amber;
          pills.push({
            text: issueCount + (criticalCount > 0 || isCycle ? "!" : "w"),
            color: issueColor,
          });
        }
        if (routesCount > 0) {
          pills.push({
            text: routesCount + " rt",
            color: p.edgeTrace,
          });
        }

        let pillsTotalW = 0;
        if (pills.length > 0) {
          ctx.font = "700 9.5px ui-monospace, Menlo, Consolas, monospace";
          for (const pill of pills) {
            pill.w = Math.max(20, ctx.measureText(pill.text).width + 10);
            pillsTotalW += pill.w + 4;
          }
          pillsTotalW += 2;
        }

        let pillRight = rx;
        for (const pill of pills) {
          const px = pillRight - pill.w;
          const py = y + 11;
          const ph = 16;
          ctx.fillStyle = colorWithAlpha(pill.color, this.themeName === "paper" ? 0.14 : 0.2);
          ctx.strokeStyle = colorWithAlpha(pill.color, 0.65);
          ctx.lineWidth = 1;
          roundedRectPath(ctx, px, py, pill.w, ph, 8);
          ctx.fill();
          ctx.stroke();

          ctx.fillStyle = pill.color;
          ctx.font = "700 9.5px ui-monospace, Menlo, Consolas, monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(pill.text, px + pill.w / 2, py + ph / 2 + 0.5);
          pillRight = px - 4;
        }

        // Bold cluster title truncated cleanly
        const maxTitleW = Math.max(40, rx - tx - pillsTotalW);
        ctx.font = "700 12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillStyle = isSelected || isHovered ? p.cyan : p.labelText;
        const title = truncateCanvasText(ctx, node.name || node.clusterKey || node.id, maxTitleW);
        ctx.fillText(title, tx, y + 20);

        // Subtitle: "28 files · 2.4k LOC"
        const fileCount = Number(node.fileCount || 0);
        const filesLabel = fileCount === 1 ? "1 file" : fileCount + " files";
        const subtitleText = filesLabel + " \u00b7 " + formatCompactLoc(node.loc) + " LOC";
        ctx.font = "500 10.5px ui-monospace, Menlo, Consolas, monospace";
        ctx.fillStyle = p.labelMuted;
        const subTrunc = truncateCanvasText(ctx, subtitleText, w - 22);
        ctx.fillText(subTrunc, tx, y + 46);
      }

      // Blast Radius Hop Badges (-1/-2 or +1/+2) placed at bottom-right edge to avoid top-right status pill
      if (this.spotlight && node.id !== this.spotlight.targetId) {
        const uDepth = this.spotlight.upstreamMap && this.spotlight.upstreamMap.get(node.id);
        const dDepth = this.spotlight.downstreamMap && this.spotlight.downstreamMap.get(node.id);
        let badgeText = "";
        let badgeColor = p.cyan;
        if (uDepth !== undefined && (dDepth === undefined || uDepth <= dDepth)) {
          badgeText = "-" + uDepth;
          badgeColor = uDepth === 1 ? p.upstream1 : p.upstreamDeep;
        } else if (dDepth !== undefined) {
          badgeText = "+" + dDepth;
          badgeColor = p.downstream;
        }
        if (badgeText) {
          const bx = x + w - 10;
          const by = y + h - 4;
          ctx.fillStyle = badgeColor;
          roundedRectPath(ctx, bx - 12, by - 9, 24, 18, 9);
          ctx.fill();
          ctx.fillStyle = this.themeName === "paper" ? "#ffffff" : "#060913";
          ctx.font = "700 10px ui-monospace, Menlo, Consolas, monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(badgeText, bx, by + 0.5);
        }
      }
    }

    _drawNodes(ctx, cam, isExport) {
      const p = this.palette;
      const traceSet =
        this.traceFlow && Array.isArray(this.traceFlow.highlighted_nodes)
          ? new Set(this.traceFlow.highlighted_nodes)
          : null;

      // Sort so highlighted / selected / hovered nodes render cleanly on top
      const ordered = Array.from(this.nodeStates.values()).sort((a, b) => {
        const aFocus = a.node.id === this.selectedId || a.node.id === this.hoveredId ? 1 : 0;
        const bFocus = b.node.id === this.selectedId || b.node.id === this.hoveredId ? 1 : 0;
        return (a.opacity - b.opacity) || (aFocus - bFocus);
      });

      for (const st of ordered) {
        const node = st.node;
        const isSelected = node.id === this.selectedId;
        const isHovered = node.id === this.hoveredId;
        const isSpotTarget = this.spotlight && this.spotlight.targetId === node.id;
        const isTraceNode = traceSet && traceSet.has(node.id);
        const accent = this._getNodeColor(st);
        const isCycle =
          this.cycleNodeIds.has(node.id) ||
          node.health_status === "cycle" ||
          (st.issues && st.issues.includes("cycle"));
        const isViolation =
          this.violationNodeIds.has(node.id) ||
          node.health_status === "violation" ||
          (st.issues && st.issues.includes("violation"));
        const isCritical = node.health_status === "critical";
        const isCycleOrViolation = isCycle || isViolation || isCritical;

        ctx.save();
        ctx.globalAlpha = st.opacity;

        if (node.isClusterNode) {
          this._drawClusterCard(ctx, st, cam, isExport, {
            isSelected,
            isHovered,
            isSpotTarget,
            isTraceNode,
            accent,
            isCycle,
            isCritical,
            isCycleOrViolation,
          });
          ctx.restore();
          continue;
        }

        // Outer focus halo
        if (isSelected || isHovered || isSpotTarget || isTraceNode) {
          ctx.strokeStyle = isSpotTarget || isSelected ? p.cyan : accent;
          ctx.lineWidth = isSelected || isSpotTarget ? 3 : 2;
          ctx.beginPath();
          ctx.arc(st.x, st.y, st.radius + 6, 0, Math.PI * 2);
          ctx.stroke();
        }

        // Problem dashed ring
        if (isCycleOrViolation && !isSelected) {
          ctx.strokeStyle = isCycle || isCritical ? p.crimson : p.amber;
          ctx.lineWidth = 1.6;
          ctx.setLineDash([4, 3]);
          ctx.beginPath();
          ctx.arc(st.x, st.y, st.radius + 4, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Node fill
        ctx.fillStyle = p.nodeFill;
        ctx.beginPath();
        ctx.arc(st.x, st.y, st.radius, 0, Math.PI * 2);
        ctx.fill();

        // Tinted inner ring
        ctx.fillStyle = colorWithAlpha(accent, this.themeName === "paper" ? 0.14 : 0.18);
        ctx.beginPath();
        ctx.arc(st.x, st.y, st.radius - 2, 0, Math.PI * 2);
        ctx.fill();

        // Crisp node stroke
        ctx.strokeStyle = isSelected || isHovered ? p.cyan : accent;
        ctx.lineWidth = isSelected ? 2.6 : 2;
        ctx.beginPath();
        ctx.arc(st.x, st.y, st.radius, 0, Math.PI * 2);
        ctx.stroke();

        // Architectural telemetry arc (complexity indicator)
        if (cam.zoom > 0.45 && Number(node.complexity || 0) > 4) {
          const ratio = Math.min(1, Number(node.complexity || 1) / 35);
          ctx.strokeStyle = ratio > 0.72 ? p.amber : colorWithAlpha(accent, 0.85);
          ctx.lineWidth = 2.4;
          ctx.beginPath();
          ctx.arc(st.x, st.y, st.radius - 4.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * ratio);
          ctx.stroke();
        }

        // Vector emblem inside node (entrypoint arrow / DB disk / hotspot diamond)
        if (cam.zoom > 0.4) {
          ctx.strokeStyle = accent;
          ctx.fillStyle = accent;
          ctx.lineWidth = 1.5;
          if (node.is_entrypoint || (node.routes && node.routes.length > 0)) {
            ctx.beginPath();
            ctx.moveTo(st.x - 3.5, st.y - 4.5);
            ctx.lineTo(st.x + 5, st.y);
            ctx.lineTo(st.x - 3.5, st.y + 4.5);
            ctx.closePath();
            ctx.fill();
          } else if (node.has_db_access) {
            ctx.beginPath();
            ctx.ellipse(st.x, st.y - 3, 5.5, 2.2, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(st.x - 5.5, st.y - 3);
            ctx.lineTo(st.x - 5.5, st.y + 3);
            ctx.ellipse(st.x, st.y + 3, 5.5, 2.2, 0, Math.PI, 0, true);
            ctx.lineTo(st.x + 5.5, st.y - 3);
            ctx.stroke();
          } else {
            ctx.beginPath();
            ctx.arc(st.x, st.y, 3.2, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        // Blast Radius Hop Badges (-1/-2 or +1/+2)
        if (this.spotlight && node.id !== this.spotlight.targetId) {
          const uDepth = this.spotlight.upstreamMap && this.spotlight.upstreamMap.get(node.id);
          const dDepth = this.spotlight.downstreamMap && this.spotlight.downstreamMap.get(node.id);
          let badgeText = "";
          let badgeColor = p.cyan;
          if (uDepth !== undefined && (dDepth === undefined || uDepth <= dDepth)) {
            badgeText = "-" + uDepth;
            badgeColor = uDepth === 1 ? p.upstream1 : p.upstreamDeep;
          } else if (dDepth !== undefined) {
            badgeText = "+" + dDepth;
            badgeColor = p.downstream;
          }
          if (badgeText) {
            const bx = st.x + st.radius * 0.72;
            const by = st.y - st.radius * 0.72;
            ctx.fillStyle = badgeColor;
            roundedRectPath(ctx, bx - 12, by - 9, 24, 18, 9);
            ctx.fill();
            ctx.fillStyle = this.themeName === "paper" ? "#ffffff" : "#060913";
            ctx.font = "700 10px ui-monospace, Menlo, Consolas, monospace";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(badgeText, bx, by + 0.5);
          }
        }

        ctx.restore();
      }
    }

    _drawLabels(ctx, viewW, viewH, cam, isExport) {
      const p = this.palette;
      const zoom = cam.zoom;
      const traceSet =
        this.traceFlow && Array.isArray(this.traceFlow.highlighted_nodes)
          ? new Set(this.traceFlow.highlighted_nodes)
          : null;

      // Priority sorting: hovered/selected > spotlight/trace > problem/entrypoint > fan_in
      const candidates = Array.from(this.nodeStates.values()).filter(
        (st) => st.opacity > 0.25 && !(st.node && st.node.isClusterNode)
      );
      candidates.sort((a, b) => {
        const aForce =
          a.node.id === this.selectedId ||
          a.node.id === this.hoveredId ||
          (this.spotlight && a.node.id === this.spotlight.targetId)
            ? 1
            : 0;
        const bForce =
          b.node.id === this.selectedId ||
          b.node.id === this.hoveredId ||
          (this.spotlight && b.node.id === this.spotlight.targetId)
            ? 1
            : 0;
        if (bForce !== aForce) {
          return bForce - aForce;
        }
        const aSpot = traceSet && traceSet.has(a.node.id) ? 1 : 0;
        const bSpot = traceSet && traceSet.has(b.node.id) ? 1 : 0;
        if (bSpot !== aSpot) {
          return bSpot - aSpot;
        }
        return (Number(b.node.fan_in || 0) + Number(b.node.fan_out || 0)) - (Number(a.node.fan_in || 0) + Number(a.node.fan_out || 0));
      });

      const occupiedBoxes = [];
      ctx.font = "600 11px ui-monospace, Menlo, Consolas, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      for (const st of candidates) {
        const node = st.node;
        const isForce =
          node.id === this.selectedId ||
          node.id === this.hoveredId ||
          (this.spotlight &&
            (node.id === this.spotlight.targetId ||
              (this.spotlight.upstreamMap && this.spotlight.upstreamMap.has(node.id)) ||
              (this.spotlight.downstreamMap && this.spotlight.downstreamMap.has(node.id)))) ||
          (traceSet && traceSet.has(node.id));

        if (!isExport && !isForce && zoom < 0.42) {
          continue;
        }

        const sx = (st.x - cam.x) * zoom + viewW / 2;
        const sy = (st.y - cam.y) * zoom + viewH / 2 + st.radius * zoom + 13;

        if (sx < -80 || sx > viewW + 80 || sy < -40 || sy > viewH + 40) {
          continue;
        }

        const label = node.name || node.id;
        const textWidth = ctx.measureText(label).width;
        const boxW = textWidth + 12;
        const boxH = 19;
        const boxLeft = sx - boxW / 2;
        const boxTop = sy - boxH / 2;

        // LOD collision culling
        if (!isForce) {
          let overlaps = false;
          for (const box of occupiedBoxes) {
            if (
              boxLeft < box.right + 4 &&
              boxLeft + boxW > box.left - 4 &&
              boxTop < box.bottom + 2 &&
              boxTop + boxH > box.top - 2
            ) {
              overlaps = true;
              break;
            }
          }
          if (overlaps) {
            continue;
          }
        }

        occupiedBoxes.push({
          left: boxLeft,
          right: boxLeft + boxW,
          top: boxTop,
          bottom: boxTop + boxH,
        });

        ctx.save();
        ctx.globalAlpha = Math.min(1, st.opacity + 0.12);
        ctx.fillStyle = p.labelBg;
        roundedRectPath(ctx, boxLeft, boxTop, boxW, boxH, 5);
        ctx.fill();

        ctx.fillStyle = isForce ? p.cyan : p.labelText;
        ctx.fillText(label, sx, sy + 0.5);
        ctx.restore();
      }
    }

    _renderMinimap() {
      if (!this.miniCtx || !this.minimapCanvas) {
        return;
      }
      const p = this.palette;
      const ctx = this.miniCtx;
      const dpr = this.dpr;
      const w = this.minimapCanvas.width / dpr;
      const h = this.minimapCanvas.height / dpr;

      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = p.minimapBg;
      ctx.fillRect(0, 0, w, h);

      const bounds = this._computeWorldBounds();
      if (!bounds) {
        ctx.restore();
        return;
      }

      const pad = 12;
      const spanW = Math.max(120, bounds.maxX - bounds.minX);
      const spanH = Math.max(120, bounds.maxY - bounds.minY);
      const scale = Math.min((w - pad * 2) / spanW, (h - pad * 2) / spanH);
      const offX = (w - spanW * scale) / 2;
      const offY = (h - spanH * scale) / 2;

      this.minimapBounds = {
        minX: bounds.minX,
        maxX: bounds.maxX,
        minY: bounds.minY,
        maxY: bounds.maxY,
        scale,
        offX,
        offY,
      };

      // Edges in minimap
      ctx.strokeStyle = p.edgeDefault;
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      for (const e of this.edges) {
        const s = this.nodeStates.get(e.source);
        const t = this.nodeStates.get(e.target);
        if (!s || !t) {
          continue;
        }
        ctx.moveTo((s.x - bounds.minX) * scale + offX, (s.y - bounds.minY) * scale + offY);
        ctx.lineTo((t.x - bounds.minX) * scale + offX, (t.y - bounds.minY) * scale + offY);
      }
      ctx.stroke();

      // Nodes in minimap
      for (const st of this.nodeStates.values()) {
        const mx = (st.x - bounds.minX) * scale + offX;
        const my = (st.y - bounds.minY) * scale + offY;
        ctx.globalAlpha = Math.max(0.25, st.opacity);
        ctx.fillStyle = this._getNodeColor(st);
        if (st.node && st.node.isClusterNode) {
          const mw = Math.max(5, CLUSTER_CARD_W * scale);
          const mh = Math.max(3, CLUSTER_CARD_H * scale);
          ctx.fillRect(mx - mw / 2, my - mh / 2, mw, mh);
        } else {
          ctx.beginPath();
          ctx.arc(mx, my, Math.max(2, st.radius * scale * 0.9), 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;

      // Active camera viewport rect
      const viewLeft = this.camera.x - this.width / (2 * this.camera.zoom);
      const viewTop = this.camera.y - this.height / (2 * this.camera.zoom);
      const viewWorldW = this.width / this.camera.zoom;
      const viewWorldH = this.height / this.camera.zoom;

      const rx = (viewLeft - bounds.minX) * scale + offX;
      const ry = (viewTop - bounds.minY) * scale + offY;
      const rw = viewWorldW * scale;
      const rh = viewWorldH * scale;

      ctx.strokeStyle = p.minimapViewport;
      ctx.fillStyle = colorWithAlpha(p.cyan, 0.12);
      ctx.lineWidth = 1.4;
      ctx.fillRect(rx, ry, rw, rh);
      ctx.strokeRect(rx, ry, rw, rh);

      ctx.restore();
    }

    destroy() {
      if (this.rafId) {
        cancelAnimationFrame(this.rafId);
        this.rafId = 0;
      }
      if (this.resizeObserver) {
        this.resizeObserver.disconnect();
      }
      if (typeof window !== "undefined") {
        window.removeEventListener("resize", this._onResizeObserver);
      }
      this.canvas.removeEventListener("pointerdown", this._onPointerDown);
      this.canvas.removeEventListener("pointermove", this._onPointerMove);
      this.canvas.removeEventListener("pointerup", this._onPointerUp);
      this.canvas.removeEventListener("pointercancel", this._onPointerUp);
      this.canvas.removeEventListener("pointerleave", this._onPointerLeave);
      this.canvas.removeEventListener("wheel", this._onWheel);
      if (this.minimapCanvas) {
        this.minimapCanvas.removeEventListener("pointerdown", this._onMiniDown);
        this.minimapCanvas.removeEventListener("pointermove", this._onMiniMove);
        this.minimapCanvas.removeEventListener("pointerup", this._onMiniUp);
        this.minimapCanvas.removeEventListener("pointercancel", this._onMiniUp);
      }
    }
  }

  if (typeof window !== "undefined") {
    window.CodeAtlasCanvas = CodeAtlasCanvas;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = CodeAtlasCanvas;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
