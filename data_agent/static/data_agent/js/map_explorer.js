/**
 * Map Explorer v2 — Leaflet map with raster tiles, admin boundaries, and
 * interactive query system (point + area modes).
 *
 * Control rows:
 *   Row 1: Source / Variable / Date / Opacity
 *   Row 2: Admin overlay
 *   Row 3: Query mode — Point | Area (with Box/Polygon/Admin + Centroid/Area avg sub-options)
 *
 * Footer slider: resizable query results panel (time-series chart, static/categorical values, zonal stats).
 */
(function () {
    'use strict';

    // Horizon: global Chart.js font
    if (typeof Chart !== 'undefined') {
        Chart.defaults.font.family = "'Public Sans', sans-serif";
    }

    // -----------------------------------------------------------------------
    // DOM refs
    // -----------------------------------------------------------------------
    var sourceSelect    = document.getElementById('map-source');
    var varSelect       = document.getElementById('map-variable');
    var dateInput       = document.getElementById('map-date');
    var datePrev        = document.getElementById('date-prev');
    var dateNext        = document.getElementById('date-next');
    var dateGroup       = document.getElementById('map-date-group');
    var adminSelect     = document.getElementById('map-admin-level');
    var opacitySlider   = document.getElementById('map-opacity');
    var opacityLabel    = document.getElementById('opacity-label');
    var queryModeBar    = document.getElementById('query-mode-bar');
    var queryStatus     = document.getElementById('query-status');
    var areaAggSelect   = document.getElementById('area-aggregation');
    var queryLocLabel   = document.getElementById('query-location-label');
    var queryTimeSeriesSec = document.getElementById('query-timeseries-section');
    var queryStaticSec     = document.getElementById('query-static-section');
    var queryAreaSec    = document.getElementById('query-area-section');
    var queryAreaResults = document.getElementById('query-area-results');
    var queryStaticResults = document.getElementById('query-static-results');
    var queryEmpty      = document.getElementById('query-empty');
    var queryStartDate  = document.getElementById('query-start-date');
    var queryEndDate    = document.getElementById('query-end-date');
    var queryReloadBtn  = document.getElementById('query-reload');
    var queryChartCanvas = document.getElementById('query-chart');
    var queryMinimizeBtn = document.getElementById('query-minimize');
    var queryLocSelect  = document.getElementById('query-location-select');
    var queryRunRow     = document.getElementById('query-run-row');
    var queryRunSelect  = document.getElementById('query-run-select');
    var queryRunCount   = document.getElementById('query-run-count');
    var footerHandle    = document.getElementById('footer-handle');
    var footerContent   = document.getElementById('query-results-content');
    var queryVarSelect  = document.getElementById('query-variable');
    var queryAddBtn     = document.getElementById('query-add');
    var queryControlsRow = document.getElementById('query-controls-row');
    var queryDateRangeGroup = document.getElementById('query-date-range-group');

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------
    var sources = [];
    var sourcesById = {};
    var queryMode = 'point';          // 'point' | 'bbox' | 'polygon' | 'admin'
    var areaAggregation = 'mean';     // 'mean' | 'min' | 'max' (for area modes)
    var queryChart = null;
    var queryMarker = null;
    var queryShape = null;
    var lastQueryLat = null;
    var lastQueryLon = null;
    var clickCooldown = false;
    var footerOpenHeight = 280;       // default open height in px
    var lastQueryGeometry = null;
    var accumulatedTimeSeries = [];
    var queryHistory = [];
    var activeLocationIdx = -1;
    var locationIdCounter = 0;
    var runIdCounter = 0;

    // Deployment subpath prefix (e.g. "/subpath" or ""). Prepend to module-absolute URLs.
    var SUBPATH = window.SUBPATH || '';

    // -----------------------------------------------------------------------
    // Display name helpers (reads from source metadata, NOT static lists)
    // -----------------------------------------------------------------------
    function displayName(key, source) {
        if (!key) return '';
        // Try source-specific variable display name
        if (source) {
            var src = typeof source === 'string' ? sourcesById[source] : source;
            if (src && src.variable_display_names && src.variable_display_names[key]) {
                return src.variable_display_names[key];
            }
        }
        // Fallback: capitalize + replace underscores
        return key.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function sourceDisplayName(src) {
        if (!src) return '';
        return src.display_name || src.name || src.id || '';
    }

    // -----------------------------------------------------------------------
    // Legend + chart color config
    // -----------------------------------------------------------------------
    var LEGEND_CONFIG = {
        tmax: { label: 'Max Temp (°C)',     gradient: 'linear-gradient(to right, #3264c8, #64c8ff, #ffff64, #ff6400, #b40000)', min: '-10', max: '45' },
        tmin: { label: 'Min Temp (°C)',     gradient: 'linear-gradient(to right, #3264c8, #64c8ff, #ffff64, #ff6400, #b40000)', min: '-20', max: '35' },
        rain: { label: 'Rainfall (mm)',      gradient: 'linear-gradient(to right, rgba(240,250,255,0.3), #b4dcff, #64b4ff, #3264dc, #0000b4)', min: '0', max: '50+' },
        srad: { label: 'Solar Rad (MJ/m²)', gradient: 'linear-gradient(to right, rgba(255,255,220,0.3), #ffff96, #ffff00, #ff9600, #c80000)', min: '0', max: '35' },
    };
    var CHART_COLORS = {
        tmax: 'rgba(246,65,55,0.9)', tmin: 'rgba(1,112,185,0.9)',
        rain: 'rgba(34,197,94,0.9)', srad: 'rgba(245,158,11,0.9)',
    };

    // -----------------------------------------------------------------------
    // Map init
    // -----------------------------------------------------------------------
    var map = L.map('map-container', { center: [33.0, -86.5], zoom: 7, zoomControl: true });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '© CARTO © OSM', subdomains: 'abcd', maxZoom: 19,
    }).addTo(map);

    // -----------------------------------------------------------------------
    // Raster tile layer
    // -----------------------------------------------------------------------
    var rasterLayer = null;

    function buildRasterLayer() {
        var sid = sourceSelect.value, v = varSelect.value, d = dateInput.value;
        var opacity = parseInt(opacitySlider.value, 10) / 100;
        if (rasterLayer) { map.removeLayer(rasterLayer); rasterLayer = null; }
        if (!sid || !v) return;
        var src = sourcesById[sid];
        var hasDate = !src || src.dataset_type !== 'static';
        var dp = hasDate ? (d || '2020-01-01') : 'static';

        // Skip tile loading if we know there's no data for this date
        // (the date warning badge is already shown by checkDateAvailability)
        if (hasDate && d) {
            var mon = d.slice(0, 7);
            var cacheKey = sid + ':' + mon;
            var avail = calAvailCache[cacheKey];
            if (avail && !avail.has(d) && !avail.has('static')) {
                // No data — don't waste API requests on empty tiles
                return;
            }
        }

        rasterLayer = L.tileLayer(
            SUBPATH + '/data/api/map/tiles/' + encodeURIComponent(sid) + '/' +
            encodeURIComponent(v) + '/' + dp + '/{z}/{x}/{y}.png',
            { opacity: opacity, maxZoom: 19, tileSize: 256, attribution: sourceDisplayName(src) }
        );

        // Tile loading progress tracker
        var tilesLoading = 0, tilesLoaded = 0;
        rasterLayer.on('loading', function () {
            tilesLoading = 0; tilesLoaded = 0;
            showProgress(progressTiles);
            progressTilesLoaded.textContent = '0';
            progressTilesTotal.textContent = '?';
        });
        rasterLayer.on('tileloadstart', function () {
            tilesLoading++;
            progressTilesTotal.textContent = tilesLoading;
        });
        rasterLayer.on('tileload', function () {
            tilesLoaded++;
            progressTilesLoaded.textContent = tilesLoaded;
        });
        rasterLayer.on('tileerror', function () {
            tilesLoaded++;
            progressTilesLoaded.textContent = tilesLoaded;
        });
        rasterLayer.on('load', function () {
            hideProgress(progressTiles);
        });

        rasterLayer.addTo(map);
    }

    // -----------------------------------------------------------------------
    // Source loader + dropdown rendering (uses display_name from backend)
    // -----------------------------------------------------------------------
    function loadSources() {
        return fetch(SUBPATH + '/data/api/sources/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                sources = (data && data.sources) || [];
                sourcesById = {};
                sources.forEach(function (s) { sourcesById[s.id] = s; });
                renderSourceDropdown();
            })
            .catch(function (err) {
                console.error('Failed to load sources:', err);
                sourceSelect.innerHTML = '<option value="">Failed to load</option>';
            });
    }

    function renderSourceDropdown() {
        var groups = { time_series: [], static: [] };
        sources.forEach(function (s) { (groups[s.dataset_type] || groups.time_series).push(s); });
        sourceSelect.innerHTML = '<option value="">— Select a source —</option>';
        var labels = { time_series: 'Time Series', static: 'Static' };
        ['time_series', 'static'].forEach(function (kind) {
            var entries = groups[kind] || [];
            if (!entries.length) return;
            var og = document.createElement('optgroup');
            og.label = labels[kind];
            entries.forEach(function (s) {
                var opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = sourceDisplayName(s);
                og.appendChild(opt);
            });
            sourceSelect.appendChild(og);
        });
    }

    function onSourceChange() {
        var src = sourcesById[sourceSelect.value];
        varSelect.innerHTML = '';
        if (!src) {
            varSelect.disabled = true;
            varSelect.innerHTML = '<option value="">Pick a source</option>';
            buildRasterLayer();
            return;
        }
        var vars = src.variables || [];
        varSelect.disabled = !vars.length;
        if (!vars.length) {
            varSelect.innerHTML = '<option value="">No variables</option>';
        } else {
            vars.forEach(function (v) {
                var opt = document.createElement('option');
                opt.value = v;
                opt.textContent = displayName(v, src);
                varSelect.appendChild(opt);
            });
        }
        var isStatic = src.dataset_type === 'static';
        dateGroup.style.display = isStatic ? 'none' : '';
        // Update aggregation dropdown visibility based on source type
        updateAggregationState();
        legend.update(varSelect.value);

        // For reference (static) rasters, load tiles immediately.
        // For time-series sources, DON'T load tiles yet — wait for
        // fetchLatestDate() to resolve and call setDate() which triggers
        // buildRasterLayer() with the correct date. This prevents tile
        // requests against unknown/wrong dates that cause 500 errors.
        if (isStatic) {
            buildRasterLayer();
        } else {
            // Clear existing tiles while we wait for the date check
            if (rasterLayer) { map.removeLayer(rasterLayer); rasterLayer = null; }
        }
    }

    function updateAggregationState() {
        // Aggregation dropdown is enabled for area modes, disabled for point
        var isArea = queryMode !== 'point';
        areaAggSelect.disabled = !isArea;
        areaAggSelect.style.opacity = isArea ? '1' : '0.4';
    }

    // -----------------------------------------------------------------------
    // Admin boundary overlay (hover/click gated behind query mode)
    // -----------------------------------------------------------------------
    var adminLayer = null;
    var adminStyle = { color: '#fff', weight: 1.2, opacity: 0.7, fillColor: 'transparent', fillOpacity: 0 };
    var adminHover = { color: '#0170B9', weight: 2.5, opacity: 1, fillColor: '#0170B9', fillOpacity: 0.12 };
    var adminClick = { color: '#f64137', weight: 3, opacity: 1, fillColor: '#f64137', fillOpacity: 0.15 };

    function isAdminInteractive() {
        return queryMode === 'admin';
    }

    // Progress tracker helpers
    var progressPanel = document.getElementById('progress-panel');
    var progressAdmin = document.getElementById('progress-admin');
    var progressAdminCount = document.getElementById('progress-admin-count');
    var progressTiles = document.getElementById('progress-tiles');
    var progressTilesLoaded = document.getElementById('progress-tiles-loaded');
    var progressTilesTotal = document.getElementById('progress-tiles-total');

    function showProgress(el) { el.classList.remove('is-hidden'); progressPanel.classList.remove('is-hidden'); }
    function hideProgress(el) {
        el.classList.add('is-hidden');
        if (progressAdmin.classList.contains('is-hidden') && progressTiles.classList.contains('is-hidden')) {
            progressPanel.classList.add('is-hidden');
        }
    }

    function loadAdminBoundaries() {
        var level = adminSelect.value;
        if (adminLayer) { map.removeLayer(adminLayer); adminLayer = null; }
        if (!level) return;

        // Use NDJSON streaming for progressive rendering
        showProgress(progressAdmin);
        var featureCount = 0;
        progressAdminCount.textContent = '0';

        adminLayer = L.geoJSON(null, {
                    style: adminStyle,
                    onEachFeature: function (feature, layer) {
                        var props = feature.properties || {};
                        var parts = [];
                        if (props.country) parts.push('<b>' + props.country + '</b>');
                        if (props.admin1) parts.push(props.admin1);
                        if (props.admin2) parts.push(props.admin2);
                        var popupText = parts.join(' › ');

                        layer.on('mouseover', function () {
                            if (!isAdminInteractive()) return;
                            layer.setStyle(adminHover);
                            layer.bringToFront();
                            layer.bindTooltip(popupText, { sticky: true, direction: 'top', opacity: 0.9 }).openTooltip();
                        });
                        layer.on('mouseout', function () {
                            if (!isAdminInteractive()) return;
                            if (adminLayer) adminLayer.resetStyle(layer);
                            layer.closeTooltip();
                            layer.unbindTooltip();
                        });
                        layer.on('click', function (e) {
                            if (!isAdminInteractive()) {
                                map.fire('click', e);
                                return;
                            }
                            L.DomEvent.stopPropagation(e);
                            if (adminLayer) adminLayer.resetStyle();
                            layer.setStyle(adminClick);
                            var center = layer.getBounds().getCenter();
                            // Admin mode = area query, pass admin props for label
                            runAreaQuery(feature.geometry, center, undefined, props);
                        });
                    },
                });
        adminLayer.addTo(map);

        // Stream features via NDJSON
        fetch(SUBPATH + '/data/api/map/admin/?level=' + encodeURIComponent(level) + '&stream=1', {
            headers: { 'Accept': 'application/x-ndjson' },
        })
        .then(function (response) {
            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';

            function processChunk(result) {
                if (result.done) {
                    // Process any remaining buffer
                    if (buffer.trim()) {
                        try {
                            var feature = JSON.parse(buffer.trim());
                            adminLayer.addData(feature);
                            featureCount++;
                            progressAdminCount.textContent = featureCount;
                        } catch (e) { /* skip malformed last line */ }
                    }
                    hideProgress(progressAdmin);
                    return;
                }

                buffer += decoder.decode(result.value, { stream: true });
                var lines = buffer.split('\n');
                buffer = lines.pop(); // keep incomplete last line in buffer

                lines.forEach(function (line) {
                    if (!line.trim()) return;
                    try {
                        var feature = JSON.parse(line);
                        adminLayer.addData(feature);
                        featureCount++;
                        progressAdminCount.textContent = featureCount;
                    } catch (e) {
                        console.warn('NDJSON parse error:', e);
                    }
                });

                return reader.read().then(processChunk);
            }

            return reader.read().then(processChunk);
        })
        .catch(function (err) {
            console.error('Admin stream error:', err);
            hideProgress(progressAdmin);
        });
    }

    // -----------------------------------------------------------------------
    // Legend
    // -----------------------------------------------------------------------
    var LegendControl = L.Control.extend({
        options: { position: 'topright' },
        onAdd: function () { this._div = L.DomUtil.create('div', 'map-legend'); this.update(varSelect.value); return this._div; },
        update: function (v) {
            var cfg = LEGEND_CONFIG[v];
            if (!cfg) { this._div.innerHTML = ''; this._div.style.display = 'none'; return; }
            this._div.style.display = 'block';
            this._div.innerHTML = '<div class="legend-title">' + cfg.label + '</div>' +
                '<div class="legend-bar" style="background:' + cfg.gradient + '"></div>' +
                '<div class="legend-labels"><span>' + cfg.min + '</span><span>' + cfg.max + '</span></div>';
        },
    });
    var legend = new LegendControl();
    legend.addTo(map);

    // -----------------------------------------------------------------------
    // Date navigation + reusable MiniCalendar
    // -----------------------------------------------------------------------
    var dateTrigger    = document.getElementById('date-trigger');
    var dateWarning    = document.getElementById('date-warning');
    var calAvailCache  = {};  // 'source:YYYY-MM' → Set of 'YYYY-MM-DD'

    function setDate(iso) {
        dateInput.value = iso;
        dateTrigger.textContent = iso;
        checkDateAvailability(iso);
        buildRasterLayer();
    }

    function shiftDate(d) {
        if (!dateInput.value) return;
        var dt = new Date(dateInput.value + 'T00:00:00');
        dt.setDate(dt.getDate() + d);
        setDate(dt.toISOString().slice(0, 10));
    }
    datePrev.addEventListener('click', function () { shiftDate(-1); });
    dateNext.addEventListener('click', function () { shiftDate(1); });

    // --- Availability check ---
    function checkDateAvailability(iso) {
        var sid = sourceSelect.value;
        if (!sid || !iso) { dateWarning.classList.add('is-hidden'); return; }
        var src = sourcesById[sid];
        if (src && src.dataset_type === 'static') { dateWarning.classList.add('is-hidden'); return; }
        var mon = iso.slice(0, 7);
        var cacheKey = sid + ':' + mon;
        if (calAvailCache[cacheKey]) {
            dateWarning.classList.toggle('is-hidden', calAvailCache[cacheKey].has(iso));
            return;
        }
        fetchAvailDates(sid, mon).then(function () {
            if (calAvailCache[cacheKey]) {
                dateWarning.classList.toggle('is-hidden', calAvailCache[cacheKey].has(iso));
            }
        });
    }

    function fetchAvailDates(sid, month) {
        var cacheKey = sid + ':' + month;
        if (calAvailCache[cacheKey]) return Promise.resolve();
        return fetch(SUBPATH + '/data/api/dates/?source=' + encodeURIComponent(sid) + '&month=' + month)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.static) { calAvailCache[cacheKey] = new Set(['static']); return; }
                var dates = (data.dates || []), byMonth = {};
                dates.forEach(function (d) {
                    var m = d.slice(0, 7);
                    if (!byMonth[m]) byMonth[m] = new Set();
                    byMonth[m].add(d);
                });
                Object.keys(byMonth).forEach(function (m) { calAvailCache[sid + ':' + m] = byMonth[m]; });
                if (!calAvailCache[cacheKey]) calAvailCache[cacheKey] = new Set();
            })
            .catch(function () { calAvailCache[cacheKey] = new Set(); });
    }

    // -----------------------------------------------------------------------
    // Reusable MiniCalendar — builds its own DOM inside a container div
    // -----------------------------------------------------------------------
    var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    function MiniCalendar(triggerEl, hiddenInput, containerEl, onSelect) {
        var self = this;
        self.trigger = triggerEl;
        self.input = hiddenInput;
        self.container = containerEl;
        self.onSelect = onSelect;
        self.viewMonth = null; // 0-11
        self.viewYear = null;

        // Build DOM inside container
        self.container.innerHTML =
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.4rem;">' +
                '<button type="button" class="date-nav-btn mc-prev" style="width:24px;height:24px;font-size:0.8rem;background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);">◀</button>' +
                '<div style="display:flex;gap:0.3rem;">' +
                    '<select class="mc-month" aria-label="Month" style="background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);padding:0.15rem;border-radius:var(--radius);font-size:0.75rem;">' +
                        MONTHS.map(function(m,i) { return '<option value="'+i+'">'+m+'</option>'; }).join('') +
                    '</select>' +
                    '<select class="mc-year" aria-label="Year" style="background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);padding:0.15rem;border-radius:var(--radius);font-size:0.75rem;">' +
                        (function() { var h=''; for(var y=2015;y<=2030;y++) h+='<option value="'+y+'">'+y+'</option>'; return h; })() +
                    '</select>' +
                '</div>' +
                '<button type="button" class="date-nav-btn mc-next" style="width:24px;height:24px;font-size:0.8rem;background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);">▶</button>' +
            '</div>' +
            '<div style="display:grid;grid-template-columns:repeat(7,1fr);text-align:center;font-size:0.68rem;color:var(--text-secondary);margin-bottom:0.2rem;">' +
                '<span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span>' +
            '</div>' +
            '<div class="mc-grid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px;"></div>';

        self.monthSel = self.container.querySelector('.mc-month');
        self.yearSel = self.container.querySelector('.mc-year');
        self.grid = self.container.querySelector('.mc-grid');

        self.container.querySelector('.mc-prev').addEventListener('click', function (e) { e.stopPropagation(); self.nav(-1); });
        self.container.querySelector('.mc-next').addEventListener('click', function (e) { e.stopPropagation(); self.nav(1); });
        self.monthSel.addEventListener('change', function () { self.render(); });
        self.yearSel.addEventListener('change', function () { self.render(); });

        // Toggle on trigger click
        self.trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            var isOpen = !self.container.classList.contains('is-hidden');
            closeAllCalendars();
            if (!isOpen) {
                // Sync to current value
                var cur = self.input.value || new Date().toISOString().slice(0, 10);
                self.viewMonth = parseInt(cur.slice(5, 7), 10) - 1;
                self.viewYear = parseInt(cur.slice(0, 4), 10);
                self.monthSel.value = self.viewMonth;
                self.yearSel.value = self.viewYear;
                self.container.classList.remove('is-hidden');
                self.render();
            }
        });

        // Prevent clicks inside calendar from closing it
        self.container.addEventListener('click', function (e) { e.stopPropagation(); });
    }

    MiniCalendar.prototype.nav = function (dir) {
        var m = parseInt(this.monthSel.value) + dir;
        var y = parseInt(this.yearSel.value);
        if (m < 0) { m = 11; y--; } if (m > 11) { m = 0; y++; }
        this.monthSel.value = m; this.yearSel.value = y;
        this.render();
    };

    MiniCalendar.prototype.render = function () {
        var self = this;
        var m = parseInt(self.monthSel.value);
        var y = parseInt(self.yearSel.value);
        var sid = sourceSelect.value;
        var monthKey = y + '-' + String(m + 1).padStart(2, '0');
        var currentDate = self.input.value || '';

        function doRender() {
            var firstDay = new Date(y, m, 1).getDay();
            var daysInMonth = new Date(y, m + 1, 0).getDate();
            var availSet = calAvailCache[(sid || '') + ':' + monthKey] || new Set();
            self.grid.innerHTML = '';

            for (var i = 0; i < firstDay; i++) {
                var empty = document.createElement('div');
                empty.className = 'cal-day other-month';
                self.grid.appendChild(empty);
            }
            for (var d = 1; d <= daysInMonth; d++) {
                var iso = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
                var cell = document.createElement('div');
                cell.className = 'cal-day';
                cell.textContent = d;
                if (availSet.has(iso) || availSet.has('static')) {
                    cell.classList.add('available');
                    cell.addEventListener('click', (function (dateVal) {
                        return function () {
                            self.input.value = dateVal;
                            self.trigger.textContent = dateVal;
                            self.container.classList.add('is-hidden');
                            if (self.onSelect) self.onSelect(dateVal);
                        };
                    })(iso));
                }
                if (iso === currentDate) cell.classList.add('selected');
                self.grid.appendChild(cell);
            }
        }

        if (sid) fetchAvailDates(sid, monthKey).then(doRender); else doRender();
    };

    MiniCalendar.prototype.setValue = function (iso) {
        this.input.value = iso;
        this.trigger.textContent = iso;
    };

    // Close all open calendars on outside click
    var allCalendars = [];
    function closeAllCalendars() {
        allCalendars.forEach(function (mc) { mc.container.classList.add('is-hidden'); });
    }
    document.addEventListener('click', closeAllCalendars);

    // -----------------------------------------------------------------------
    // Create the three calendar instances
    // -----------------------------------------------------------------------

    // 1. Main date picker (header Row 1)
    var mainCal = new MiniCalendar(
        dateTrigger,
        dateInput,
        document.getElementById('date-calendar'),
        function (iso) { setDate(iso); }
    );
    allCalendars.push(mainCal);

    // 2. Query start date
    var queryStartCal = new MiniCalendar(
        document.getElementById('query-start-trigger'),
        queryStartDate,
        document.getElementById('query-start-cal'),
        null // no auto-action on select — user clicks Reload
    );
    allCalendars.push(queryStartCal);

    // 3. Query end date
    var queryEndCal = new MiniCalendar(
        document.getElementById('query-end-trigger'),
        queryEndDate,
        document.getElementById('query-end-cal'),
        null
    );
    allCalendars.push(queryEndCal);

    // Init main trigger label
    dateTrigger.textContent = dateInput.value || '—';

    // On source change: update dropdowns IMMEDIATELY, then fetch latest date async.
    // The variable dropdown must update before any tiles are requested, so we
    // call _origOnSourceChange() synchronously first, then update just the date.
    var sourceNoDataWarning = document.getElementById('source-no-data-warning');
    var _origOnSourceChange = onSourceChange;
    onSourceChange = function () {
        calAvailCache = {};
        sourceNoDataWarning.classList.add('is-hidden');
        dateWarning.classList.add('is-hidden');

        // Update variable dropdown + legend + hide date for static rasters
        // IMMEDIATELY — no async delay.
        _origOnSourceChange();

        var src = sourcesById[sourceSelect.value];

        // If footer is open, sync it with the newly selected source
        var footH = parseInt(footerContent.style.height) || 0;
        if (footH > 50 && getActiveLocation()) syncFooterWithSource();

        // For static rasters, no date logic needed
        if (src && src.dataset_type === 'static') return;

        // For time-series sources, fetch the latest available date AFTER
        // the dropdown is already correct. If data exists, update the date
        // and reload tiles. If not, show the red warning.
        if (sourceSelect.value) {
            fetchLatestDate(sourceSelect.value).then(function () {
                if (dateInput.value) checkDateAvailability(dateInput.value);
            });
        }
    };

    function fetchLatestDate(sid) {
        return fetch(SUBPATH + '/data/api/check/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: sid }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success || !data.results || !data.results.length) {
                sourceNoDataWarning.classList.remove('is-hidden');
                return;
            }
            var result = data.results[0];
            if (!result.available || !result.last_date) {
                // No data loaded at all — show red warning
                sourceNoDataWarning.classList.remove('is-hidden');
                return;
            }
            sourceNoDataWarning.classList.add('is-hidden');
            // Set the date to the latest available
            setDate(result.last_date);
        })
        .catch(function (err) {
            console.warn('Failed to fetch latest date:', err);
            sourceNoDataWarning.classList.remove('is-hidden');
        });
    }

    // Init trigger label
    dateTrigger.textContent = dateInput.value || '—';

    // -----------------------------------------------------------------------
    // Query mode management — flat: Point / Box / Polygon / Admin
    // -----------------------------------------------------------------------
    var drawRectHandler = null, drawPolyHandler = null;

    function initDrawHandlers() {
        if (typeof L.Draw === 'undefined') return;
        var opts = { shapeOptions: { color: '#0170B9', weight: 2, opacity: 0.8, fillColor: '#0170B9', fillOpacity: 0.12 } };
        drawRectHandler = new L.Draw.Rectangle(map, opts);
        drawPolyHandler = new L.Draw.Polygon(map, opts);
        map.on(L.Draw.Event.CREATED, function (e) {
            var geojson = e.layer.toGeoJSON().geometry;
            var center = e.layer.getBounds().getCenter();
            // Box and Polygon are area modes → area query (runAreaQuery handles shape display)
            runAreaQuery(geojson, center);
        });
    }

    // Flat mode toggle — all four buttons wired to setQueryMode
    queryModeBar.querySelectorAll('[data-qmode]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            setQueryMode(btn.dataset.qmode);
        });
    });

    function setQueryMode(mode) {
        queryMode = mode;
        // Update button active states
        queryModeBar.querySelectorAll('[data-qmode]').forEach(function (b) {
            b.classList.toggle('active', b.dataset.qmode === mode);
        });
        // Clear previous drawn shapes and markers when switching modes
        clearQueryShape();
        clearQueryMarker();
        // Disable all draw handlers, then enable the right one
        if (drawRectHandler) drawRectHandler.disable();
        if (drawPolyHandler) drawPolyHandler.disable();

        if (mode === 'bbox' && drawRectHandler) {
            drawRectHandler.enable();
            queryStatus.textContent = 'Draw a rectangle on the map';
        } else if (mode === 'polygon' && drawPolyHandler) {
            drawPolyHandler.enable();
            queryStatus.textContent = 'Draw a polygon on the map';
        } else if (mode === 'admin') {
            queryStatus.textContent = 'Click an admin boundary';
        } else {
            queryStatus.textContent = '';
        }

        updateAggregationState();
    }

    if (areaAggSelect) {
        areaAggSelect.addEventListener('change', function () {
            areaAggregation = areaAggSelect.value;
        });
    }

    // -----------------------------------------------------------------------
    // Map click handler (Point mode only)
    // -----------------------------------------------------------------------
    map.on('click', function (e) {
        if (queryMode !== 'point') return;
        if (clickCooldown) return;
        clickCooldown = true;
        setTimeout(function () { clickCooldown = false; }, 300);
        runPointQuery(e.latlng.lat, e.latlng.lng);
    });

    // -----------------------------------------------------------------------
    // Query controls — variable selector + date range visibility
    // -----------------------------------------------------------------------
    function populateQueryVarSelect(src) {
        var prevVal = queryVarSelect.value;
        queryVarSelect.innerHTML = '';
        if (!src || !src.variables || !src.variables.length) return;
        src.variables.forEach(function (v) {
            var opt = document.createElement('option');
            opt.value = v;
            opt.textContent = displayName(v, src);
            queryVarSelect.appendChild(opt);
        });
        // Preserve previous selection if still valid, else default to header
        var opts = Array.from(queryVarSelect.options);
        if (prevVal && opts.some(function (o) { return o.value === prevVal; })) {
            queryVarSelect.value = prevVal;
        } else if (varSelect.value) {
            queryVarSelect.value = varSelect.value;
        }
    }

    function showQueryControls() {
        var src = sourcesById[sourceSelect.value];
        populateQueryVarSelect(src);
        queryControlsRow.style.display = 'flex';
        var isTimeSeries = src && src.dataset_type === 'time_series';
        queryDateRangeGroup.style.display = isTimeSeries ? 'inline-flex' : 'none';
        queryAddBtn.style.display = isTimeSeries ? '' : 'none';
    }

    // -----------------------------------------------------------------------
    // Query history management
    // -----------------------------------------------------------------------
    function getActiveLocation() {
        return (activeLocationIdx >= 0 && activeLocationIdx < queryHistory.length)
            ? queryHistory[activeLocationIdx] : null;
    }

    function getActiveRun() {
        var loc = getActiveLocation();
        if (!loc) return null;
        var idx = loc.activeRunIdx;
        return (idx >= 0 && idx < loc.datasetRuns.length) ? loc.datasetRuns[idx] : null;
    }

    function generateLocationLabel(type, lat, lon, adminProps) {
        if (adminProps) {
            if (adminProps.admin2) return adminProps.admin2 + ', ' + (adminProps.admin1 || adminProps.country || '');
            if (adminProps.admin1) return adminProps.admin1 + ', ' + (adminProps.country || '');
            if (adminProps.country) return adminProps.country;
        }
        var coords = lat.toFixed(4) + ', ' + lon.toFixed(4);
        if (type === 'bbox') return 'Box @ ' + coords;
        if (type === 'polygon') return 'Polygon @ ' + coords;
        return '@ ' + coords;
    }

    function findMatchingLocation(type, adminProps) {
        if (type !== 'admin' || !adminProps) return -1;
        var key = JSON.stringify({ c: adminProps.country || '', a1: adminProps.admin1 || '', a2: adminProps.admin2 || '' });
        for (var i = 0; i < queryHistory.length; i++) {
            if (queryHistory[i].type !== 'admin' || !queryHistory[i].adminProps) continue;
            var ap = queryHistory[i].adminProps;
            var eKey = JSON.stringify({ c: ap.country || '', a1: ap.admin1 || '', a2: ap.admin2 || '' });
            if (key === eKey) return i;
        }
        return -1;
    }

    function addOrFindLocation(type, lat, lon, geometry, adminProps) {
        var idx = findMatchingLocation(type, adminProps);
        if (idx >= 0) {
            activeLocationIdx = idx;
            return queryHistory[idx];
        }
        var entry = {
            id: 'loc_' + (locationIdCounter++),
            label: generateLocationLabel(type, lat, lon, adminProps),
            type: type, lat: lat, lon: lon,
            geometry: geometry || null,
            adminProps: adminProps || null,
            datasetRuns: [],
            activeRunIdx: -1
        };
        queryHistory.push(entry);
        activeLocationIdx = queryHistory.length - 1;
        return entry;
    }

    function findMatchingRun(loc, sourceId) {
        for (var i = 0; i < loc.datasetRuns.length; i++) {
            if (loc.datasetRuns[i].sourceId === sourceId) return i;
        }
        return -1;
    }

    function addOrUpdateRun(loc, sourceId) {
        var idx = findMatchingRun(loc, sourceId);
        if (idx >= 0) {
            loc.activeRunIdx = idx;
            return loc.datasetRuns[idx];
        }
        var src = sourcesById[sourceId];
        var run = {
            id: 'run_' + (runIdCounter++),
            sourceId: sourceId,
            sourceName: sourceDisplayName(src),
            lastVariable: null,
            startDate: null, endDate: null,
            timeSeriesResults: [], staticResults: [],
            zonalData: null, accumulatedTimeSeries: []
        };
        loc.datasetRuns.unshift(run);
        loc.activeRunIdx = 0;
        return run;
    }

    function cacheRunResults(run, data, isArea) {
        if (data.time_series_results) {
            run.timeSeriesResults = data.time_series_results;
            run.accumulatedTimeSeries = data.time_series_results;
        }
        if (data.static_results) run.staticResults = data.static_results;
        if (isArea && !data.time_series_results) run.zonalData = data;
    }

    function generateRunLabel(run) {
        return run.sourceName || run.sourceId;
    }

    function renderLocationDropdown() {
        queryLocSelect.innerHTML = '';
        for (var i = 0; i < queryHistory.length; i++) {
            var opt = document.createElement('option');
            opt.value = queryHistory[i].id;
            opt.textContent = queryHistory[i].label;
            queryLocSelect.appendChild(opt);
        }
        if (activeLocationIdx >= 0 && activeLocationIdx < queryHistory.length) {
            queryLocSelect.value = queryHistory[activeLocationIdx].id;
        }
        queryLocSelect.classList.toggle('is-hidden', queryHistory.length === 0);
        queryLocLabel.style.display = queryHistory.length > 0 ? 'none' : '';
    }

    function renderRunDropdown() {
        var loc = getActiveLocation();
        queryRunSelect.innerHTML = '';
        if (!loc || !loc.datasetRuns.length) {
            queryRunRow.style.display = 'none';
            queryRunCount.textContent = '';
            return;
        }
        for (var i = 0; i < loc.datasetRuns.length; i++) {
            var opt = document.createElement('option');
            opt.value = loc.datasetRuns[i].id;
            opt.textContent = generateRunLabel(loc.datasetRuns[i]);
            queryRunSelect.appendChild(opt);
        }
        if (loc.activeRunIdx >= 0) queryRunSelect.value = loc.datasetRuns[loc.activeRunIdx].id;
        queryRunRow.style.display = 'flex';
        queryRunCount.textContent = loc.datasetRuns.length + (loc.datasetRuns.length === 1 ? ' source' : ' sources');
    }

    function restoreLocationState(idx) {
        if (idx < 0 || idx >= queryHistory.length) return;
        activeLocationIdx = idx;
        var loc = queryHistory[idx];
        lastQueryLat = loc.lat; lastQueryLon = loc.lon; lastQueryGeometry = loc.geometry;

        // Swap map marker/shape — only one visible at a time
        clearQueryMarker(); clearQueryShape();
        if (loc.type === 'point') {
            queryMarker = L.marker([loc.lat, loc.lon]).addTo(map);
        } else if (loc.geometry) {
            queryShape = L.geoJSON(loc.geometry, {
                style: { color: '#f64137', weight: 3, opacity: 1, fillColor: '#f64137', fillOpacity: 0.15 }
            }).addTo(map);
        }

        renderLocationDropdown();

        // Prefer the run matching the current map source; show prompt if none
        var mapSource = sourceSelect.value;
        var matchIdx = mapSource ? findMatchingRun(loc, mapSource) : -1;
        if (matchIdx >= 0) {
            restoreRunState(matchIdx);
        } else if (mapSource && loc.datasetRuns.length) {
            renderRunDropdown();
            showSourcePrompt(mapSource);
        } else if (loc.activeRunIdx >= 0 && loc.activeRunIdx < loc.datasetRuns.length) {
            restoreRunState(loc.activeRunIdx);
        }
    }

    function restoreRunState(runIdx) {
        var loc = getActiveLocation();
        if (!loc) return;
        loc.activeRunIdx = runIdx;
        var run = loc.datasetRuns[runIdx];
        if (!run) return;

        // Populate query controls from the run's source
        var src = sourcesById[run.sourceId];
        populateQueryVarSelect(src);
        queryControlsRow.style.display = 'flex';
        var isTS = src && src.dataset_type === 'time_series';
        queryDateRangeGroup.style.display = isTS ? 'inline-flex' : 'none';
        queryAddBtn.style.display = isTS ? '' : 'none';
        if (run.lastVariable) queryVarSelect.value = run.lastVariable;
        if (isTS && run.startDate) queryStartCal.setValue(run.startDate);
        if (isTS && run.endDate) queryEndCal.setValue(run.endDate);

        // Re-render from cache
        queryTimeSeriesSec.style.display = 'none';
        queryStaticSec.style.display = 'none';
        queryAreaSec.style.display = 'none';
        queryEmpty.style.display = 'none';
        var hasContent = false;

        if (run.accumulatedTimeSeries && run.accumulatedTimeSeries.length) {
            queryTimeSeriesSec.style.display = 'block';
            renderTimeSeriesChart(run.accumulatedTimeSeries);
            hasContent = true;
        }
        if (run.staticResults && run.staticResults.length) {
            queryStaticSec.style.display = 'block';
            var html = '';
            run.staticResults.forEach(function (rsrc) {
                var srcObj = sourcesById[rsrc.source_id];
                html += '<div style="font-weight:600;margin-top:0.5rem;">' + esc(sourceDisplayName(srcObj || rsrc)) + '</div>';
                var bands = rsrc.bands || {};
                Object.keys(bands).forEach(function (meaning) {
                    var b = bands[meaning];
                    var val = b.value != null ? b.value : '—';
                    var label = b.label || '';
                    html += '<div class="static-result">' + esc(displayName(meaning, srcObj)) + ': <strong>' + esc(String(val)) + '</strong>';
                    if (label && label !== String(val)) html += ' — ' + esc(label);
                    html += '</div>';
                });
            });
            queryStaticResults.innerHTML = html;
            hasContent = true;
        }
        if (run.zonalData) {
            queryAreaSec.style.display = 'block';
            renderAreaResults(run.zonalData, src);
            hasContent = true;
        }
        if (!hasContent) {
            queryEmpty.style.display = 'block';
            queryEmpty.textContent = 'No cached data for this source.';
        }

        accumulatedTimeSeries = run.accumulatedTimeSeries || [];
        renderRunDropdown();
    }

    function minimizeFooter() {
        footerContent.style.height = '0';
    }

    // -----------------------------------------------------------------------
    // Source ↔ footer sync — show prompt when location lacks current source
    // -----------------------------------------------------------------------
    function syncFooterWithSource() {
        var loc = getActiveLocation();
        if (!loc) return;
        var h = parseInt(footerContent.style.height) || 0;
        if (h < 50) return;
        var mapSource = sourceSelect.value;
        if (!mapSource) return;
        var runIdx = findMatchingRun(loc, mapSource);
        if (runIdx >= 0) {
            restoreRunState(runIdx);
        } else {
            showSourcePrompt(mapSource);
        }
    }

    function showSourcePrompt(sourceId) {
        var src = sourcesById[sourceId];
        var name = sourceDisplayName(src) || sourceId;
        queryTimeSeriesSec.style.display = 'none';
        queryStaticSec.style.display = 'none';
        queryAreaSec.style.display = 'none';
        queryControlsRow.style.display = 'none';
        renderRunDropdown(); // keep run dropdown so user can switch to existing runs
        queryEmpty.style.display = 'block';
        queryEmpty.innerHTML = '';
        var msg = document.createElement('div');
        msg.style.cssText = 'margin-bottom:0.5rem;';
        msg.innerHTML = 'No results for <strong>' + esc(name) + '</strong> at this location.';
        var btn = document.createElement('button');
        btn.className = 'date-nav-btn';
        btn.style.cssText = 'padding:0.3rem 0.8rem;width:auto;font-size:0.78rem;background:var(--color-primary);border:1px solid var(--color-primary);color:#fff;';
        btn.textContent = 'Query ' + name + ' Here';
        btn.addEventListener('click', querySourceAtLocation);
        queryEmpty.appendChild(msg);
        queryEmpty.appendChild(btn);
    }

    function querySourceAtLocation() {
        var loc = getActiveLocation();
        if (!loc) return;
        var activeSource = sourceSelect.value;
        var src = sourcesById[activeSource];
        if (!src) return;
        var isTS = src.dataset_type === 'time_series';
        var activeVar = varSelect.value;

        var run = addOrUpdateRun(loc, activeSource);
        run.lastVariable = activeVar;
        accumulatedTimeSeries = [];

        var sd = null, ed = null;
        if (isTS) {
            var centerDate = dateInput.value ? new Date(dateInput.value + 'T00:00:00') : new Date();
            var start = new Date(centerDate); start.setDate(start.getDate() - 30);
            var end = new Date(centerDate); end.setDate(end.getDate() + 30);
            sd = start.toISOString().slice(0, 10);
            ed = end.toISOString().slice(0, 10);
            queryStartCal.setValue(sd); queryEndCal.setValue(ed);
        }
        run.startDate = sd; run.endDate = ed;
        showQueryControls();
        renderRunDropdown();

        if (loc.geometry) {
            queryStatus.textContent = 'Querying…';
            queryEmpty.innerHTML = ''; queryEmpty.textContent = 'Loading…';
            queryEmpty.style.display = 'block';
            queryTimeSeriesSec.style.display = 'none';
            queryAreaSec.style.display = 'none';
            if (isTS) {
                Promise.all([
                    fetch(SUBPATH + '/data/api/point-query/', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ lat: loc.lat, lon: loc.lon, sources: [activeSource], variable: activeVar, start_date: sd, end_date: ed }),
                    }).then(function (r) { return r.json(); }),
                    fetch(SUBPATH + '/data/api/zonal-stats/', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ source: activeSource, variable: activeVar, date: dateInput.value || null, geometry: loc.geometry, aggregation: areaAggregation }),
                    }).then(function (r) { return r.json(); }),
                ]).then(function (results) {
                    queryStatus.textContent = '';
                    var pointData = results[0], zonalData = results[1];
                    queryEmpty.style.display = 'none';
                    var tsData = pointData.time_series_results || [];
                    accumulatedTimeSeries = tsData;
                    var curRun = getActiveRun();
                    if (curRun) { cacheRunResults(curRun, pointData, false); if (!zonalData.error) curRun.zonalData = zonalData; }
                    if (tsData.length) { queryTimeSeriesSec.style.display = 'block'; renderTimeSeriesChart(tsData); }
                    if (!zonalData.error) { queryAreaSec.style.display = 'block'; renderAreaResults(zonalData, src); }
                }).catch(function (err) { queryStatus.textContent = ''; queryEmpty.textContent = 'Query failed: ' + err.message; });
            } else {
                fetch(SUBPATH + '/data/api/zonal-stats/', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source: activeSource, variable: activeVar, geometry: loc.geometry, aggregation: areaAggregation }),
                }).then(function (r) { return r.json(); }).then(function (data) {
                    queryStatus.textContent = ''; queryEmpty.style.display = 'none';
                    var curRun = getActiveRun();
                    if (curRun) curRun.zonalData = data;
                    if (!data.error) { queryAreaSec.style.display = 'block'; renderAreaResults(data, src); }
                }).catch(function (err) { queryStatus.textContent = ''; queryEmpty.textContent = 'Query failed: ' + err.message; });
            }
        } else {
            fetchPointQuery(loc.lat, loc.lon, sd, ed, activeSource ? [activeSource] : 'all', activeVar);
        }
    }

    // -----------------------------------------------------------------------
    // Point query
    // -----------------------------------------------------------------------
    function runPointQuery(lat, lon) {
        var loc = addOrFindLocation('point', lat, lon, null, null);
        lastQueryLat = lat; lastQueryLon = lon;
        lastQueryGeometry = null;

        clearQueryMarker(); clearQueryShape();
        queryMarker = L.marker([lat, lon]).addTo(map);
        queryStatus.textContent = 'Querying…';
        openFooter();
        showQueryControls();

        var activeSource = sourceSelect.value;
        var activeVar = queryVarSelect.value || varSelect.value;
        var src = sourcesById[activeSource];
        var isTimeSeries = src && src.dataset_type === 'time_series';

        var run = addOrUpdateRun(loc, activeSource);
        run.lastVariable = activeVar;
        accumulatedTimeSeries = [];

        var sd = null, ed = null;
        if (isTimeSeries) {
            var centerDate = dateInput.value ? new Date(dateInput.value + 'T00:00:00') : new Date();
            var start = new Date(centerDate); start.setDate(start.getDate() - 30);
            var end = new Date(centerDate); end.setDate(end.getDate() + 30);
            sd = start.toISOString().slice(0, 10);
            ed = end.toISOString().slice(0, 10);
            queryStartCal.setValue(sd); queryEndCal.setValue(ed);
        }
        run.startDate = sd; run.endDate = ed;
        renderLocationDropdown(); renderRunDropdown();

        fetchPointQuery(lat, lon, sd, ed, activeSource ? [activeSource] : 'all', activeVar);
    }

    function fetchPointQuery(lat, lon, sd, ed, srcs, variable) {
        queryEmpty.textContent = 'Loading…';
        queryEmpty.style.display = 'block';
        queryTimeSeriesSec.style.display = 'none';
        queryStaticSec.style.display = 'none';
        queryAreaSec.style.display = 'none';

        var body = { lat: lat, lon: lon, sources: srcs };
        if (sd) body.start_date = sd;
        if (ed) body.end_date = ed;
        if (variable) body.variable = variable;

        fetch(SUBPATH + '/data/api/point-query/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            queryStatus.textContent = '';
            if (data.error) { queryEmpty.textContent = 'Error: ' + data.error; return; }
            var run = getActiveRun();
            if (run) {
                cacheRunResults(run, data, false);
                accumulatedTimeSeries = run.accumulatedTimeSeries;
            } else {
                accumulatedTimeSeries = data.time_series_results || [];
            }
            renderPointResults(data);
        })
        .catch(function (err) {
            queryStatus.textContent = '';
            queryEmpty.textContent = 'Query failed: ' + err.message;
        });
    }

    function renderPointResults(data) {
        var tsData =data.time_series_results || [], staticData = data.static_results || [];
        if (!tsData.length && !staticData.length) {
            queryEmpty.textContent = 'No data found at this location.';
            return;
        }
        queryEmpty.style.display = 'none';

        if (tsData.length) {
            queryTimeSeriesSec.style.display = 'block';
            renderTimeSeriesChart(tsData);
        }
        if (staticData.length) {
            queryStaticSec.style.display = 'block';
            var html = '';
            staticData.forEach(function (src) {
                var srcObj = sourcesById[src.source_id];
                html += '<div style="font-weight:600;margin-top:0.5rem;">' + esc(sourceDisplayName(srcObj || src)) + '</div>';
                var bands = src.bands || {};
                Object.keys(bands).forEach(function (meaning) {
                    var b = bands[meaning];
                    var val = b.value != null ? b.value : '—';
                    var label = b.label || '';
                    html += '<div class="static-result">' + esc(displayName(meaning, srcObj)) + ': <strong>' + esc(String(val)) + '</strong>';
                    if (label && label !== String(val)) html += ' — ' + esc(label);
                    html += '</div>';
                });
            });
            queryStaticResults.innerHTML = html;
        }
    }

    // -----------------------------------------------------------------------
    // Area query (zonal stats via /api/data/zonal-stats/)
    // -----------------------------------------------------------------------
    function runAreaQuery(geometry, center, overrideVar, adminProps) {
        var loc = addOrFindLocation(queryMode, center.lat, center.lng, geometry, adminProps || null);
        lastQueryGeometry = geometry;
        lastQueryLat = center.lat; lastQueryLon = center.lng;

        clearQueryMarker(); clearQueryShape();
        if (geometry) {
            queryShape = L.geoJSON(geometry, {
                style: { color: '#f64137', weight: 3, opacity: 1, fillColor: '#f64137', fillOpacity: 0.15 }
            }).addTo(map);
        }

        queryStatus.textContent = 'Area query…';
        openFooter();
        showQueryControls();
        queryEmpty.style.display = 'block';
        queryEmpty.textContent = 'Loading area statistics…';
        queryTimeSeriesSec.style.display = 'none';
        queryStaticSec.style.display = 'none';
        queryAreaSec.style.display = 'none';

        var sid = sourceSelect.value;
        var variable = overrideVar || queryVarSelect.value || varSelect.value;
        var date = dateInput.value;
        var src = sourcesById[sid];

        if (!sid) {
            queryEmpty.textContent = 'Select a data source first.';
            queryStatus.textContent = '';
            return;
        }

        var isTimeSeries = src && src.dataset_type === 'time_series';
        var areaRun = addOrUpdateRun(loc, sid);
        areaRun.lastVariable = variable;
        accumulatedTimeSeries = [];

        if (isTimeSeries) {
            var centerDate = dateInput.value ? new Date(dateInput.value + 'T00:00:00') : new Date();
            var start = new Date(centerDate); start.setDate(start.getDate() - 30);
            var end = new Date(centerDate); end.setDate(end.getDate() + 30);
            var sd = start.toISOString().slice(0, 10);
            var ed = end.toISOString().slice(0, 10);
            queryStartCal.setValue(sd); queryEndCal.setValue(ed);
            areaRun.startDate = sd; areaRun.endDate = ed;
        }
        renderLocationDropdown(); renderRunDropdown();

        if (isTimeSeries) {
            Promise.all([
                fetch(SUBPATH + '/data/api/point-query/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        lat: center.lat, lon: center.lng,
                        sources: [sid], variable: variable,
                        start_date: queryStartDate.value,
                        end_date: queryEndDate.value,
                    }),
                }).then(function (r) { return r.json(); }),
                fetch(SUBPATH + '/data/api/zonal-stats/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        source: sid, variable: variable,
                        date: date || null, geometry: geometry,
                        aggregation: areaAggregation,
                    }),
                }).then(function (r) { return r.json(); }),
            ])
            .then(function (results) {
                queryStatus.textContent = '';
                var pointData = results[0];
                var zonalData = results[1];

                if (pointData.error && zonalData.error) {
                    queryEmpty.textContent = 'Error: ' + (pointData.error || zonalData.error);
                    return;
                }
                queryEmpty.style.display = 'none';

                var tsData =pointData.time_series_results || [];
                accumulatedTimeSeries = tsData;
                var curRun = getActiveRun();
                if (curRun) {
                    cacheRunResults(curRun, pointData, false);
                    if (!zonalData.error) curRun.zonalData = zonalData;
                }
                if (tsData.length) {
                    queryTimeSeriesSec.style.display = 'block';
                    renderTimeSeriesChart(tsData);
                }

                if (!zonalData.error) {
                    queryAreaSec.style.display = 'block';
                    renderAreaResults(zonalData, src);
                }
            })
            .catch(function (err) {
                queryStatus.textContent = '';
                queryEmpty.textContent = 'Area query failed: ' + err.message;
            });
        } else {
            // Categorical/reference: zonal stats only, no date
            fetch(SUBPATH + '/data/api/zonal-stats/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source: sid, variable: variable,
                    geometry: geometry,
                    aggregation: areaAggregation,
                }),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                queryStatus.textContent = '';
                if (data.error) {
                    queryEmpty.textContent = 'Error: ' + data.error;
                    return;
                }
                queryEmpty.style.display = 'none';
                var curRun = getActiveRun();
                if (curRun) curRun.zonalData = data;
                queryAreaSec.style.display = 'block';
                renderAreaResults(data, src);
            })
            .catch(function (err) {
                queryStatus.textContent = '';
                queryEmpty.textContent = 'Area query failed: ' + err.message;
            });
        }
    }

    var areaHistChart = null; // Chart.js instance for area histogram

    function renderAreaResults(data, src) {
        queryAreaResults.innerHTML = '';
        if (areaHistChart) { areaHistChart.destroy(); areaHistChart = null; }

        var cats = data.categories || [];
        var hasCats = cats.length > 0;

        // --- Header ---
        var header = document.createElement('div');
        header.style.cssText = 'font-weight:600;margin-bottom:0.4rem;';
        if (data.type === 'categorical') {
            header.textContent = sourceDisplayName(src) + ' — Category Distribution';
        } else {
            header.textContent = sourceDisplayName(src) + ' — ' + displayName(data.variable, src);
        }
        queryAreaResults.appendChild(header);

        if (data.date) {
            var dateDiv = document.createElement('div');
            dateDiv.style.cssText = 'font-size:0.75rem;color:var(--text-secondary);margin-bottom:0.3rem;';
            dateDiv.textContent = 'Date: ' + data.date;
            queryAreaResults.appendChild(dateDiv);
        }

        // --- Summary stats (continuous only) ---
        if (data.type === 'continuous' && data.stats) {
            var stats = data.stats;
            var st = document.createElement('table');
            st.style.cssText = 'font-size:0.82rem;border-collapse:collapse;margin-bottom:0.5rem;';
            var rows = [
                ['Mean', '<strong>' + stats.mean + '</strong>'],
                ['Min', stats.min],
                ['Max', stats.max],
                ['Std Dev', stats.stddev || '—'],
                ['Pixels', stats.count],
            ];
            rows.forEach(function (r) {
                var tr = document.createElement('tr');
                tr.innerHTML = '<td style="padding:0.2rem 0.6rem 0.2rem 0;color:var(--text-secondary);">' + r[0] + '</td><td>' + r[1] + '</td>';
                st.appendChild(tr);
            });
            queryAreaResults.appendChild(st);
        } else if (data.type === 'continuous' && !data.stats) {
            var noData = document.createElement('div');
            noData.style.color = 'var(--text-secondary)';
            noData.textContent = 'No data within the selected area.';
            queryAreaResults.appendChild(noData);
            return;
        }

        // --- Distribution section (table/chart toggle) ---
        if (!hasCats) return;

        // Toggle bar
        var toggleBar = document.createElement('div');
        toggleBar.style.cssText = 'display:flex;gap:0.3rem;margin-bottom:0.4rem;';
        var btnTable = document.createElement('button');
        btnTable.className = 'query-mode-btn active';
        btnTable.textContent = 'Table';
        btnTable.style.cssText = 'padding:0.2rem 0.5rem;font-size:0.72rem;background:var(--color-primary);border:1px solid var(--color-primary);color:#fff;';
        var btnChart = document.createElement('button');
        btnChart.className = 'query-mode-btn';
        btnChart.textContent = 'Chart';
        btnChart.style.cssText = 'padding:0.2rem 0.5rem;font-size:0.72rem;background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);';
        toggleBar.appendChild(btnTable);
        toggleBar.appendChild(btnChart);
        queryAreaResults.appendChild(toggleBar);

        // Table view
        var tableDiv = document.createElement('div');
        var tbl = '<table style="width:100%;border-collapse:collapse;font-size:0.8rem;">';
        tbl += '<thead><tr><th style="text-align:left;border-bottom:1px solid var(--glass-border);padding:0.3rem;">Value</th>' +
               '<th style="text-align:right;border-bottom:1px solid var(--glass-border);padding:0.3rem;">Count</th>' +
               '<th style="text-align:right;border-bottom:1px solid var(--glass-border);padding:0.3rem;">%</th></tr></thead><tbody>';
        cats.forEach(function (c) {
            var cellLabel = c.label ? esc(c.label) : esc(String(c.value));
            tbl += '<tr><td style="padding:0.2rem;">' + cellLabel + '</td>' +
                   '<td style="text-align:right;padding:0.2rem;">' + c.count + '</td>' +
                   '<td style="text-align:right;padding:0.2rem;">' + c.percentage + '%</td></tr>';
        });
        tbl += '</tbody></table>';
        tbl += '<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:0.3rem;">Total pixels: ' + (data.total_pixels || '') + '</div>';
        tableDiv.innerHTML = tbl;
        queryAreaResults.appendChild(tableDiv);

        // Chart view (hidden initially)
        var chartDiv = document.createElement('div');
        chartDiv.style.cssText = 'display:none;position:relative;height:180px;';
        var canvas = document.createElement('canvas');
        chartDiv.appendChild(canvas);
        queryAreaResults.appendChild(chartDiv);

        // Toggle handlers
        var activeToggleCss = 'background:var(--color-primary);border:1px solid var(--color-primary);color:#fff;';
        var inactiveToggleCss = 'background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);';
        function setToggleStyles(active, inactive) {
            active.style.cssText = 'padding:0.2rem 0.5rem;font-size:0.72rem;' + activeToggleCss;
            inactive.style.cssText = 'padding:0.2rem 0.5rem;font-size:0.72rem;' + inactiveToggleCss;
        }
        btnTable.addEventListener('click', function () {
            btnTable.classList.add('active'); btnChart.classList.remove('active');
            setToggleStyles(btnTable, btnChart);
            tableDiv.style.display = ''; chartDiv.style.display = 'none';
        });
        btnChart.addEventListener('click', function () {
            btnChart.classList.add('active'); btnTable.classList.remove('active');
            setToggleStyles(btnChart, btnTable);
            tableDiv.style.display = 'none'; chartDiv.style.display = 'block';
            // Lazy-create histogram on first view
            if (!areaHistChart) {
                var labels = cats.map(function (c) { return c.label || String(c.value); });
                var counts = cats.map(function (c) { return c.count; });
                areaHistChart = new Chart(canvas, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Count',
                            data: counts,
                            backgroundColor: 'rgba(1,112,185,0.6)',
                            borderColor: 'rgba(1,112,185,0.9)',
                            borderWidth: 1,
                        }],
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            x: { ticks: { color: '#58585b', font: { size: 9 }, maxRotation: 45 }, grid: { display: false } },
                            y: { ticks: { color: '#58585b', font: { size: 10 } }, grid: { color: 'rgba(221,221,221,0.8)' } },
                        },
                    },
                });
            }
        });
    }

    // -----------------------------------------------------------------------
    // Chart.js time-series
    // -----------------------------------------------------------------------
    function renderTimeSeriesChart(timeSeriesResults) {
        var datasets = [], allDates = new Set();
        timeSeriesResults.forEach(function (src) {
            var srcObj = sourcesById[src.source_id];
            var vars = src.variables || {};
            Object.keys(vars).forEach(function (vn) {
                vars[vn].forEach(function (pt) { allDates.add(pt.date); });
                datasets.push({
                    label: displayName(vn, srcObj),
                    data: vars[vn].map(function (pt) { return { x: pt.date, y: pt.value }; }),
                    borderColor: CHART_COLORS[vn] || 'rgba(88,88,91,0.8)',
                    backgroundColor: 'transparent',
                    borderWidth: 1.5, pointRadius: 0, tension: 0.3,
                });
            });
        });
        var labels = Array.from(allDates).sort();
        if (queryChart) queryChart.destroy();
        queryChart = new Chart(queryChartCanvas, {
            type: 'line',
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { type: 'category', ticks: { color: '#58585b', maxTicksLimit: 12, font: { size: 10 } }, grid: { color: 'rgba(221,221,221,0.8)' } },
                    y: { ticks: { color: '#58585b', font: { size: 10 } }, grid: { color: 'rgba(221,221,221,0.8)' } },
                },
                plugins: {
                    legend: { labels: { color: '#3a3a3a', font: { size: 11 } } },
                    tooltip: { backgroundColor: '#ffffff', borderColor: '#dddddd', borderWidth: 1, titleColor: '#0170B9', bodyColor: '#3a3a3a' },
                },
            },
        });
    }

    // -----------------------------------------------------------------------
    // Footer slider management
    // -----------------------------------------------------------------------
    function openFooter() {
        footerContent.style.height = footerOpenHeight + 'px';
        // Sync displayed results with current map source on re-open
        if (getActiveLocation()) syncFooterWithSource();
    }

    // Minimize button — collapse footer but preserve all query history
    queryMinimizeBtn.addEventListener('click', function (e) {
        e.stopPropagation(); // prevent handle click toggle from firing
        minimizeFooter();
    });

    // Prevent location dropdown from triggering handle drag/click
    queryLocSelect.addEventListener('mousedown', function (e) { e.stopPropagation(); });
    queryLocSelect.addEventListener('click', function (e) { e.stopPropagation(); });

    // Location dropdown — navigate between query locations
    queryLocSelect.addEventListener('change', function () {
        var selectedId = queryLocSelect.value;
        for (var i = 0; i < queryHistory.length; i++) {
            if (queryHistory[i].id === selectedId) {
                openFooter();
                restoreLocationState(i);
                break;
            }
        }
    });

    // Dataset run dropdown — navigate between source runs at same location
    queryRunSelect.addEventListener('change', function () {
        var selectedId = queryRunSelect.value;
        var loc = getActiveLocation();
        if (!loc) return;
        for (var i = 0; i < loc.datasetRuns.length; i++) {
            if (loc.datasetRuns[i].id === selectedId) {
                restoreRunState(i);
                break;
            }
        }
    });

    // Drag-to-resize
    var isDragging = false;
    footerHandle.addEventListener('mousedown', function (e) {
        isDragging = true;
        e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
        if (!isDragging) return;
        var mapPage = document.querySelector('.map-page');
        var pageBottom = mapPage.getBoundingClientRect().bottom;
        var newH = pageBottom - e.clientY - 36; // 36 = handle height
        newH = Math.max(0, Math.min(newH, window.innerHeight * 0.7));
        footerContent.style.height = newH + 'px';
        footerOpenHeight = newH > 50 ? newH : footerOpenHeight;
    });
    document.addEventListener('mouseup', function () { isDragging = false; });

    // Click handle to toggle
    footerHandle.addEventListener('click', function () {
        if (!isDragging) {
            var h = parseInt(footerContent.style.height) || 0;
            if (h > 50) minimizeFooter();
            else openFooter();
        }
    });

    // Reload query — same source updates run, different source creates new run
    queryReloadBtn.addEventListener('click', function () {
        var loc = getActiveLocation();
        if (!loc) return;
        var qvar = queryVarSelect.value || varSelect.value;
        var activeSource = sourceSelect.value;
        var src = sourcesById[activeSource];
        var isTS = src && src.dataset_type === 'time_series';
        var currentRun = getActiveRun();

        // Different source → new run; same source → update
        if (!currentRun || currentRun.sourceId !== activeSource) {
            var newRun = addOrUpdateRun(loc, activeSource);
            newRun.lastVariable = qvar;
            if (isTS) { newRun.startDate = queryStartDate.value; newRun.endDate = queryEndDate.value; }
            renderRunDropdown();
        } else {
            currentRun.lastVariable = qvar;
            if (isTS) { currentRun.startDate = queryStartDate.value; currentRun.endDate = queryEndDate.value; }
        }

        accumulatedTimeSeries = [];

        if (loc.geometry) {
            // Area query reload
            queryStatus.textContent = 'Reloading…';
            queryTimeSeriesSec.style.display = 'none';
            queryAreaSec.style.display = 'none';
            queryEmpty.style.display = 'block';
            queryEmpty.textContent = 'Loading…';

            if (isTS) {
                Promise.all([
                    fetch(SUBPATH + '/data/api/point-query/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            lat: loc.lat, lon: loc.lon,
                            sources: [activeSource], variable: qvar,
                            start_date: queryStartDate.value, end_date: queryEndDate.value,
                        }),
                    }).then(function (r) { return r.json(); }),
                    fetch(SUBPATH + '/data/api/zonal-stats/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            source: activeSource, variable: qvar,
                            date: dateInput.value || null, geometry: loc.geometry,
                            aggregation: areaAggregation,
                        }),
                    }).then(function (r) { return r.json(); }),
                ]).then(function (results) {
                    queryStatus.textContent = '';
                    var pointData = results[0], zonalData = results[1];
                    queryEmpty.style.display = 'none';
                    var tsData =pointData.time_series_results || [];
                    accumulatedTimeSeries = tsData;
                    var run = getActiveRun();
                    if (run) { cacheRunResults(run, pointData, false); if (!zonalData.error) run.zonalData = zonalData; }
                    if (tsData.length) { queryTimeSeriesSec.style.display = 'block'; renderTimeSeriesChart(tsData); }
                    if (!zonalData.error) { queryAreaSec.style.display = 'block'; renderAreaResults(zonalData, src); }
                }).catch(function (err) { queryStatus.textContent = ''; queryEmpty.textContent = 'Reload failed: ' + err.message; });
            } else {
                fetch(SUBPATH + '/data/api/zonal-stats/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source: activeSource, variable: qvar, geometry: loc.geometry, aggregation: areaAggregation }),
                }).then(function (r) { return r.json(); }).then(function (data) {
                    queryStatus.textContent = '';
                    queryEmpty.style.display = 'none';
                    var run = getActiveRun();
                    if (run) run.zonalData = data;
                    if (!data.error) { queryAreaSec.style.display = 'block'; renderAreaResults(data, src); }
                }).catch(function (err) { queryStatus.textContent = ''; queryEmpty.textContent = 'Reload failed: ' + err.message; });
            }
        } else {
            // Point query reload
            fetchPointQuery(loc.lat, loc.lon,
                isTS ? queryStartDate.value : null,
                isTS ? queryEndDate.value : null,
                activeSource ? [activeSource] : 'all', qvar);
        }
    });

    // Add variable to existing chart (time-series only)
    queryAddBtn.addEventListener('click', function () {
        var loc = getActiveLocation();
        if (!loc) return;
        var qvar = queryVarSelect.value;
        var activeSource = sourceSelect.value;
        if (!qvar || !activeSource) return;
        queryStatus.textContent = 'Adding…';
        var body = {
            lat: loc.lat, lon: loc.lon,
            sources: [activeSource], variable: qvar,
            start_date: queryStartDate.value, end_date: queryEndDate.value,
        };
        fetch(SUBPATH + '/data/api/point-query/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            queryStatus.textContent = '';
            if (data.error) return;
            var tsData =data.time_series_results || [];
            accumulatedTimeSeries = accumulatedTimeSeries.concat(tsData);
            var run = getActiveRun();
            if (run) run.accumulatedTimeSeries = accumulatedTimeSeries;
            if (accumulatedTimeSeries.length) {
                queryTimeSeriesSec.style.display = 'block';
                renderTimeSeriesChart(accumulatedTimeSeries);
            }
        })
        .catch(function () { queryStatus.textContent = ''; });
    });

    function clearQueryMarker() { if (queryMarker) { map.removeLayer(queryMarker); queryMarker = null; } }
    function clearQueryShape() { if (queryShape) { map.removeLayer(queryShape); queryShape = null; } }

    // -----------------------------------------------------------------------
    // Event listeners
    // -----------------------------------------------------------------------
    sourceSelect.addEventListener('change', onSourceChange);
    varSelect.addEventListener('change', function () { legend.update(varSelect.value); buildRasterLayer(); });
    // dateInput is now a hidden field — changes are driven by setDate() via
    // the calendar widget and the prev/next buttons. No direct listener needed.
    adminSelect.addEventListener('change', loadAdminBoundaries);
    opacitySlider.addEventListener('input', function () {
        var pct = parseInt(opacitySlider.value, 10);
        opacityLabel.textContent = pct + '%';
        if (rasterLayer) rasterLayer.setOpacity(pct / 100);
    });

    // -----------------------------------------------------------------------
    // Utility
    // -----------------------------------------------------------------------
    function esc(s) {
        if (s == null) return '';
        var d = document.createElement('div');
        d.textContent = String(s);
        return d.innerHTML;
    }

    // -----------------------------------------------------------------------
    // Init
    // -----------------------------------------------------------------------
    loadSources().then(function () {
        if (sources.length && !sourceSelect.value) {
            sourceSelect.value = sources[0].id;
            onSourceChange();
        }
    });
    loadAdminBoundaries();
    initDrawHandlers();
    setQueryMode('point');

})();
