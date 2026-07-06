/**
 * Knowledge Agent explorer — client-side logic.
 *
 * Handles:
 *   - Activating a retrieval strategy
 *   - Starting ingestion (full pipeline or individual steps)
 *   - Polling Celery task progress and updating the UI
 *   - Persisting active task ID server-side so all users see progress
 */

(function () {
    "use strict";

    /* ---------------------------------------------------------------
       Helpers
       --------------------------------------------------------------- */

    function getCookie(name) {
        var match = document.cookie.match(
            new RegExp("(^|;\\s*)" + name + "=([^;]*)")
        );
        return match ? decodeURIComponent(match[2]) : "";
    }

    var CSRF = getCookie("csrftoken");

    function postJSON(url, body) {
        return fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": CSRF,
            },
            body: JSON.stringify(body),
        }).then(function (r) { return r.json(); });
    }

    function deleteJSON(url) {
        return fetch(url, {
            method: "DELETE",
            headers: { "X-CSRFToken": CSRF },
        }).then(function (r) { return r.json(); });
    }

    function getJSON(url) {
        return fetch(url).then(function (r) { return r.json(); });
    }

    /* ---------------------------------------------------------------
       Resolve API base from the current page URL.
       --------------------------------------------------------------- */

    function apiBase() {
        var path = window.location.pathname;
        var idx = path.indexOf("/knowledge/");
        if (idx !== -1) {
            return path.substring(0, idx) + "/knowledge/";
        }
        return "/knowledge/";
    }

    var API = apiBase();

    /* ---------------------------------------------------------------
       Server-side task persistence
       --------------------------------------------------------------- */

    function saveTask(taskId) {
        postJSON(API + "api/task/", { task_id: taskId }).catch(function () {});
    }

    function clearTask() {
        deleteJSON(API + "api/task/").catch(function () {});
    }

    /* ---------------------------------------------------------------
       Activate Strategy — shared helper
       --------------------------------------------------------------- */

    function setActiveStrategy(strategy) {
        return postJSON(API + "api/strategies/active/", { strategy: strategy }).then(
            function (data) {
                if (data.error) {
                    alert("Error: " + data.error);
                    return data;
                }
                var sel = document.getElementById("active-strategy-select");
                if (sel) sel.value = data.active_strategy;

                document.querySelectorAll(".strategy-card").forEach(function (card) {
                    var name = card.dataset.strategy;
                    var isNow = name === data.active_strategy;
                    card.classList.toggle("strategy-active", isNow);

                    var actions = card.querySelector(".strategy-actions");
                    if (!actions) return;

                    var oldBadge = actions.querySelector(".active-badge");
                    if (oldBadge) oldBadge.remove();

                    var btn = actions.querySelector(".activate-btn");
                    if (isNow) {
                        if (btn) btn.style.display = "none";
                        var badge = document.createElement("span");
                        badge.className = "active-badge";
                        badge.textContent = "Active";
                        actions.prepend(badge);
                    } else {
                        if (btn) btn.style.display = "";
                    }
                });
                return data;
            }
        );
    }

    var strategySelect = document.getElementById("active-strategy-select");
    if (strategySelect) {
        strategySelect.addEventListener("change", function () {
            setActiveStrategy(this.value);
        });
    }

    document.querySelectorAll(".activate-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            setActiveStrategy(this.dataset.strategy);
        });
    });

    /* ---------------------------------------------------------------
       Start Ingestion — full pipeline
       --------------------------------------------------------------- */

    var btnRunAll = document.getElementById("btn-run-all");
    if (btnRunAll) {
        btnRunAll.addEventListener("click", function () {
            btnRunAll.disabled = true;
            postJSON(API + "api/ingest/", {}).then(function (data) {
                if (data.task_id) {
                    saveTask(data.task_id);
                    pollProgress(data.task_id);
                }
            });
        });
    }

    /* ---------------------------------------------------------------
       Start Ingestion — single step
       --------------------------------------------------------------- */

    document.querySelectorAll(".step-run-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
            e.stopPropagation();
            var self = this;
            var step = self.dataset.step;
            self.disabled = true;
            self.textContent = "Running...";
            postJSON(API + "api/ingest/step/", { step: step }).then(function (data) {
                if (data.task_id) {
                    saveTask(data.task_id);
                    pollProgress(data.task_id, function () {
                        self.disabled = false;
                        self.textContent = self.getAttribute("title") || step;
                    });
                } else {
                    self.disabled = false;
                }
            });
        });
    });

    /* ---------------------------------------------------------------
       Build button (run required steps for a strategy)
       --------------------------------------------------------------- */

    document.querySelectorAll(".build-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var self = this;
            var steps = self.dataset.steps.split(",").filter(Boolean);
            self.disabled = true;
            self.textContent = "Building...";
            postJSON(API + "api/ingest/", { steps: steps }).then(function (data) {
                if (data.task_id) {
                    saveTask(data.task_id);
                    pollProgress(data.task_id, function () {
                        self.disabled = false;
                        self.textContent = "Build";
                    });
                } else {
                    self.disabled = false;
                }
            });
        });
    });

    /* ---------------------------------------------------------------
       Progress Polling
       --------------------------------------------------------------- */

    var STEP_LABELS = {
        setup_tables: "Create tables",
        download: "Download documents",
        parse: "Parse PDFs",
        chunk_and_embed: "Chunk & embed",
        build_parent_child: "Parent / child hierarchy",
        build_contextual: "Contextual enrichment",
        build_graph: "GraphRAG extraction",
        build_ontology: "OWL / RDF ontology",
        build_raptor: "RAPTOR summary tree",
    };

    function showBanner() {
        var b = document.getElementById("progress-banner");
        if (b) {
            b.classList.remove("hidden", "progress-done", "progress-failed");
        }
    }

    function pollProgress(taskId, onDone) {
        showBanner();
        var label = document.getElementById("progress-label");
        var stepEl = document.getElementById("progress-step");
        var bar = document.getElementById("progress-bar");
        var banner = document.getElementById("progress-banner");

        var interval = setInterval(function () {
            getJSON(API + "api/ingest/status/" + taskId + "/").then(function (data) {
                if (data.status === "progress") {
                    var step = data.current_step || "";
                    var done = data.completed_steps || 0;
                    var total = data.total_steps || 1;
                    var pct = Math.round((done / total) * 100);
                    if (label)
                        label.textContent = "Ingesting... (" + done + "/" + total + ")";
                    if (stepEl)
                        stepEl.textContent = STEP_LABELS[step] || step;
                    if (bar) bar.style.width = pct + "%";
                } else if (data.status === "completed") {
                    clearInterval(interval);
                    clearTask();
                    if (label) label.textContent = "Ingestion complete";
                    if (stepEl) stepEl.textContent = "";
                    if (bar) bar.style.width = "100%";
                    if (banner) banner.classList.add("progress-done");
                    if (btnRunAll) btnRunAll.disabled = false;
                    if (onDone) onDone();
                    setTimeout(refreshReadiness, 1000);
                } else if (data.status === "failed") {
                    clearInterval(interval);
                    clearTask();
                    if (label)
                        label.textContent =
                            "Ingestion failed: " + (data.error || "unknown error");
                    if (banner) banner.classList.add("progress-failed");
                    if (btnRunAll) btnRunAll.disabled = false;
                    if (onDone) onDone();
                } else if (data.status === "pending") {
                    if (label) label.textContent = "Waiting for worker...";
                }
            }).catch(function () {
                // Network error — keep polling, it may recover
            });
        }, 2500);
    }

    /* ---------------------------------------------------------------
       Refresh readiness after ingestion completes
       --------------------------------------------------------------- */

    function refreshReadiness() {
        getJSON(API + "api/readiness/").then(function (data) {
            var strategies = data.strategies || [];
            strategies.forEach(function (s) {
                var card = document.getElementById("card-" + s.name);
                if (!card) return;
                var pill = card.querySelector(".status-pill");
                if (pill) {
                    pill.classList.remove("status-completed", "status-pending");
                    pill.classList.add(s.ready ? "status-completed" : "status-pending");
                    pill.textContent = s.ready ? "Ready" : "Not ready";
                }
            });
        });
    }

    /* ---------------------------------------------------------------
       On page load: check server for an active task and resume
       --------------------------------------------------------------- */

    (function resumeIfActive() {
        getJSON(API + "api/task/").then(function (data) {
            var taskId = data.task_id;
            if (!taskId) return;

            // Verify the task is actually still running.
            // Celery returns "PENDING" for both queued tasks AND unknown IDs,
            // so only treat "progress" as truly active (the task has started).
            getJSON(API + "api/ingest/status/" + taskId + "/").then(function (status) {
                if (status.status === "progress") {
                    if (btnRunAll) btnRunAll.disabled = true;
                    pollProgress(taskId);
                } else {
                    // pending (likely stale), completed, or failed — clear
                    clearTask();
                }
            }).catch(function () {
                clearTask();
            });
        }).catch(function () {
            // API not reachable — nothing to resume
        });
    })();

})();
