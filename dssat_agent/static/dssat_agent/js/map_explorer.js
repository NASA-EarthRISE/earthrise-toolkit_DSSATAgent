/**
 * Map Explorer — Leaflet.js map with weather raster tiles and admin boundaries
 */
(function () {
    'use strict';

    // Deployment subpath prefix (e.g. "/subpath" or ""). Prepend to module-absolute URLs.
    var SUBPATH = window.SUBPATH || '';

    // -----------------------------------------------------------------------
    // DOM refs
    // -----------------------------------------------------------------------
    var sourceSelect  = document.getElementById('map-source');
    var varSelect     = document.getElementById('map-variable');
    var dateInput     = document.getElementById('map-date');
    var datePrev      = document.getElementById('date-prev');
    var dateNext      = document.getElementById('date-next');
    var adminSelect   = document.getElementById('map-admin-level');
    var opacitySlider = document.getElementById('map-opacity');
    var opacityLabel  = document.getElementById('opacity-label');

    // -----------------------------------------------------------------------
    // Legend color config per variable
    // -----------------------------------------------------------------------
    var LEGEND_CONFIG = {
        tmax: { label: 'Max Temp (\u00B0C)', gradient: 'linear-gradient(to right, #3264c8, #64c8ff, #ffff64, #ff6400, #b40000)', min: '-10', max: '45' },
        tmin: { label: 'Min Temp (\u00B0C)', gradient: 'linear-gradient(to right, #3264c8, #64c8ff, #ffff64, #ff6400, #b40000)', min: '-20', max: '35' },
        rain: { label: 'Rainfall (mm)',       gradient: 'linear-gradient(to right, rgba(240,250,255,0.3), #b4dcff, #64b4ff, #3264dc, #0000b4)', min: '0', max: '50+' },
        srad: { label: 'Solar Rad (MJ/m\u00B2)', gradient: 'linear-gradient(to right, rgba(255,255,220,0.3), #ffff96, #ffff00, #ff9600, #c80000)', min: '0', max: '35' },
    };

    // -----------------------------------------------------------------------
    // Map init
    // -----------------------------------------------------------------------
    var map = L.map('map-container', {
        center: [33.0, -86.5],
        zoom: 7,
        zoomControl: true,
    });

    // CartoDB Dark Matter basemap
    L.tileLayer(
        'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        {
            attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
            subdomains: 'abcd',
            maxZoom: 19,
        }
    ).addTo(map);

    // -----------------------------------------------------------------------
    // Weather tile layer
    // -----------------------------------------------------------------------
    var weatherLayer = null;

    function buildWeatherLayer() {
        var source   = sourceSelect.value;
        var variable = varSelect.value;
        var date     = dateInput.value;
        var opacity  = parseInt(opacitySlider.value, 10) / 100;

        if (weatherLayer) {
            map.removeLayer(weatherLayer);
        }

        if (!source || !variable || !date) return;

        weatherLayer = L.tileLayer(
            SUBPATH + '/data/api/map/tiles/' + source + '/' + variable + '/' + date + '/{z}/{x}/{y}.png',
            {
                opacity: opacity,
                maxZoom: 19,
                tileSize: 256,
                attribution: 'Weather: DataAgent',
            }
        );
        weatherLayer.addTo(map);
    }

    // -----------------------------------------------------------------------
    // Admin boundary overlay
    // -----------------------------------------------------------------------
    var adminLayer = null;

    var adminStyle = {
        color: '#ffffff',
        weight: 1.2,
        opacity: 0.7,
        fillColor: 'transparent',
        fillOpacity: 0,
    };

    var adminHoverStyle = {
        color: '#0170B9',
        weight: 2.5,
        opacity: 1,
        fillColor: '#0170B9',
        fillOpacity: 0.12,
    };

    function loadAdminBoundaries() {
        var level = adminSelect.value;

        if (adminLayer) {
            map.removeLayer(adminLayer);
            adminLayer = null;
        }

        if (!level) return;

        fetch(SUBPATH + '/data/api/map/admin/?level=' + encodeURIComponent(level))
            .then(function (r) { return r.json(); })
            .then(function (geojson) {
                if (!geojson || !geojson.features) return;

                adminLayer = L.geoJSON(geojson, {
                    style: adminStyle,
                    onEachFeature: function (feature, layer) {
                        var props = feature.properties || {};
                        var name = props.admin2 || props.admin1 || props.country || '';
                        var parts = [];
                        if (props.country) parts.push('<b>' + props.country + '</b>');
                        if (props.admin1) parts.push(props.admin1);
                        if (props.admin2) parts.push(props.admin2);
                        layer.bindPopup(parts.join(' &rsaquo; '));

                        layer.on('mouseover', function () {
                            layer.setStyle(adminHoverStyle);
                            layer.bringToFront();
                        });
                        layer.on('mouseout', function () {
                            if (adminLayer) adminLayer.resetStyle(layer);
                        });
                    },
                });
                adminLayer.addTo(map);
            })
            .catch(function (err) {
                console.error('Admin boundary load error:', err);
            });
    }

    // -----------------------------------------------------------------------
    // Legend control
    // -----------------------------------------------------------------------
    var LegendControl = L.Control.extend({
        options: { position: 'bottomright' },

        onAdd: function () {
            this._div = L.DomUtil.create('div', 'map-legend');
            this.update(varSelect.value);
            return this._div;
        },

        update: function (variable) {
            var cfg = LEGEND_CONFIG[variable] || LEGEND_CONFIG.tmax;
            this._div.innerHTML =
                '<div class="legend-title">' + cfg.label + '</div>' +
                '<div class="legend-bar" style="background:' + cfg.gradient + '"></div>' +
                '<div class="legend-labels"><span>' + cfg.min + '</span><span>' + cfg.max + '</span></div>';
        },
    });

    var legend = new LegendControl();
    legend.addTo(map);

    // -----------------------------------------------------------------------
    // Date navigation
    // -----------------------------------------------------------------------
    function shiftDate(days) {
        var d = new Date(dateInput.value + 'T00:00:00');
        d.setDate(d.getDate() + days);
        var iso = d.toISOString().slice(0, 10);
        dateInput.value = iso;
        buildWeatherLayer();
    }

    datePrev.addEventListener('click', function () { shiftDate(-1); });
    dateNext.addEventListener('click', function () { shiftDate(1); });

    // -----------------------------------------------------------------------
    // Event listeners
    // -----------------------------------------------------------------------
    sourceSelect.addEventListener('change', buildWeatherLayer);
    varSelect.addEventListener('change', function () {
        legend.update(varSelect.value);
        buildWeatherLayer();
    });
    dateInput.addEventListener('change', buildWeatherLayer);
    adminSelect.addEventListener('change', loadAdminBoundaries);

    opacitySlider.addEventListener('input', function () {
        var pct = parseInt(opacitySlider.value, 10);
        opacityLabel.textContent = pct + '%';
        if (weatherLayer) {
            weatherLayer.setOpacity(pct / 100);
        }
    });

    // -----------------------------------------------------------------------
    // Initial load
    // -----------------------------------------------------------------------
    buildWeatherLayer();
    loadAdminBoundaries();

})();
