/*
 * inspect.js
 *
 * Unified JS module for two contexts:
 *   - Source detail page (per-document tabs)
 *   - Corpus inspect pages (graphrag, raptor, ontology)
 *
 * Which mode it runs in is determined by which window globals are set:
 *   window.KNOWLEDGE_AGENT_CONFIG  → per-document tabbed page
 *   window.KNOWLEDGE_INSPECT_CONFIG → corpus inspect page
 *
 * Vanilla JS only — no framework. Cytoscape is loaded from CDN by the
 * page templates that need it.
 */

(function () {
    "use strict";

    // ===================================================================
    // Common helpers
    // ===================================================================

    function getCsrfToken() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : "";
    }

    function fetchJSON(url, options) {
        options = options || {};
        options.headers = Object.assign(
            { "Content-Type": "application/json", "Accept": "application/json" },
            options.headers || {},
        );
        if (options.method && options.method !== "GET") {
            options.headers["X-CSRFToken"] = getCsrfToken();
            options.credentials = "same-origin";
        }
        return fetch(url, options).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status + " on " + url);
            return r.json();
        });
    }

    function el(tag, attrs, children) {
        const e = document.createElement(tag);
        if (attrs) {
            for (const k in attrs) {
                if (k === "class") e.className = attrs[k];
                else if (k === "html") e.innerHTML = attrs[k];
                else if (k.startsWith("on") && typeof attrs[k] === "function")
                    e.addEventListener(k.slice(2), attrs[k]);
                else e.setAttribute(k, attrs[k]);
            }
        }
        if (children) {
            (Array.isArray(children) ? children : [children]).forEach(function (c) {
                if (c == null) return;
                e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
            });
        }
        return e;
    }

    function htmlEscape(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function colorForType(entityType) {
        // Stable hash → HSL color so the same type renders the same color
        // across reloads. Reasonably distinct for typical entity vocabularies.
        let hash = 0;
        for (let i = 0; i < entityType.length; i++)
            hash = (hash * 31 + entityType.charCodeAt(i)) | 0;
        return "hsl(" + (Math.abs(hash) % 360) + ", 65%, 55%)";
    }

    function showError(host, msg) {
        host.innerHTML = "";
        host.appendChild(el("div", { class: "tab-error" }, "Error: " + msg));
    }

    // ===================================================================
    // GRAPH (cytoscape) — shared between per-doc + corpus modes
    // ===================================================================

    function renderGraph(canvas, data, opts) {
        opts = opts || {};
        if (typeof cytoscape !== "function") {
            showError(canvas, "Cytoscape failed to load");
            return null;
        }
        canvas.innerHTML = "";

        const cy = cytoscape({
            container: canvas,
            elements: {
                nodes: data.nodes || [],
                edges: data.edges || [],
            },
            style: [
                {
                    selector: "node",
                    style: {
                        "label": "data(label)",
                        "background-color": function (e) {
                            return colorForType(e.data("entity_type") || "Unknown");
                        },
                        "color": "#222",
                        "text-outline-color": "#fff",
                        "text-outline-width": 2,
                        "font-size": "10px",
                        "width": function (e) {
                            return 14 + Math.min(20, (e.data("ref_count") || 0));
                        },
                        "height": function (e) {
                            return 14 + Math.min(20, (e.data("ref_count") || 0));
                        },
                    },
                },
                {
                    selector: "edge",
                    style: {
                        "label": "data(label)",
                        "width": 1.5,
                        "line-color": "#bbb",
                        "target-arrow-color": "#bbb",
                        "target-arrow-shape": "triangle",
                        "curve-style": "bezier",
                        "font-size": "8px",
                        "color": "#666",
                        "text-rotation": "autorotate",
                        "text-margin-y": -6,
                    },
                },
                {
                    selector: "node:selected",
                    style: {
                        "border-width": 3,
                        "border-color": "#1976d2",
                    },
                },
            ],
            layout: {
                name: "cose",
                animate: false,
                fit: true,
                padding: 40,
                idealEdgeLength: 80,
                nodeRepulsion: 8000,
            },
            wheelSensitivity: 0.2,
        });

        if (opts.onSelectNode) cy.on("tap", "node", opts.onSelectNode);
        return cy;
    }

    function buildTypeFilterChips(chipHost, data, applyFn) {
        chipHost.innerHTML = "";
        const types = {};
        (data.nodes || []).forEach(function (n) {
            const t = n.data.entity_type || "Unknown";
            types[t] = (types[t] || 0) + 1;
        });
        const sorted = Object.keys(types).sort(function (a, b) {
            return types[b] - types[a];
        });
        sorted.forEach(function (t) {
            const chip = el("button", {
                class: "filter-chip active",
                "data-type": t,
                style: "border-color:" + colorForType(t),
            });
            chip.innerHTML =
                '<span class="dot" style="background:' + colorForType(t) + '"></span>'
                + htmlEscape(t) + ' <span class="ct">' + types[t] + '</span>';
            chip.addEventListener("click", function () {
                chip.classList.toggle("active");
                applyFn(activeTypeSet(chipHost));
            });
            chipHost.appendChild(chip);
        });
    }

    function activeTypeSet(chipHost) {
        const active = new Set();
        chipHost.querySelectorAll(".filter-chip.active").forEach(function (c) {
            active.add(c.getAttribute("data-type"));
        });
        return active;
    }

    function applyTypeFilter(cy, activeTypes) {
        cy.batch(function () {
            cy.nodes().forEach(function (n) {
                const t = n.data("entity_type") || "Unknown";
                n.style("display", activeTypes.has(t) ? "element" : "none");
            });
            cy.edges().forEach(function (e) {
                const visible =
                    e.source().style("display") !== "none" &&
                    e.target().style("display") !== "none";
                e.style("display", visible ? "element" : "none");
            });
        });
    }

    function showEntityDetail(detailHost, entityNode, entitySourcesUrlTpl) {
        const d = entityNode.data();
        detailHost.innerHTML = "";
        detailHost.appendChild(el("div", { html:
            '<div class="entity-detail">' +
            '<div class="entity-label">' + htmlEscape(d.label) + '</div>' +
            '<div class="entity-type">' +
            '<span class="dot" style="background:' + colorForType(d.entity_type || "Unknown") + '"></span>' +
            htmlEscape(d.entity_type || "Unknown") +
            '</div>' +
            (d.description ? '<p class="entity-desc">' + htmlEscape(d.description) + '</p>' : '') +
            '<div class="entity-refs">' + (d.ref_count || 0) + ' relationship(s)</div>' +
            '<div class="entity-sources-host"><em>Loading source documents…</em></div>' +
            '</div>'
        }));
        if (!entitySourcesUrlTpl) return;
        const url = entitySourcesUrlTpl.replace("__ENTITY_ID__", encodeURIComponent(d.id));
        fetchJSON(url).then(function (r) {
            const host = detailHost.querySelector(".entity-sources-host");
            if (!host) return;
            if (!r.sources || !r.sources.length) {
                host.innerHTML = '<em>No source documents tracked.</em>';
                return;
            }
            const ul = document.createElement("ul");
            ul.className = "entity-sources-list";
            r.sources.forEach(function (s) {
                const li = document.createElement("li");
                const a = document.createElement("a");
                a.href = "/knowledge/documents/" + s.source_slug + "/";
                a.textContent = (s.source_title || s.source_slug) +
                    " (" + s.chunk_count + " contributing chunk(s))";
                li.appendChild(a);
                ul.appendChild(li);
            });
            host.innerHTML = "";
            host.appendChild(ul);
        }).catch(function (e) {
            const host = detailHost.querySelector(".entity-sources-host");
            if (host) host.innerHTML = '<em>Could not load sources: ' + htmlEscape(e.message) + '</em>';
        });
    }

    // ===================================================================
    // RAPTOR tree renderer (vanilla DOM — collapsible)
    // ===================================================================

    function renderRaptorTree(canvas, data, opts) {
        opts = opts || {};
        canvas.innerHTML = "";
        if (!data.nodes || !data.nodes.length) {
            canvas.appendChild(el("div", { class: "empty-state" },
                "No RAPTOR nodes for this scope."));
            return;
        }
        // Build index + adjacency
        const byId = {};
        data.nodes.forEach(function (n) { byId[n.id] = n; n.parents = []; });
        // children_ids points DOWN, so map child→parents by inversion
        const childrenOf = {};
        data.nodes.forEach(function (n) {
            childrenOf[n.id] = (n.children_ids || []).filter(function (cid) {
                return byId[cid];
            });
            childrenOf[n.id].forEach(function (cid) { byId[cid].parents.push(n.id); });
        });
        // Roots = nodes with no parent within our scope
        const roots = data.nodes
            .filter(function (n) { return !n.parents.length; })
            .sort(function (a, b) { return b.level - a.level; });

        function renderNode(n, container) {
            const wrapper = el("div", { class: "raptor-node-wrap" });
            const isLeaf = n.level === 0;
            const card = el("div", {
                class: "raptor-node " + (isLeaf ? "raptor-leaf" : "raptor-summary")
                       + (n.is_source_leaf ? " raptor-source-leaf" : ""),
                "data-node-id": n.id,
            });
            const header = el("div", { class: "raptor-node-header" }, [
                el("span", { class: "raptor-level-tag" }, "L" + n.level),
                el("span", { class: "raptor-preview" },
                   (n.content || "").slice(0, 140) + ((n.content || "").length > 140 ? "…" : "")),
                el("span", { class: "raptor-children-count" },
                   (childrenOf[n.id] || []).length + " child(ren)"),
            ]);
            header.addEventListener("click", function (ev) {
                ev.stopPropagation();
                wrapper.classList.toggle("expanded");
                if (opts.onSelectNode) opts.onSelectNode(n);
            });
            card.appendChild(header);
            wrapper.appendChild(card);

            const kids = childrenOf[n.id] || [];
            if (kids.length) {
                const kidsHost = el("div", { class: "raptor-children" });
                kids.forEach(function (cid) {
                    renderNode(byId[cid], kidsHost);
                });
                wrapper.appendChild(kidsHost);
            }
            container.appendChild(wrapper);
        }

        roots.forEach(function (root) { renderNode(root, canvas); });
    }

    function showRaptorDetail(detailHost, node) {
        detailHost.innerHTML =
            '<div class="raptor-node-detail">' +
            '<div><strong>Level:</strong> ' + node.level + '</div>' +
            '<div><strong>Node ID:</strong> <code>' + htmlEscape(node.id) + '</code></div>' +
            (node.metadata && node.metadata.source_slug
                ? '<div><strong>Source:</strong> ' + htmlEscape(node.metadata.source_slug) + '</div>'
                : '') +
            '<div><strong>Content:</strong></div>' +
            '<pre class="file-content-viewer">' + htmlEscape(node.content || '') + '</pre>' +
            '</div>';
        // The tree can grow taller than the viewport once expanded, which
        // pushes this panel below the fold — bring it into view so a click
        // on a deep node visibly "opens" it instead of only toggling
        // expansion. block:"nearest" is a no-op when already visible.
        if (detailHost.scrollIntoView) {
            detailHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }

    // ===================================================================
    // ONTOLOGY renderer
    // ===================================================================

    function renderOntologySchema(host, schema) {
        if (!schema) {
            host.innerHTML = "<em>No schema declared for this tenant.</em>";
            return;
        }
        const lines = [];
        if (schema.namespace)
            lines.push('<dt>Namespace</dt><dd><code>' + htmlEscape(schema.namespace) + '</code></dd>');
        if (schema.prefix)
            lines.push('<dt>Prefix</dt><dd><code>' + htmlEscape(schema.prefix) + ':</code></dd>');
        lines.push('<dt>Classes (' + (schema.classes || []).length + ')</dt><dd>'
            + (schema.classes || []).map(function (c) {
                return '<span class="onto-token" style="background:' + colorForType(c) + ';color:#fff">'
                       + htmlEscape(c) + '</span>';
            }).join(" ") + '</dd>');
        lines.push('<dt>Object Properties (' + (schema.object_properties || []).length + ')</dt><dd>'
            + (schema.object_properties || []).map(function (p) {
                return '<span class="onto-token">' + htmlEscape(p) + '</span>';
            }).join(" ") + '</dd>');
        if ((schema.data_properties || []).length) {
            lines.push('<dt>Data Properties (' + (schema.data_properties || []).length + ')</dt><dd>'
                + schema.data_properties.map(function (p) {
                    return '<span class="onto-token onto-data">' + htmlEscape(p) + '</span>';
                }).join(" ") + '</dd>');
        }
        if (schema.relationship_typing && Object.keys(schema.relationship_typing).length) {
            const typings = Object.entries(schema.relationship_typing).map(function (pair) {
                const rel = pair[0]; const t = pair[1];
                const dom = Array.isArray(t) ? t[0] : (t && t.domain);
                const rng = Array.isArray(t) ? t[1] : (t && t.range);
                return '<li><code>' + htmlEscape(rel) + '</code>: '
                       + htmlEscape(dom || '?') + ' → ' + htmlEscape(rng || '?') + '</li>';
            }).join("");
            lines.push('<dt>Relationship typing</dt><dd><ul class="onto-typings">' + typings + '</ul></dd>');
        }
        host.innerHTML = '<dl class="ontology-schema">' + lines.join("") + '</dl>';
    }

    function renderOntologyIndividuals(host, payload) {
        if (!payload.ontology_built) {
            host.innerHTML =
                '<div class="empty-state"><h4>Ontology not built yet</h4>' +
                '<p>Run the build_ontology pipeline step to create individuals.</p></div>';
            return;
        }
        const individuals = payload.individuals || {};
        const classes = Object.keys(individuals).sort();
        if (!classes.length) {
            host.innerHTML = '<em>No individuals classified yet.</em>';
            return;
        }
        host.innerHTML = "";
        classes.forEach(function (cls) {
            const sect = el("section", { class: "onto-class-group" });
            sect.appendChild(el("h4", null, [
                el("span", {
                    class: "onto-token",
                    style: "background:" + colorForType(cls) + ";color:#fff",
                }, cls),
                el("span", { class: "ct" }, " " + individuals[cls].length + " individual(s)"),
            ]));
            const ul = el("ul", { class: "onto-individuals" });
            individuals[cls].slice(0, 100).forEach(function (name) {
                ul.appendChild(el("li", null, name));
            });
            if (individuals[cls].length > 100) {
                ul.appendChild(el("li", { class: "more" },
                    "… and " + (individuals[cls].length - 100) + " more"));
            }
            sect.appendChild(ul);
            host.appendChild(sect);
        });
    }

    function renderOntologyPerSourcePane(host, payload) {
        if (!payload.ontology_built) {
            host.innerHTML = '<em>Ontology not built yet.</em>';
            return;
        }
        host.innerHTML =
            '<dl class="ontology-contribution">' +
            '<dt>This source contributed</dt>' +
            '<dd>' + (payload.contributing_chunk_count || 0) + ' chunk(s) of '
                  + (payload.source_chunk_total || 0) + ' total</dd>' +
            '<dt>Status</dt>' +
            '<dd>' + (payload.source_contributed
                ? '<span class="status-pill status-completed">Contributed to corpus ontology</span>'
                : '<span class="status-pill status-pending">No contribution detected</span>') + '</dd>' +
            '</dl>' +
            '<p class="hint">The ontology graph is corpus-wide and cannot be partitioned ' +
            'per source. The "Inspect" button on the Strategies page shows the full graph.</p>';
    }

    // ===================================================================
    // PER-DOC tabs (only runs on source detail page)
    // ===================================================================

    function initSourceDetail(cfg) {
        if (!cfg) return;
        wireTabs();
        wireQueryComposer(cfg);
    }

    function wireTabs() {
        document.querySelectorAll(".tab-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                const tab = btn.getAttribute("data-tab");
                document.querySelectorAll(".tab-btn").forEach(function (b) {
                    b.classList.toggle("active", b === btn);
                });
                document.querySelectorAll(".tab-panel").forEach(function (p) {
                    p.classList.toggle("active",
                        p.getAttribute("data-tab") === tab);
                });
                lazyLoadTab(tab);
            });
        });
    }

    function lazyLoadTab(tab) {
        const cfg = window.KNOWLEDGE_AGENT_CONFIG;
        const panel = document.querySelector('.tab-panel[data-tab="' + tab + '"]');
        if (!panel || panel.getAttribute("data-loaded") === "true") return;
        panel.setAttribute("data-loaded", "true");

        if (tab === "contextual") loadContextual(panel, cfg);
        else if (tab === "parent-child") loadParentChild(panel, cfg);
        else if (tab === "graphrag") loadGraph(panel, cfg);
        else if (tab === "raptor") loadRaptor(panel, cfg);
        else if (tab === "ontology") loadOntology(panel, cfg);
    }

    function loadContextual(panel, cfg) {
        const list = panel.querySelector(".contextual-list");
        const loading = panel.querySelector(".tab-loading");
        fetchJSON(cfg.urls.contextual + "?page=1&per_page=25").then(function (data) {
            loading.style.display = "none";
            const rows = data.contextual_chunks || [];
            if (!rows.length) {
                list.innerHTML = '<div class="empty-state"><h3>No contextual chunks</h3>' +
                    '<p>Run the build_contextual ingestion step to generate them.</p></div>';
                return;
            }
            list.innerHTML = "";
            rows.forEach(function (c) {
                const card = el("div", { class: "contextual-card" });
                card.innerHTML =
                    '<div class="contextual-header">' +
                    '<span class="mono">' + htmlEscape(c.id.slice(0, 12)) + '</span> ' +
                    '<span class="label-sm">base chunk</span> ' +
                    '<code>' + htmlEscape((c.base_chunk_id || "").slice(0, 12)) + '</code>' +
                    '</div>' +
                    '<div class="contextual-prefix">' +
                    '<span class="label-sm">LLM-generated context</span>' +
                    '<pre>' + htmlEscape(c.context_prefix) + '</pre></div>' +
                    '<details><summary>Show enriched content</summary>' +
                    '<pre class="file-content-viewer">' + htmlEscape(c.enriched_content) + '</pre>' +
                    '</details>';
                list.appendChild(card);
            });
        }).catch(function (e) { showError(panel, e.message); });
    }

    function loadParentChild(panel, cfg) {
        const tree = panel.querySelector(".pc-tree");
        const loading = panel.querySelector(".tab-loading");
        fetchJSON(cfg.urls.parentChild).then(function (data) {
            loading.style.display = "none";
            const parents = data.parents || [];
            if (!parents.length) {
                tree.innerHTML = '<div class="empty-state"><h3>No parent/child chunks</h3>' +
                    '<p>Run the build_parent_child ingestion step to generate them.</p></div>';
                return;
            }
            tree.innerHTML = "";
            parents.forEach(function (p) {
                const wrap = el("details", { class: "pc-parent" });
                wrap.appendChild(el("summary", null,
                    "Parent " + p.id.slice(0, 12) + " — "
                    + (p.children.length) + " child(ren) — "
                    + (p.content || "").slice(0, 80) + "…"));
                wrap.appendChild(el("pre",
                    { class: "file-content-viewer pc-parent-content" }, p.content));
                p.children.forEach(function (c) {
                    const childWrap = el("details", { class: "pc-child" });
                    childWrap.appendChild(el("summary", null,
                        "Child " + c.id.slice(0, 12) + " — "
                        + (c.content || "").slice(0, 60) + "…"));
                    childWrap.appendChild(el("pre",
                        { class: "file-content-viewer" }, c.content));
                    wrap.appendChild(childWrap);
                });
                tree.appendChild(wrap);
            });
        }).catch(function (e) { showError(panel, e.message); });
    }

    function loadGraph(panel, cfg) {
        const canvas = panel.querySelector(".graph-canvas");
        const toolbar = panel.querySelector(".graph-toolbar");
        const detail = panel.querySelector(".graph-detail-body");
        const loading = panel.querySelector(".tab-loading");
        const stats = panel.querySelector(".graph-stats");
        fetchJSON(cfg.urls.graph).then(function (data) {
            loading.style.display = "none";
            toolbar.classList.remove("hidden");
            stats.textContent = (data.nodes || []).length + " nodes, "
                + (data.edges || []).length + " edges"
                + (data.truncated ? " (truncated)" : "");
            const cy = renderGraph(canvas, data, {
                onSelectNode: function (evt) {
                    showEntityDetail(detail, evt.target, cfg.urls.entitySources);
                },
            });
            const chips = panel.querySelector(".filter-chips");
            if (cy) {
                buildTypeFilterChips(chips, data, function (active) {
                    applyTypeFilter(cy, active);
                });
            }
        }).catch(function (e) { showError(panel, e.message); });
    }

    function loadRaptor(panel, cfg) {
        const canvas = panel.querySelector(".raptor-tree");
        const controls = panel.querySelector(".raptor-controls");
        const loading = panel.querySelector(".tab-loading");
        const stats = panel.querySelector(".raptor-stats");
        const depthInput = panel.querySelector("#raptor-max-depth");
        const reloadBtn = panel.querySelector("#raptor-reload");

        function load() {
            const depth = parseInt(depthInput.value, 10) || 3;
            fetchJSON(cfg.urls.raptor + "?max_depth=" + depth).then(function (data) {
                loading.style.display = "none";
                controls.classList.remove("hidden");
                stats.textContent = (data.nodes || []).length + " nodes, max depth "
                                  + (data.max_depth_reached || 0);
                renderRaptorTree(canvas, data, {
                    onSelectNode: function (n) {
                        const detail = panel.querySelector(".raptor-detail-body");
                        if (detail) showRaptorDetail(detail, n);
                    },
                });
            }).catch(function (e) { showError(canvas, e.message); });
        }
        reloadBtn.addEventListener("click", load);
        load();
    }

    function loadOntology(panel, cfg) {
        const loading = panel.querySelector(".tab-loading");
        const grid = panel.querySelector(".ontology-grid");
        const schemaBody = panel.querySelector(".ontology-schema-body");
        const sourceBody = panel.querySelector(".ontology-source-body");
        fetchJSON(cfg.urls.ontology).then(function (data) {
            loading.style.display = "none";
            grid.classList.remove("hidden");
            renderOntologySchema(schemaBody, data.schema);
            renderOntologyPerSourcePane(sourceBody, data);
        }).catch(function (e) { showError(panel, e.message); });
    }

    function wireQueryComposer(cfg) {
        const form = document.getElementById("query-composer");
        const stratSel = document.getElementById("query-strategy");
        const resultsHost = document.getElementById("query-results");
        if (!form || !stratSel) return;
        (cfg.allStrategies || []).forEach(function (s) {
            const opt = document.createElement("option");
            opt.value = s; opt.textContent = s;
            if (s === cfg.activeStrategy) opt.selected = true;
            stratSel.appendChild(opt);
        });
        form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            const q = document.getElementById("query-text").value.trim();
            if (!q) return;
            const strategy = stratSel.value;
            const top_k = parseInt(document.getElementById("query-topk").value, 10) || 5;
            const restrict = document.getElementById("query-restrict").checked;
            resultsHost.innerHTML = '<div class="tab-loading">Searching…</div>';
            fetchJSON(cfg.urls.query, {
                method: "POST",
                body: JSON.stringify({ query: q, strategy: strategy, top_k: top_k,
                                       restrict_to_doc: restrict }),
            }).then(function (data) {
                if (data.error) {
                    resultsHost.innerHTML = '<div class="tab-error">' + htmlEscape(data.error) + '</div>';
                    return;
                }
                const results = data.results || [];
                if (!results.length) {
                    resultsHost.innerHTML = '<em>No results for strategy=' + strategy + '.</em>';
                    return;
                }
                resultsHost.innerHTML = '<h4>' + results.length + ' result(s) — strategy=' + htmlEscape(strategy) + '</h4>';
                results.forEach(function (r, i) {
                    const card = el("div", { class: "query-result-card" });
                    card.innerHTML =
                        '<div class="result-rank">#' + (i + 1) + '</div>' +
                        '<div class="result-score">score ' + (r.score || 0).toFixed(3) + '</div>' +
                        '<pre class="file-content-viewer">' + htmlEscape(r.content) + '</pre>';
                    resultsHost.appendChild(card);
                });
            }).catch(function (e) {
                resultsHost.innerHTML = '<div class="tab-error">' + htmlEscape(e.message) + '</div>';
            });
        });
    }

    // ===================================================================
    // CORPUS INSPECT pages
    // ===================================================================

    function initInspect(cfg) {
        if (!cfg) return;
        if (cfg.urls.graph) initCorpusGraph(cfg);
        if (cfg.urls.raptor) initCorpusRaptor(cfg);
        if (cfg.urls.ontology) initCorpusOntology(cfg);
    }

    function initCorpusGraph(cfg) {
        const canvas = document.getElementById("graphrag-canvas");
        const chips = document.getElementById("graph-filter-chips");
        const detail = document.getElementById("graph-detail-body");
        const stats = document.getElementById("graph-stats");
        const maxEdgesInput = document.getElementById("graph-max-edges");
        const reloadBtn = document.getElementById("graph-reload");

        function load() {
            canvas.innerHTML = '<div class="tab-loading">Loading…</div>';
            const me = parseInt(maxEdgesInput.value, 10) || 2000;
            fetchJSON(cfg.urls.graph + "?max_edges=" + me).then(function (data) {
                stats.textContent = (data.nodes || []).length + " nodes, "
                    + (data.edges || []).length + " edges"
                    + (data.truncated ? " (truncated)" : "");
                const cy = renderGraph(canvas, data, {
                    onSelectNode: function (evt) {
                        showEntityDetail(detail, evt.target, cfg.urls.entitySources);
                    },
                });
                if (cy) {
                    buildTypeFilterChips(chips, data, function (active) {
                        applyTypeFilter(cy, active);
                    });
                }
            }).catch(function (e) { showError(canvas, e.message); });
        }
        reloadBtn.addEventListener("click", load);
        load();
    }

    function initCorpusRaptor(cfg) {
        const canvas = document.getElementById("raptor-tree-canvas");
        const detail = document.getElementById("raptor-detail-body");
        const stats = document.getElementById("raptor-stats");
        const depthInput = document.getElementById("raptor-max-depth");
        const reloadBtn = document.getElementById("raptor-reload");
        function load() {
            canvas.innerHTML = '<div class="tab-loading">Loading…</div>';
            const d = parseInt(depthInput.value, 10) || 5;
            fetchJSON(cfg.urls.raptor + "?max_depth=" + d).then(function (data) {
                stats.textContent = (data.nodes || []).length + " nodes, max depth "
                    + (data.max_depth_reached || 0);
                renderRaptorTree(canvas, data, {
                    onSelectNode: function (n) { showRaptorDetail(detail, n); },
                });
            }).catch(function (e) { showError(canvas, e.message); });
        }
        reloadBtn.addEventListener("click", load);
        load();
    }

    function initCorpusOntology(cfg) {
        const schemaBody = document.getElementById("ontology-schema-body");
        const indivBody = document.getElementById("ontology-individuals-body");
        const stats = document.getElementById("ontology-stats");
        fetchJSON(cfg.urls.ontology).then(function (data) {
            stats.innerHTML = data.ontology_built
                ? '<span class="status-pill status-completed">Ontology v' + data.version + '</span> '
                  + '<strong>' + (data.total_triples || 0) + '</strong> triples, '
                  + '<strong>' + (data.contributing_chunks || 0) + '</strong> contributing chunks'
                : '<span class="status-pill status-pending">Ontology not built yet</span>';
            renderOntologySchema(schemaBody, data.schema);
            renderOntologyIndividuals(indivBody, data);
        }).catch(function (e) { showError(schemaBody, e.message); });
    }

    // ===================================================================
    // Boot
    // ===================================================================

    document.addEventListener("DOMContentLoaded", function () {
        if (window.KNOWLEDGE_AGENT_CONFIG)  initSourceDetail(window.KNOWLEDGE_AGENT_CONFIG);
        if (window.KNOWLEDGE_INSPECT_CONFIG) initInspect(window.KNOWLEDGE_INSPECT_CONFIG);
    });

})();
