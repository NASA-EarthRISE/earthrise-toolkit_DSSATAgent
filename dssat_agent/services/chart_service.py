"""
Chart generation for DSSAT simulation results.

Produces Chart.js-compatible configurations from SimulationResult data.
Phenology markers (flowering, maturity) are added as Chart.js annotation
plugin vertical line configs where applicable.
"""


def _get_val(record, *keys):
    """Return the first non-None value from record for any of the given keys."""
    for k in keys:
        v = record.get(k)
        if v is not None:
            return v
    return None


def _phenology_annotations(phenology):
    """Build Chart.js annotation plugin configs for flowering/maturity vertical lines.

    Uses xMin/xMax with the DAP value. Charts that use these annotations
    MUST set their x-axis to type: 'linear' (not category) for the values
    to position correctly.
    """
    annotations = {}
    if not phenology:
        return annotations
    flo = phenology.get('flowering_dap')
    mat = phenology.get('maturity_dap')
    if flo is not None and float(flo) > 0:
        annotations['flowering'] = {
            'type': 'line',
            'scaleID': 'x',
            'value': int(flo),
            'borderColor': '#FF5722',
            'borderWidth': 2,
            'borderDash': [6, 3],
            'label': {
                'display': True,
                'content': f'Flowering ({int(flo)} DAP)',
                'position': 'start',
                'backgroundColor': 'rgba(255, 87, 34, 0.8)',
                'color': '#fff',
                'font': {'size': 10},
            },
        }
    if mat is not None and float(mat) > 0:
        annotations['maturity'] = {
            'type': 'line',
            'scaleID': 'x',
            'value': int(mat),
            'borderColor': '#795548',
            'borderWidth': 2,
            'borderDash': [6, 3],
            'label': {
                'display': True,
                'content': f'Maturity ({int(mat)} DAP)',
                'position': 'start',
                'backgroundColor': 'rgba(121, 85, 72, 0.8)',
                'color': '#fff',
                'font': {'size': 10},
            },
        }
    return annotations


def _linear_x_axis_options():
    """Return options that force the x-axis to linear type (needed for annotations)."""
    return {
        'scales': {
            'x': {
                'type': 'linear',
                'title': {'display': True, 'text': 'Days After Planting'},
            },
        },
    }


def format_agricultural_charts(results, phenology=None):
    """
    Generate Chart.js chart configs from DSSAT simulation results.

    Args:
        results: dict with 'summary', 'plant_growth', 'soil_water', etc.
        phenology: optional dict with 'flowering_dap' and 'maturity_dap'.

    Returns:
        list of chart config dicts with 'type', 'title', 'data'.
    """
    charts = []

    summary = results.get('summary', {})
    plant_growth = results.get('plant_growth', [])

    # Handle nested results
    nested = results.get('results', {})
    if isinstance(nested, dict):
        if not summary:
            summary = nested.get('summary', {})
        if not plant_growth:
            plant_growth = nested.get('plant_growth', [])

    # 1. Yield & Biomass Summary Bar Chart
    if summary:
        yield_chart = _build_yield_summary_chart(summary)
        if yield_chart:
            charts.append(yield_chart)

    # 2. Plant Growth Over Time with phenology markers
    if plant_growth and len(plant_growth) > 1:
        growth_chart = _build_growth_chart(plant_growth, phenology=phenology)
        if growth_chart:
            charts.append(growth_chart)

    return charts


def build_rainfall_chart(weather_output, phenology=None):
    """Build a daily rainfall bar chart with cumulative line from weather output data."""
    rain_points = []
    cumulative_points = []
    cumulative = 0.0

    for record in weather_output:
        dap = _get_val(record, 'DAP', 'dap', 'DAS', 'das')
        rain = _get_val(record, 'PRED', 'pred', 'RAIN', 'rain', 'RAIND', 'raind')
        if dap is not None and rain is not None:
            d = int(dap)
            r = float(rain)
            rain_points.append({'x': d, 'y': r})
            cumulative += r
            cumulative_points.append({'x': d, 'y': round(cumulative, 1)})

    if not rain_points:
        return None

    # {x,y} data + linear x-axis + dual y-axes
    opts = _linear_x_axis_options()
    opts['scales']['y'] = {
        'type': 'linear',
        'position': 'left',
        'title': {'display': True, 'text': 'Daily Rainfall (mm)'},
    }
    opts['scales']['y1'] = {
        'type': 'linear',
        'position': 'right',
        'title': {'display': True, 'text': 'Cumulative (mm)'},
        'grid': {'drawOnChartArea': False},
    }
    annotations = _phenology_annotations(phenology)
    if annotations:
        opts['plugins'] = {'annotation': {'annotations': annotations}}

    return {
        'type': 'bar',
        'title': 'Daily & Cumulative Rainfall',
        'data': {
            'datasets': [
                {
                    'type': 'bar',
                    'label': 'Daily Rainfall (mm)',
                    'data': rain_points,
                    'backgroundColor': 'rgba(21, 101, 192, 0.6)',
                    'borderColor': '#1565C0',
                    'borderWidth': 1,
                    'yAxisID': 'y',
                },
                {
                    'type': 'line',
                    'label': 'Cumulative (mm)',
                    'data': cumulative_points,
                    'borderColor': '#0D47A1',
                    'fill': False,
                    'tension': 0.3,
                    'pointRadius': 0,
                    'borderWidth': 2,
                    'yAxisID': 'y1',
                },
            ],
        },
        'options': opts,
    }


def build_stress_charts(plant_growth, significant_stress, phenology=None):
    """
    Build time-series line charts for significant stress factors.

    Args:
        plant_growth: list of daily PlantGro records.
        significant_stress: dict of stress factors that exceeded thresholds.
        phenology: optional dict with flowering/maturity DAP.

    Returns:
        list of chart configs.
    """
    charts = []

    _STRESS_CONFIG = {
        'water_photo': {
            'column': ('WSPD', 'wspd'),
            'label': 'Water Stress (Photosynthesis)',
            'color': '#E53935',
        },
        'water_expansion': {
            'column': ('WSGD', 'wsgd'),
            'label': 'Water Stress (Leaf Expansion)',
            'color': '#FF7043',
        },
        'nitrogen': {
            'column': ('NSTD', 'nstd'),
            'label': 'Nitrogen Stress',
            'color': '#FFA726',
        },
        'phosphorus_photo': {
            'column': ('PST1A', 'pst1a'),
            'label': 'Phosphorus Stress (Photosynthesis)',
            'color': '#AB47BC',
        },
        'phosphorus_growth': {
            'column': ('PST2A', 'pst2a'),
            'label': 'Phosphorus Stress (Growth)',
            'color': '#CE93D8',
        },
        'potassium': {
            'column': ('KSTD', 'kstd'),
            'label': 'Potassium Stress',
            'color': '#78909C',
        },
    }

    # Group water stress into one chart, others individually
    water_factors = {k: v for k, v in significant_stress.items() if k.startswith('water_')}
    other_factors = {k: v for k, v in significant_stress.items() if not k.startswith('water_')}

    # Stress axis options
    stress_y = {'y': {'min': 0, 'max': 1, 'title': {'display': True, 'text': 'Stress (0=none, 1=max)'}}}

    # Combined water stress chart
    if water_factors:
        datasets = []
        for factor in water_factors:
            cfg = _STRESS_CONFIG.get(factor)
            if not cfg:
                continue
            points = _extract_daily(plant_growth, cfg['column'])
            if points:
                datasets.append({
                    'label': cfg['label'],
                    'data': points,
                    'borderColor': cfg['color'],
                    'backgroundColor': cfg['color'] + '1A',
                    'fill': False,
                    'tension': 0.3,
                    'pointRadius': 0,
                })

        if datasets:
            opts = _linear_x_axis_options()
            opts['scales'].update(stress_y)
            annotations = _phenology_annotations(phenology)
            if annotations:
                opts['plugins'] = {'annotation': {'annotations': annotations}}
            charts.append({
                'type': 'line',
                'title': 'Water Stress Over Time',
                'data': {'datasets': datasets},
                'options': opts,
            })

    # Individual charts for non-water stress
    for factor, data in other_factors.items():
        cfg = _STRESS_CONFIG.get(factor)
        if not cfg:
            continue
        points = _extract_daily(plant_growth, cfg['column'])
        if not points:
            continue

        opts = _linear_x_axis_options()
        opts['scales'].update(stress_y)
        annotations = _phenology_annotations(phenology)
        if annotations:
            opts['plugins'] = {'annotation': {'annotations': annotations}}
        charts.append({
            'type': 'line',
            'title': f'{cfg["label"]} Over Time',
            'data': {
                'datasets': [{
                    'label': cfg['label'],
                    'data': points,
                    'borderColor': cfg['color'],
                    'backgroundColor': cfg['color'] + '1A',
                    'fill': True,
                    'tension': 0.3,
                    'pointRadius': 0,
                }],
            },
            'options': opts,
        })

    return charts


def _build_lai_chart(plant_growth):
    """Line chart of leaf area index over time (on-demand).

    `_extract_daily` returns a list of {x: dap, y: val} points. The old
    (daps, vals) tuple unpacking was a stale API call that raised
    "too many values to unpack (expected 2)".
    """
    points = _extract_daily(plant_growth, ('LAID', 'laid'))
    if not points or not any(p.get('y', 0) > 0 for p in points):
        return None
    daps = [p['x'] for p in points]
    vals = [p['y'] for p in points]
    return {
        'type': 'line',
        'title': 'Leaf Area Index (LAI)',
        'data': {
            'labels': daps,
            'datasets': [{
                'label': 'LAI',
                'data': vals,
                'borderColor': '#2E7D32',
                'backgroundColor': 'rgba(46, 125, 50, 0.1)',
                'fill': True,
                'tension': 0.3,
            }],
        },
    }


def _build_water_chart(soil_water):
    """Line chart of soil water content over time (on-demand)."""
    daps = []
    sw_vals = []
    for record in soil_water:
        dap = _get_val(record, 'DAP', 'dap')
        sw = _get_val(record, 'SWTD', 'swtd', 'SW1D', 'sw1d')
        if dap is not None and sw is not None:
            daps.append(int(dap))
            sw_vals.append(float(sw))
    if not daps or not any(v > 0 for v in sw_vals):
        return None
    return {
        'type': 'line',
        'title': 'Soil Water Content',
        'data': {
            'labels': daps,
            'datasets': [{
                'label': 'Soil Water (mm)',
                'data': sw_vals,
                'borderColor': '#1565C0',
                'backgroundColor': 'rgba(21, 101, 192, 0.1)',
                'fill': True,
                'tension': 0.3,
            }],
        },
    }


def _extract_daily(plant_growth, column_keys):
    """Extract DAP and values as {x, y} points from PlantGro records."""
    points = []
    for record in plant_growth:
        dap = _get_val(record, 'DAP', 'dap')
        val = _get_val(record, *column_keys)
        if dap is not None and val is not None:
            points.append({'x': int(dap), 'y': float(val)})
    return points


# ---------------------------------------------------------------------------
# Core chart builders
# ---------------------------------------------------------------------------

def _build_yield_summary_chart(summary):
    """Bar chart comparing yield, biomass, and other key metrics."""
    labels = []
    values = []
    colors = []

    field_map = [
        ('harwt', 'Harvested Yield', '#4CAF50'),
        ('hwam', 'Yield at Maturity', '#4CAF50'),
        ('topwt', 'Above-ground Biomass', '#8BC34A'),
        ('cwam', 'Above-ground Biomass', '#8BC34A'),
    ]

    seen = set()
    for key, label, color in field_map:
        val = summary.get(key)
        if val is not None and label not in seen:
            labels.append(label)
            values.append(float(val))
            colors.append(color)
            seen.add(label)

    if not values:
        return None

    return {
        'type': 'bar',
        'title': 'Yield & Biomass (kg/ha, dry weight)',
        'data': {
            'labels': labels,
            'datasets': [{
                'label': 'kg/ha (dry weight)',
                'data': values,
                'backgroundColor': colors,
                'borderWidth': 1,
            }],
        },
    }


def _build_growth_chart(plant_growth, phenology=None):
    """Line chart of grain weight and total biomass over time with phenology markers."""
    biomass_points = []
    grain_points = []

    for record in plant_growth:
        dap = _get_val(record, 'DAP', 'dap')
        gwad = _get_val(record, 'GWAD', 'gwad')
        cwad = _get_val(record, 'CWAD', 'cwad')

        if dap is not None:
            d = int(dap)
            biomass_points.append({'x': d, 'y': float(cwad) if cwad is not None else 0})
            grain_points.append({'x': d, 'y': float(gwad) if gwad is not None else 0})

    if not biomass_points:
        return None

    datasets = []
    if any(p['y'] > 0 for p in biomass_points):
        datasets.append({
            'label': 'Total Biomass (kg/ha)',
            'data': biomass_points,
            'borderColor': '#8BC34A',
            'backgroundColor': 'rgba(139, 195, 74, 0.1)',
            'fill': True,
            'tension': 0.3,
        })
    if any(p['y'] > 0 for p in grain_points):
        datasets.append({
            'label': 'Grain Weight (kg/ha)',
            'data': grain_points,
            'borderColor': '#FF9800',
            'backgroundColor': 'rgba(255, 152, 0, 0.1)',
            'fill': True,
            'tension': 0.3,
        })

    if not datasets:
        return None

    opts = _linear_x_axis_options()
    annotations = _phenology_annotations(phenology)
    if annotations:
        opts['plugins'] = {'annotation': {'annotations': annotations}}

    return {
        'type': 'line',
        'title': 'Crop Growth Over Time',
        'data': {'datasets': datasets},
        'options': opts,
    }


def build_comparison_chart(labeled_results):
    """
    Build charts comparing multiple simulation results side by side.

    Args:
        labeled_results: list of (label, result_dict) tuples

    Returns:
        list of chart configs
    """
    if len(labeled_results) < 2:
        return []

    charts = []

    labels = []
    yields = []
    for label, result in labeled_results:
        summary = result.get('summary', {})
        if not summary:
            nested = result.get('results', {})
            if isinstance(nested, dict):
                summary = nested.get('summary', {})
        yield_val = summary.get('harwt') or summary.get('hwam')
        if yield_val is not None:
            labels.append(label)
            yields.append(float(yield_val))

    if len(labels) >= 2:
        charts.append({
            'type': 'bar',
            'title': 'Yield Comparison (kg/ha, dry weight)',
            'data': {
                'labels': labels,
                'datasets': [{
                    'label': 'Yield (kg/ha)',
                    'data': yields,
                    'backgroundColor': [
                        '#4CAF50', '#2196F3', '#FF9800', '#9C27B0',
                        '#F44336', '#00BCD4', '#795548', '#607D8B',
                    ][:len(labels)],
                    'borderWidth': 1,
                }],
            },
        })

    return charts


# ---------------------------------------------------------------------------
# Ensemble chart builders
# ---------------------------------------------------------------------------

_ENSEMBLE_COLORS = [
    '#4CAF50', '#2196F3', '#FF9800', '#9C27B0',
    '#F44336', '#00BCD4', '#795548', '#607D8B',
    '#E91E63', '#009688', '#CDDC39', '#3F51B5',
]


def build_ensemble_yield_chart(treatments):
    """Bar chart comparing yield across ensemble treatments.

    All bars use the same color — the x-axis tick labels already
    identify each treatment, so per-bar colors add visual noise without
    disambiguating anything.

    Args:
        treatments: list of treatment result dicts, each with 'summary' and
                    optionally 'label'.

    Returns:
        chart config dict or None.
    """
    labels = []
    yields = []
    for i, t in enumerate(treatments):
        summary = t.get('summary', {})
        yield_val = summary.get('harwt') or summary.get('hwam')
        if yield_val is not None:
            label = t.get('label', f'Treatment {i + 1}')
            labels.append(label)
            yields.append(float(yield_val))

    if len(labels) < 2:
        return None

    return {
        'type': 'bar',
        'title': 'Yield Comparison Across Treatments',
        'data': {
            'labels': labels,
            'datasets': [{
                'label': 'Yield (kg/ha)',
                'data': yields,
                'backgroundColor': 'rgba(46, 125, 50, 0.7)',
                'borderColor': 'rgba(46, 125, 50, 1.0)',
                'borderWidth': 1,
            }],
        },
    }


def build_ensemble_growth_overlay(treatments):
    """Overlaid line chart of crop growth for each treatment.

    Surfaces the **min**, **max**, and **mean** treatments by yield as
    visible lines; the rest are emitted hidden by default so power users
    can toggle them on via the chart legend without overwhelming the
    default view. The mean is computed as a per-DAP average across all
    treatments that have growth data.

    Args:
        treatments: list of treatment result dicts with 'plant_growth' and
                    optionally 'label'.

    Returns:
        chart config dict or None.
    """
    # Identify the min/max-yielding treatments so they're highlighted.
    yields_by_idx = {}
    for i, t in enumerate(treatments):
        s = t.get('summary') or {}
        y = s.get('harwt') or s.get('hwam')
        if y is not None:
            yields_by_idx[i] = float(y)
    min_idx = min(yields_by_idx, key=yields_by_idx.get) if yields_by_idx else None
    max_idx = max(yields_by_idx, key=yields_by_idx.get) if yields_by_idx else None

    # Per-treatment biomass series.
    series = []  # list of (idx, label, points-list)
    for i, t in enumerate(treatments):
        pg = t.get('plant_growth', [])
        if not pg:
            continue
        label = t.get('label', f'Treatment {i + 1}')
        points = []
        for record in pg:
            dap = _get_val(record, 'DAP', 'dap')
            cwad = _get_val(record, 'CWAD', 'cwad')
            if dap is not None and cwad is not None:
                points.append({'x': int(dap), 'y': float(cwad)})
        if points:
            series.append((i, label, points))

    if len(series) < 2:
        return None

    # Compute the per-DAP mean across treatments. Aligns by integer DAP.
    dap_values = {}
    for _, _, points in series:
        for p in points:
            dap_values.setdefault(p['x'], []).append(p['y'])
    mean_points = [
        {'x': dap, 'y': round(sum(vals) / len(vals), 2)}
        for dap, vals in sorted(dap_values.items())
    ]

    datasets = []
    for idx, label, points in series:
        is_min = (idx == min_idx)
        is_max = (idx == max_idx)
        if is_min:
            color = '#d32f2f'  # red — worst
            display_label = f'{label} — min'
        elif is_max:
            color = '#388e3c'  # green — best
            display_label = f'{label} — max'
        else:
            color = '#90a4ae'  # neutral grey
            display_label = label
        datasets.append({
            'label': display_label,
            'data': points,
            'borderColor': color,
            'fill': False,
            'tension': 0.3,
            'pointRadius': 0,
            'borderWidth': 2 if (is_min or is_max) else 1,
            # Chart.js convention: ``hidden: true`` means the dataset is
            # registered but the line is invisible until the user clicks
            # its legend entry. The min/max stay visible by default.
            'hidden': not (is_min or is_max),
        })

    # Mean line — overlay on top, dashed, blue.
    if mean_points:
        datasets.append({
            'label': 'Mean — biomass',
            'data': mean_points,
            'borderColor': '#1565c0',
            'borderDash': [6, 4],
            'fill': False,
            'tension': 0.3,
            'pointRadius': 0,
            'borderWidth': 2,
        })

    opts = _linear_x_axis_options()
    opts['scales']['y'] = {
        'title': {'display': True, 'text': 'Above-ground Biomass (kg/ha)'},
    }

    return {
        'type': 'line',
        'title': 'Crop Growth Comparison (min / max / mean visible by default)',
        'data': {'datasets': datasets},
        'options': opts,
    }


def build_ensemble_maturity_chart(treatments):
    """Bar chart comparing days to maturity across treatments.

    Reads the DAP-form ``mat`` field that ``_clean_summary`` derives
    from ``mdat`` - ``pdat``. The previous fallback to raw ``mdat``
    surfaced YYYYDDD date integers (~2,024,202) as if they were day
    counts; we no longer fall back since the cleaned summary always
    has the proper DAP value when both dates are present.

    Args:
        treatments: list of treatment result dicts with 'summary'.

    Returns:
        chart config dict or None.
    """
    labels = []
    maturities = []
    for i, t in enumerate(treatments):
        summary = t.get('summary', {})
        mat = summary.get('mat')
        if mat is not None:
            label = t.get('label', f'Treatment {i + 1}')
            labels.append(label)
            maturities.append(float(mat))

    if len(labels) < 2:
        return None

    return {
        'type': 'bar',
        'title': 'Days to Maturity Comparison',
        'data': {
            'labels': labels,
            'datasets': [{
                'label': 'Days to Maturity',
                'data': maturities,
                'backgroundColor': 'rgba(121, 85, 72, 0.7)',
                'borderColor': 'rgba(121, 85, 72, 1.0)',
                'borderWidth': 1,
            }],
        },
    }


# ---------------------------------------------------------------------------
# Monte Carlo chart builders
# ---------------------------------------------------------------------------

def build_yield_histogram(treatments, n_bins=15):
    """Histogram of yield distribution from Monte Carlo treatments.

    Args:
        treatments: list of treatment result dicts with 'summary'.
        n_bins: number of histogram bins.

    Returns:
        chart config dict or None.
    """
    yields = []
    for t in treatments:
        summary = t.get('summary', {})
        val = summary.get('harwt') or summary.get('hwam')
        if val is not None:
            yields.append(float(val))

    if len(yields) < 3:
        return None

    min_y = min(yields)
    max_y = max(yields)
    if max_y == min_y:
        return None

    bin_width = (max_y - min_y) / n_bins
    bins = [0] * n_bins
    bin_labels = []
    for i in range(n_bins):
        lo = min_y + i * bin_width
        hi = lo + bin_width
        bin_labels.append(f'{lo:.0f}')
        for y in yields:
            if lo <= y < hi or (i == n_bins - 1 and y == max_y):
                bins[i] += 1

    return {
        'type': 'bar',
        'title': 'Yield Distribution (Monte Carlo)',
        'data': {
            'labels': bin_labels,
            'datasets': [{
                'label': 'Frequency',
                'data': bins,
                'backgroundColor': 'rgba(76, 175, 80, 0.7)',
                'borderColor': '#4CAF50',
                'borderWidth': 1,
            }],
        },
        'options': {
            'scales': {
                'x': {'title': {'display': True, 'text': 'Yield (kg/ha)'}},
                'y': {'title': {'display': True, 'text': 'Number of Samples'}},
            },
        },
    }


def build_yield_boxplot(quantiles):
    """Box-plot-style chart from pre-computed quantile statistics.

    Uses floating bars to represent the IQR and whiskers.

    Args:
        quantiles: dict with keys like 'p5', 'p25', 'p50', 'p75', 'p95'.

    Returns:
        chart config dict or None.
    """
    p25 = quantiles.get('p25')
    p75 = quantiles.get('p75')
    p50 = quantiles.get('p50')
    p5 = quantiles.get('p5')
    p95 = quantiles.get('p95')

    if None in (p25, p75, p50, p5, p95):
        return None

    # Chart.js floating bars take ``[low, high]`` per data point \u2014 the
    # earlier ``[{'y': [low, high]}]`` shape silently rendered as a single
    # zero-height bar at the median, which is why everything but the
    # median dot was invisible. The ``order`` keys force the wider P5\u2013P95
    # bar to render behind the narrower IQR bar.
    return {
        'type': 'bar',
        'title': 'Yield Range (Monte Carlo Quantiles)',
        'data': {
            'labels': ['Yield (kg/ha)'],
            'datasets': [
                {
                    'label': f'P5\u2013P95 ({p5}\u2013{p95})',
                    'data': [[float(p5), float(p95)]],
                    'backgroundColor': 'rgba(33, 150, 243, 0.20)',
                    'borderColor': '#2196F3',
                    'borderWidth': 1,
                    'barPercentage': 0.5,
                    'categoryPercentage': 0.8,
                    'order': 3,
                },
                {
                    'label': f'P25\u2013P75 IQR ({p25}\u2013{p75})',
                    'data': [[float(p25), float(p75)]],
                    'backgroundColor': 'rgba(33, 150, 243, 0.55)',
                    'borderColor': '#1565C0',
                    'borderWidth': 2,
                    'barPercentage': 0.3,
                    'categoryPercentage': 0.8,
                    'order': 2,
                },
                {
                    'type': 'scatter',
                    'label': f'Median P50 ({p50} kg/ha)',
                    'data': [{'x': 'Yield (kg/ha)', 'y': float(p50)}],
                    'backgroundColor': '#0D47A1',
                    'borderColor': '#0D47A1',
                    'pointRadius': 8,
                    'pointStyle': 'rectRot',
                    'order': 1,
                },
            ],
        },
        'options': {
            'indexAxis': 'x',
            'scales': {
                'y': {'title': {'display': True, 'text': 'Yield (kg/ha)'}, 'beginAtZero': False},
            },
            'plugins': {
                'tooltip': {'mode': 'index', 'intersect': False},
            },
        },
    }


def build_spatial_yield_scatter(treatments):
    """Scatter plot of yield by lat/lon from Monte Carlo treatments.

    Args:
        treatments: list of dicts with 'lat', 'lon', and 'summary'.

    Returns:
        chart config dict or None.
    """
    points = []
    yields = []
    for t in treatments:
        lat = t.get('lat')
        lon = t.get('lon')
        summary = t.get('summary', {})
        val = summary.get('harwt') or summary.get('hwam')
        if lat is not None and lon is not None and val is not None:
            points.append({'x': float(lon), 'y': float(lat)})
            yields.append(float(val))

    if len(points) < 2:
        return None

    # Color by yield: green (high) to red (low)
    min_y = min(yields)
    max_y = max(yields)
    range_y = max_y - min_y if max_y != min_y else 1.0

    colors = []
    for y in yields:
        ratio = (y - min_y) / range_y
        r = int(244 * (1 - ratio) + 76 * ratio)
        g = int(67 * (1 - ratio) + 175 * ratio)
        b = int(54 * (1 - ratio) + 80 * ratio)
        colors.append(f'rgba({r}, {g}, {b}, 0.8)')

    return {
        'type': 'scatter',
        'title': 'Spatial Yield Distribution',
        'data': {
            'datasets': [{
                'label': 'Yield by Location',
                'data': points,
                'backgroundColor': colors,
                'pointRadius': 8,
            }],
        },
        'options': {
            'scales': {
                'x': {
                    'type': 'linear',
                    'title': {'display': True, 'text': 'Longitude'},
                },
                'y': {
                    'type': 'linear',
                    'title': {'display': True, 'text': 'Latitude'},
                },
            },
        },
        '_yield_values': yields,
    }
