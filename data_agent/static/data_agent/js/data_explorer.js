/**
 * Data Explorer — interactive data availability checker at /data/data-availability/.
 *
 * Features:
 * - Source dropdown with live availability checking
 * - Custom MiniCalendar with availability dots (all dates clickable)
 * - Mini Leaflet map for bbox draw / point click / admin boundary selection
 * - Auto-loading availability on source/date/location change
 * - "Check at Source" remote probe
 */

function getCsrfToken() {
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
}

(function () {
    'use strict';

    // Deployment subpath prefix (e.g. "/subpath" or ""). Prepend to module-absolute URLs.
    var SUBPATH = window.SUBPATH || '';

    // -----------------------------------------------------------------------
    // DOM refs
    // -----------------------------------------------------------------------
    var sourceSelect     = document.getElementById('data-source');
    var sourceHint       = document.getElementById('data-source-hint');
    var noDataWarning    = document.getElementById('source-no-data-warning');
    var dateRow          = document.getElementById('data-date-row');
    var startDateInput   = document.getElementById('start-date');
    var endDateInput     = document.getElementById('end-date');
    var availFraction    = document.getElementById('avail-fraction');
    var resultsDiv       = document.getElementById('data-results');
    var remoteResultsDiv = document.getElementById('remote-check-results');
    var remoteBtnEl      = document.getElementById('check-remote-btn');
    var locationToggle   = document.getElementById('location-type-toggle');
    var bboxInputs       = document.getElementById('location-bbox-inputs');
    var pointInputs      = document.getElementById('location-point-inputs');
    var adminInputs      = document.getElementById('location-admin-inputs');
    var adminLevelSelect = document.getElementById('admin-level');
    var adminGeomInput   = document.getElementById('admin-geometry');
    var adminLabelInput  = document.getElementById('admin-label');
    var tableBody        = document.querySelector('#data-sources-table tbody');

    if (!sourceSelect) return; // not on the explorer page

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------
    var sources = [];
    var sourcesById = {};
    var locationType = 'bbox'; // 'bbox' | 'point' | 'admin'
    var calAvailCache = {};
    var checkTimer = null; // debounce timer for auto-check

    // -----------------------------------------------------------------------
    // Utility
    // -----------------------------------------------------------------------
    function esc(text) {
        var d = document.createElement('div');
        d.textContent = text == null ? '' : String(text);
        return d.innerHTML;
    }

    // -----------------------------------------------------------------------
    // MiniCalendar — reused from map_explorer, modified: all dates clickable
    // -----------------------------------------------------------------------
    var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var allCalendars = [];

    function MiniCalendar(triggerEl, hiddenInput, containerEl, onSelect) {
        var self = this;
        self.trigger = triggerEl;
        self.input = hiddenInput;
        self.container = containerEl;
        self.onSelect = onSelect;

        self.container.innerHTML =
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.4rem;">' +
                '<button type="button" class="mc-prev" style="background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);width:24px;height:24px;border-radius:var(--radius);cursor:pointer;font-size:0.8rem;">&#9664;</button>' +
                '<div style="display:flex;gap:0.3rem;">' +
                    '<select class="mc-month" aria-label="Month" style="background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);padding:0.15rem;border-radius:var(--radius);font-size:0.75rem;">' +
                        MONTHS.map(function(m,i) { return '<option value="'+i+'">'+m+'</option>'; }).join('') +
                    '</select>' +
                    '<select class="mc-year" aria-label="Year" style="background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);padding:0.15rem;border-radius:var(--radius);font-size:0.75rem;">' +
                        (function() { var h=''; for(var y=2000;y<=2030;y++) h+='<option value="'+y+'">'+y+'</option>'; return h; })() +
                    '</select>' +
                '</div>' +
                '<button type="button" class="mc-next" style="background:var(--color-bg);border:1px solid var(--color-border);color:var(--color-text);width:24px;height:24px;border-radius:var(--radius);cursor:pointer;font-size:0.8rem;">&#9654;</button>' +
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

        self.trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            var isOpen = !self.container.classList.contains('is-hidden');
            closeAllCalendars();
            if (!isOpen) {
                var cur = self.input.value || new Date().toISOString().slice(0, 10);
                self.monthSel.value = parseInt(cur.slice(5, 7), 10) - 1;
                self.yearSel.value = parseInt(cur.slice(0, 4), 10);
                self.container.classList.remove('is-hidden');
                self.render();
            }
        });
        self.container.addEventListener('click', function (e) { e.stopPropagation(); });
        allCalendars.push(self);
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
                // Mark available dates with green dot but ALL dates are clickable
                if (availSet.has(iso) || availSet.has('static')) {
                    cell.classList.add('available');
                }
                if (iso === currentDate) cell.classList.add('selected');
                cell.addEventListener('click', (function (dateVal) {
                    return function () {
                        self.input.value = dateVal;
                        self.trigger.textContent = dateVal;
                        self.container.classList.add('is-hidden');
                        if (self.onSelect) self.onSelect(dateVal);
                    };
                })(iso));
                self.grid.appendChild(cell);
            }
        }

        if (sid) fetchAvailDates(sid, monthKey).then(doRender); else doRender();
    };

    MiniCalendar.prototype.setValue = function (iso) {
        this.input.value = iso;
        this.trigger.textContent = iso || '—';
    };

    function closeAllCalendars() {
        allCalendars.forEach(function (mc) { mc.container.classList.add('is-hidden'); });
    }
    document.addEventListener('click', closeAllCalendars);

    function fetchAvailDates(sid, month) {
        var cacheKey = sid + ':' + month;
        if (calAvailCache[cacheKey]) return Promise.resolve();
        return fetch(SUBPATH + '/data/api/dates/?source=' + encodeURIComponent(sid) + '&month=' + month)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.static) { calAvailCache[cacheKey] = new Set(['static']); return; }
                var dates = (data.dates || []), byMonth = {};
                dates.forEach(function (d) {
                    var mk = d.slice(0, 7);
                    if (!byMonth[mk]) byMonth[mk] = new Set();
                    byMonth[mk].add(d);
                });
                Object.keys(byMonth).forEach(function (mk) { calAvailCache[sid + ':' + mk] = byMonth[mk]; });
                if (!calAvailCache[cacheKey]) calAvailCache[cacheKey] = new Set();
            })
            .catch(function () { calAvailCache[cacheKey] = new Set(); });
    }

    // Create calendar instances
    var startCal = new MiniCalendar(
        document.getElementById('start-trigger'), startDateInput,
        document.getElementById('start-cal'),
        function () { scheduleAvailCheck(); }
    );
    var endCal = new MiniCalendar(
        document.getElementById('end-trigger'), endDateInput,
        document.getElementById('end-cal'),
        function () { scheduleAvailCheck(); }
    );

    // -----------------------------------------------------------------------
    // Mini Leaflet Map
    // -----------------------------------------------------------------------
    var miniMap = L.map('location-map', { center: [20, 0], zoom: 2, zoomControl: true });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; CARTO &copy; OSM', subdomains: 'abcd', maxZoom: 19,
    }).addTo(miniMap);

    var drawnRect = null;
    var drawnMarker = null;
    var adminLayer = null;
    var selectedAdminLayer = null;
    var drawRectHandler = null;

    // Leaflet Draw for bbox
    if (typeof L.Draw !== 'undefined') {
        var drawOpts = { shapeOptions: { color: '#0170B9', weight: 2, fillColor: '#0170B9', fillOpacity: 0.12 } };
        drawRectHandler = new L.Draw.Rectangle(miniMap, drawOpts);

        miniMap.on(L.Draw.Event.CREATED, function (e) {
            if (drawnRect) miniMap.removeLayer(drawnRect);
            drawnRect = e.layer;
            drawnRect.addTo(miniMap);
            var b = drawnRect.getBounds();
            document.getElementById('bbox-west').value = b.getWest().toFixed(4);
            document.getElementById('bbox-south').value = b.getSouth().toFixed(4);
            document.getElementById('bbox-east').value = b.getEast().toFixed(4);
            document.getElementById('bbox-north').value = b.getNorth().toFixed(4);
            scheduleAvailCheck();
        });
    }

    // Point click on map
    miniMap.on('click', function (e) {
        if (locationType !== 'point') return;
        if (drawnMarker) miniMap.removeLayer(drawnMarker);
        drawnMarker = L.marker([e.latlng.lat, e.latlng.lng]).addTo(miniMap);
        document.getElementById('point-lat').value = e.latlng.lat.toFixed(6);
        document.getElementById('point-lon').value = e.latlng.lng.toFixed(6);
        scheduleAvailCheck();
    });

    // Sync text inputs → map for bbox
    ['bbox-west', 'bbox-south', 'bbox-east', 'bbox-north'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', function () { syncBboxToMap(); scheduleAvailCheck(); });
    });
    ['point-lat', 'point-lon'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', function () { syncPointToMap(); scheduleAvailCheck(); });
    });

    function syncBboxToMap() {
        var w = parseFloat(document.getElementById('bbox-west').value);
        var s = parseFloat(document.getElementById('bbox-south').value);
        var e = parseFloat(document.getElementById('bbox-east').value);
        var n = parseFloat(document.getElementById('bbox-north').value);
        if (isNaN(w) || isNaN(s) || isNaN(e) || isNaN(n)) return;
        if (drawnRect) miniMap.removeLayer(drawnRect);
        drawnRect = L.rectangle([[s, w], [n, e]], { color: '#0170B9', weight: 2, fillColor: '#0170B9', fillOpacity: 0.12 }).addTo(miniMap);
        miniMap.fitBounds(drawnRect.getBounds(), { padding: [20, 20] });
    }

    function syncPointToMap() {
        var lat = parseFloat(document.getElementById('point-lat').value);
        var lon = parseFloat(document.getElementById('point-lon').value);
        if (isNaN(lat) || isNaN(lon)) return;
        if (drawnMarker) miniMap.removeLayer(drawnMarker);
        drawnMarker = L.marker([lat, lon]).addTo(miniMap);
        miniMap.setView([lat, lon], 8);
    }

    // -----------------------------------------------------------------------
    // Location type toggle
    // -----------------------------------------------------------------------
    locationToggle.addEventListener('click', function (e) {
        var tab = e.target.closest('.tab');
        if (!tab) return;
        locationType = tab.dataset.locationType;
        locationToggle.querySelectorAll('.tab').forEach(function (t) {
            t.classList.toggle('active', t.dataset.locationType === locationType);
        });
        bboxInputs.style.display = locationType === 'bbox' ? '' : 'none';
        pointInputs.classList.toggle('is-hidden', locationType !== 'point');
        adminInputs.classList.toggle('is-hidden', locationType !== 'admin');

        // Enable/disable draw handler
        if (drawRectHandler) drawRectHandler.disable();
        if (locationType === 'bbox' && drawRectHandler) drawRectHandler.enable();

        // Load admin boundaries if switching to admin tab
        if (locationType === 'admin') loadAdminBoundaries();
    });

    // -----------------------------------------------------------------------
    // Admin boundary loading (NDJSON streaming, same as map explorer)
    // -----------------------------------------------------------------------
    var adminStyle = { color: '#fff', weight: 1.2, opacity: 0.7, fillColor: 'transparent', fillOpacity: 0 };
    var adminHoverStyle = { color: '#0170B9', weight: 2.5, opacity: 1, fillColor: '#0170B9', fillOpacity: 0.12 };
    var adminSelectStyle = { color: '#f64137', weight: 3, opacity: 1, fillColor: '#f64137', fillOpacity: 0.15 };

    function loadAdminBoundaries() {
        var level = adminLevelSelect.value;
        if (adminLayer) { miniMap.removeLayer(adminLayer); adminLayer = null; }
        if (selectedAdminLayer) { miniMap.removeLayer(selectedAdminLayer); selectedAdminLayer = null; }
        if (!level) return;

        adminLayer = L.geoJSON(null, {
            style: adminStyle,
            onEachFeature: function (feature, layer) {
                var props = feature.properties || {};
                var parts = [];
                if (props.country) parts.push(props.country);
                if (props.admin1) parts.push(props.admin1);
                if (props.admin2) parts.push(props.admin2);
                var label = parts.join(' > ');

                layer.on('mouseover', function () {
                    layer.setStyle(adminHoverStyle);
                    layer.bringToFront();
                    layer.bindTooltip(label, { sticky: true, direction: 'top', opacity: 0.9 }).openTooltip();
                });
                layer.on('mouseout', function () {
                    if (adminLayer) adminLayer.resetStyle(layer);
                    layer.closeTooltip();
                    layer.unbindTooltip();
                    // Re-apply selected style if this was the selected one
                    if (selectedAdminLayer === layer) layer.setStyle(adminSelectStyle);
                });
                layer.on('click', function (e) {
                    L.DomEvent.stopPropagation(e);
                    if (adminLayer) adminLayer.resetStyle();
                    layer.setStyle(adminSelectStyle);
                    selectedAdminLayer = layer;
                    adminGeomInput.value = JSON.stringify(feature.geometry);
                    adminLabelInput.value = label;
                    document.getElementById('admin-hint').textContent = 'Selected: ' + label;
                    scheduleAvailCheck();
                });
            },
        });
        adminLayer.addTo(miniMap);

        // NDJSON streaming
        fetch(SUBPATH + '/data/api/map/admin/?level=' + encodeURIComponent(level) + '&stream=1', {
            headers: { 'Accept': 'application/x-ndjson' },
        })
        .then(function (response) {
            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';
            function processChunk(result) {
                if (result.done) {
                    if (buffer.trim()) {
                        try { adminLayer.addData(JSON.parse(buffer.trim())); } catch (e) {}
                    }
                    return;
                }
                buffer += decoder.decode(result.value, { stream: true });
                var lines = buffer.split('\n');
                buffer = lines.pop();
                lines.forEach(function (line) {
                    if (!line.trim()) return;
                    try { adminLayer.addData(JSON.parse(line)); } catch (e) {}
                });
                return reader.read().then(processChunk);
            }
            return reader.read().then(processChunk);
        })
        .catch(function (err) { console.error('Admin stream error:', err); });
    }

    if (adminLevelSelect) {
        adminLevelSelect.addEventListener('change', loadAdminBoundaries);
    }

    // -----------------------------------------------------------------------
    // Source loader + table
    // -----------------------------------------------------------------------
    function loadSources() {
        return fetch(SUBPATH + '/data/api/sources/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                sources = (data && data.sources) || [];
                sourcesById = {};
                sources.forEach(function (s) { sourcesById[s.id] = s; });
                renderSourceDropdown();
                renderSourceTable();
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
                opt.textContent = s.display_name || s.name;
                og.appendChild(opt);
            });
            sourceSelect.appendChild(og);
        });
    }

    function renderSourceTable() {
        if (!tableBody) return;
        if (!sources.length) {
            tableBody.innerHTML = '<tr><td colspan="4" class="empty-state small">No sources registered.</td></tr>';
            return;
        }
        tableBody.innerHTML = sources.map(function (s) {
            var dispName = s.display_name || s.name || s.id;
            var finals = (s.variables || []).map(function (v) {
                return (s.variable_display_names || {})[v] || v;
            }).join(', ') || '—';
            var srcs = (s.source_variables || []).join(', ') || '—';
            return '<tr><td>' + esc(dispName) + '</td><td>' + esc(finals) +
                   '</td><td>' + esc(srcs) + '</td><td>' + esc(s.resolution || '—') + '</td></tr>';
        }).join('');
    }

    // -----------------------------------------------------------------------
    // Source change handler — auto-load availability
    // -----------------------------------------------------------------------
    sourceSelect.addEventListener('change', function () {
        calAvailCache = {};
        noDataWarning.style.display = 'none';
        remoteBtnEl.classList.add('is-hidden');
        remoteResultsDiv.innerHTML = '';

        var src = sourcesById[sourceSelect.value];
        if (!src) {
            dateRow.style.display = '';
            sourceHint.textContent = 'Pick a source to see its variables and check availability.';
            availFraction.innerHTML = '';
            resultsDiv.innerHTML = '<div class="empty-state small"><p>Select a data source to see availability.</p></div>';
            return;
        }

        var isStatic = src.dataset_type === 'static';
        dateRow.style.display = isStatic ? 'none' : '';

        // Update hint
        var finals = (src.variables || []).join(', ') || '—';
        sourceHint.innerHTML = '<strong>Variables:</strong> ' + esc(finals) +
            '<br><strong>Type:</strong> ' + esc(src.dataset_type || 'unknown');

        if (isStatic) {
            // Static raster — show coverage info directly
            var cov = src.coverage_extent;
            var covStr = 'global / unbounded';
            if (cov && cov.type === 'bbox') {
                covStr = 'bbox: ' + cov.min_lat + ',' + cov.min_lon + ' → ' + cov.max_lat + ',' + cov.max_lon;
            }
            resultsDiv.innerHTML =
                '<table class="data-table">' +
                '<tr><th>Source</th><td>' + esc(src.name) + '</td></tr>' +
                '<tr><th>Type</th><td>Static raster</td></tr>' +
                '<tr><th>Variables</th><td>' + esc(finals) + '</td></tr>' +
                '<tr><th>Coverage</th><td>' + esc(covStr) + '</td></tr>' +
                '</table>';
            availFraction.innerHTML = '';
            return;
        }

        // Time-series: fetch first/last available date, init calendars
        resultsDiv.innerHTML = '<div class="empty-state small"><p>Loading availability...</p></div>';
        fetchLatestDate(sourceSelect.value);
    });

    function fetchLatestDate(sid) {
        fetch(SUBPATH + '/data/api/check/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ source: sid }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success || !data.results || !data.results.length) {
                noDataWarning.style.display = 'block';
                startCal.setValue(''); endCal.setValue('');
                availFraction.innerHTML = '';
                resultsDiv.innerHTML = '';
                return;
            }
            var result = data.results[0];
            if (!result.available || !result.first_date) {
                noDataWarning.style.display = 'block';
                startCal.setValue(''); endCal.setValue('');
                availFraction.innerHTML = '';
                resultsDiv.innerHTML = '';
                return;
            }
            noDataWarning.style.display = 'none';
            // Initialize calendars to first/last available date
            startCal.setValue(result.first_date);
            endCal.setValue(result.last_date);
            // Run the availability check
            runAvailCheck();
        })
        .catch(function (err) {
            console.warn('Failed to fetch latest date:', err);
            noDataWarning.style.display = 'block';
        });
    }

    // -----------------------------------------------------------------------
    // Auto-load availability (debounced)
    // -----------------------------------------------------------------------
    function scheduleAvailCheck() {
        if (checkTimer) clearTimeout(checkTimer);
        checkTimer = setTimeout(runAvailCheck, 400);
    }

    function getLocationParams() {
        var params = {};
        if (locationType === 'point') {
            var lat = document.getElementById('point-lat').value;
            var lon = document.getElementById('point-lon').value;
            if (lat) params.lat = parseFloat(lat);
            if (lon) params.lon = parseFloat(lon);
        } else if (locationType === 'admin') {
            var geom = adminGeomInput.value;
            if (geom) {
                try { params.geometry = JSON.parse(geom); } catch (e) {}
            }
        } else {
            var w = document.getElementById('bbox-west').value;
            var s = document.getElementById('bbox-south').value;
            var e = document.getElementById('bbox-east').value;
            var n = document.getElementById('bbox-north').value;
            if (w || s || e || n) {
                params.bbox = {
                    west: parseFloat(w) || 0, south: parseFloat(s) || 0,
                    east: parseFloat(e) || 0, north: parseFloat(n) || 0,
                };
            }
        }
        return params;
    }

    function runAvailCheck() {
        var sid = sourceSelect.value;
        var sd = startDateInput.value;
        var ed = endDateInput.value;
        if (!sid || !sd || !ed) return;

        var src = sourcesById[sid];
        if (src && src.dataset_type === 'static') return;

        var body = { source: sid, start_date: sd, end_date: ed };
        var locParams = getLocationParams();
        Object.keys(locParams).forEach(function (k) { body[k] = locParams[k]; });

        fetch(SUBPATH + '/data/api/check/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(body),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success || !data.results) {
                resultsDiv.innerHTML = '<div class="alert alert-error">' + esc(data.error || 'Check failed') + '</div>';
                return;
            }
            renderAvailResults(data.results, sd, ed);
        })
        .catch(function (err) {
            console.error('Avail check error:', err);
            resultsDiv.innerHTML = '<div class="alert alert-error">Failed to check availability.</div>';
        });
    }

    function renderAvailResults(results, sd, ed) {
        // Compute total requested days
        var start = new Date(sd + 'T00:00:00');
        var end = new Date(ed + 'T00:00:00');
        var totalDays = Math.round((end - start) / 86400000) + 1;

        var html = '<table class="data-table"><thead><tr><th>Source</th><th>Date Range</th><th>Coverage</th></tr></thead><tbody>';
        var hasIncomplete = false;

        for (var i = 0; i < results.length; i++) {
            var r = results[i];
            html += '<tr><td class="mono">' + esc(r.source) + '</td>';
            if (r.available) {
                html += '<td>' + esc(r.first_date) + ' to ' + esc(r.last_date) + '</td>';
                if (r.coverage === 'all') {
                    html += '<td><span class="text-green">All ' + r.days_available + ' days</span></td>';
                } else {
                    hasIncomplete = true;
                    var pct = totalDays ? Math.round(r.days_available / totalDays * 100) : 0;
                    html += '<td><span class="text-yellow">' + r.days_available + ' / ' + totalDays + ' days (' + pct + '%)</span></td>';
                }
            } else {
                hasIncomplete = true;
                html += '<td>—</td><td><span class="text-red">None</span></td>';
            }
            html += '</tr>';
        }
        html += '</tbody></table>';
        resultsDiv.innerHTML = html;

        // Update fraction display
        if (results.length && results[0].available) {
            var r0 = results[0];
            var pct0 = totalDays ? Math.round(r0.days_available / totalDays * 100) : 0;
            var colorClass = pct0 >= 100 ? 'text-green' : (pct0 > 0 ? 'text-yellow' : 'text-red');
            availFraction.innerHTML = 'Available: <span class="count ' + colorClass + '">' +
                r0.days_available + ' / ' + totalDays + ' days (' + pct0 + '%)</span>';
        } else {
            availFraction.innerHTML = 'Available: <span class="count text-red">0 / ' + totalDays + ' days (0%)</span>';
        }

        // Show/hide "Check at Source" button
        remoteBtnEl.classList.toggle('is-hidden', !hasIncomplete);
        remoteResultsDiv.innerHTML = '';
    }

    // -----------------------------------------------------------------------
    // "Check at Source" remote probe
    // -----------------------------------------------------------------------
    remoteBtnEl.addEventListener('click', function () {
        var sid = sourceSelect.value;
        var sd = startDateInput.value;
        var ed = endDateInput.value;
        if (!sid) return;

        remoteBtnEl.disabled = true;
        remoteBtnEl.textContent = 'Checking...';
        remoteResultsDiv.innerHTML = '';

        var body = { source: sid, start_date: sd, end_date: ed };
        var locParams = getLocationParams();
        Object.keys(locParams).forEach(function (k) { body[k] = locParams[k]; });

        fetch(SUBPATH + '/data/api/check-remote/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(body),
        })
        .then(function (r) { return r.json(); })
        .then(function (rd) {
            if (rd.success && rd.result && rd.result.checks) {
                var html = '<table class="data-table compact" style="margin-top:0.75rem;">' +
                    '<thead><tr><th>Source</th><th>Format</th><th>Reachable</th><th>Dates at Source</th><th>Note</th></tr></thead><tbody>';
                rd.result.checks.forEach(function (c) {
                    var icon = c.reachable ? '<span class="text-green">Yes</span>' : '<span class="text-red">No</span>';
                    var datesCell;
                    if (c.days_available != null && c.days_available > 0) {
                        var dr = c.days_requested;
                        if (dr && c.days_available >= dr) {
                            datesCell = '<span class="text-green">All ' + c.days_available + ' days</span>';
                        } else {
                            var pct = dr ? ' (' + Math.round(c.days_available / dr * 100) + '%)' : '';
                            datesCell = '<span class="text-yellow">' + c.days_available + (dr ? ' / ' + dr : '') + ' days' + pct + '</span>';
                        }
                        if (c.first_date && c.last_date) {
                            datesCell += '<br><span style="font-size:0.75rem;color:var(--text-muted)">' + esc(c.first_date) + ' — ' + esc(c.last_date) + '</span>';
                        }
                    } else {
                        datesCell = c.reachable ? '<span class="text-muted">—</span>' : '<span class="text-red">—</span>';
                    }
                    html += '<tr><td class="mono">' + esc(c.source_name) + '</td><td>' + esc(c.format) +
                        '</td><td>' + icon + '</td><td>' + datesCell + '</td><td>' + esc(c.note || '') + '</td></tr>';
                });
                html += '</tbody></table>';
                remoteResultsDiv.innerHTML = html;
            } else {
                remoteResultsDiv.innerHTML = '<div class="alert alert-error" style="margin-top:0.75rem;">' + esc(rd.error || 'Remote check failed') + '</div>';
            }
        })
        .catch(function () {
            remoteResultsDiv.innerHTML = '<div class="alert alert-error" style="margin-top:0.75rem;">Failed to check remote source.</div>';
        })
        .finally(function () {
            remoteBtnEl.disabled = false;
            remoteBtnEl.textContent = 'Check at Source';
        });
    });

    // -----------------------------------------------------------------------
    // Init
    // -----------------------------------------------------------------------
    loadSources();
    // Make body scrollable for explorer pages
    document.body.classList.add('explorer-page');
    // Invalidate map size after layout settles
    setTimeout(function () { miniMap.invalidateSize(); }, 200);

})();
