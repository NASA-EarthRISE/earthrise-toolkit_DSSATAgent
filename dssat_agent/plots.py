def format_agricultural_charts(data):
    """
    Format agricultural simulation data for Chart.js consumption.

    Args:
        data (dict): Input data containing yield_results, water_stress, nitro_stress,
                    and optionally yield_anomalies

    Returns:
        list: List of chart configurations ready for Chart.js
    """

    # Constants from the original code
    SERIES_CI = [95, 75, 50, 25]
    COLORS = ["#66ff66", "#33cc33", "#009933", "#006600"]

    DEV_STAGES_LABELS = [
        "Emerg.-End Juv.",
        "End Juv-Flor Init",
        "Flor Init-End Lf Gro",
        "End lf Gro-Beg Grain Fil",
        "Grain Fill"
    ]

    CAT_NAMES = ["Very low", "Low", "Normal", "High", "Very high"]
    CAT_COLORS = ['#cc0000', "#ff9933", "#ffff66", "#99cc00", "#009933"]

    charts = []

    # 1. Yield Range Probability Chart (Stacked Bar) - if yield data is provided
    if 'yield_results' in data:
        yield_data = data['yield_results']
        segments = convert_yield_to_range_segments(yield_data)
        experiment_name = next(iter(yield_data.values()))['name'].replace('<br>', ' ')
        experiment_name = 'exp'

        # Each segment uses [range_min, range_max] as y value
        data_values = [{
            'x': experiment_name,
            'y': [seg['range_min'], seg['range_max']],
            'custom': {
                'probability': seg['probability'],
                'range_min': seg['range_min'],
                'range_max': seg['range_max']
            }
        } for seg in segments]

        charts.append({
            'title': 'Experiment',
            'type': 'bar',
            'data': {
                'labels': [experiment_name],  # one label for the x-axis
                'datasets': [{
                    'label': 'Yield Range',
                    'data': [
                        {
                            'x': experiment_name,
                            'y': [seg['range_min'], seg['range_max']],
                            'custom': {
                                'probability': seg['probability'],
                                'range_min': seg['range_min'],
                                'range_max': seg['range_max']
                            }
                        } for seg in segments
                    ],
                    'backgroundColor': [
                                           '#cc0000', '#ff9933', '#ffff66', '#99cc00', '#009933'
                                       ][:len(segments)],
                    'borderColor': [
                                       '#cc0000', '#ff9933', '#ffff66', '#99cc00', '#009933'
                                   ][:len(segments)],
                    'borderWidth': 1
                }]
            },
            'options': {
                'responsive': True,
                'plugins': {
                    'tooltip': {
                        'callbacks': {
                            'label': """
                            function(context) {
                                const meta = context.raw.custom;
                                return meta.range_min.toFixed(2) + ' - ' + meta.range_max.toFixed(2) +
                                       ' t/ha (Probability: ' + meta.probability.toFixed(1) + '%)';
                            }
                            """
                        }
                    },
                    'legend': {'display': False}
                },
                'scales': {
                    'y': {
                        'stacked': True,
                        'title': {'display': True, 'text': 'Yield (t/ha)'}
                    },
                    'x': {
                        'stacked': True,
                        'title': {'display': True, 'text': 'Experiment'}
                    }
                }
            }
        })
        """
                charts.append({
                    'title': 'Experiment',
                    'type': 'bar',
                    'data': {
                        'labels': [experiment_name],  # still required but optional for floating bars
                        'datasets': [{
                            'label': 'Yield Range',
                            'data': data_values,
                            'backgroundColor': [
                                '#cc0000', '#ff9933', '#ffff66', '#99cc00', '#009933'
                            ][:len(segments)],
                            'borderColor': [
                                '#cc0000', '#ff9933', '#ffff66', '#99cc00', '#009933'
                            ][:len(segments)],
                            'borderWidth': 1
                        }]
                    },
                    'options': {
                        'plugins': {
                            'tooltip': {
                                'callbacks': {
                                    'label': function(context) {
                                        const meta = context.raw.custom;
                                        return meta.range_min.toFixed(2) + ' - ' + meta.range_max.toFixed(2) +
                                               ' t/ha (Probability: ' + meta.probability.toFixed(1) + '%)';
                                    }
                                }
                            }
                        },
                        'scales': {
                            'y': { 'stacked': True, 'title': {'display': True, 'text': 'Yield (t/ha)'} },
                            'x': { 'stacked': True, 'title': {'display': True, 'text': 'Experiment'} }
                        }
                    }
                })"""



    # 2. Alternative: Confidence Interval Chart (if needed separately)
    if 'yield_results' in data:
        yield_data = data['yield_results']
        experiment_name = next(iter(yield_data.values()))['name'].replace('<br>', ' ')

        # Create datasets for each confidence interval
        datasets = []
        for i, ci in enumerate(SERIES_CI):
            series_name = f"Simulated yield ({ci}% CI)"
            if series_name in yield_data:
                low_val = yield_data[series_name]['low']
                high_val = yield_data[series_name]['high']

                datasets.append({
                    'label': series_name,
                    'data': [{'x': experiment_name, 'y': [low_val, high_val]}],
                    'backgroundColor': COLORS[i],
                    'borderColor': COLORS[i],
                    'borderWidth': 0
                })

        charts.append({
            'type': 'bar',  # Chart.js doesn't have native column range, we'll use custom bar
            'title': 'DSSAT Simulated Maize Yield',
            'data': {
                'datasets': datasets
            },
            'options': {
                'responsive': True,
                'plugins': {
                    'legend': {'position': 'top'},
                    'tooltip': {
                        'callbacks': {
                            'label': 'function(context) { return context.dataset.label + ": " + context.parsed.y[0] + "-" + context.parsed.y[1] + " t/ha"; }'
                        }
                    }
                },
                'scales': {
                    'y': {
                        'title': {'display': True, 'text': 'Yield (t/ha)'},
                        'beginAtZero': True
                    },
                    'x': {
                        'title': {'display': True, 'text': 'Experiment'}
                    }
                }
            }
        })

    # 3. Water Stress Chart
    if 'water_stress' in data:
        water_stress = data['water_stress']

        # Filter out 'Planting to Harvest' and map to display labels
        stage_mapping = {
            'Emergence-End Juvenile': 'Emerg.-End Juv.',
            'End Juvenil-Floral Init': 'End Juv-Flor Init',
            'Floral Init-End Lf Grow': 'Flor Init-End Lf Gro',
            'End Lf Grth-Beg Grn Fil': 'End lf Gro-Beg Grain Fil',
            'Grain Filling Phase': 'Grain Fill'
        }

        labels = []
        stress_values = []

        for stage, display_label in stage_mapping.items():
            if stage in water_stress:
                labels.append(display_label)
                stress_values.append(water_stress[stage])

        charts.append({
            'type': 'bar',
            'title': 'Water Stress',
            'data': {
                'labels': labels,
                'datasets': [{
                    'label': 'Water Stress (%)',
                    'data': stress_values,
                    'backgroundColor': '#4A90E2',
                    'borderColor': '#4A90E2',
                    'borderWidth': 1
                }]
            },
            'options': {
                'responsive': True,
                'plugins': {
                    'legend': {'display': False},
                    'tooltip': {
                        'callbacks': {
                            'label': 'function(context) { return "Water Stress: " + context.parsed.y.toFixed(0) + "%"; }'
                        }
                    }
                },
                'scales': {
                    'y': {
                        'title': {'display': True, 'text': 'Stress (%)'},
                        'beginAtZero': True
                    },
                    'x': {
                        'title': {'display': True, 'text': 'Crop Dev. Stage'}
                    }
                }
            }
        })

    # 4. Nitrogen Stress Chart
    if 'nitro_stress' in data:
        nitro_stress = data['nitro_stress']

        # Filter out 'Planting to Harvest' and map to display labels
        stage_mapping = {
            'Emergence-End Juvenile': 'Emerg.-End Juv.',
            'End Juvenil-Floral Init': 'End Juv-Flor Init',
            'Floral Init-End Lf Grow': 'Flor Init-End Lf Gro',
            'End Lf Grth-Beg Grn Fil': 'End lf Gro-Beg Grain Fil',
            'Grain Filling Phase': 'Grain Fill'
        }

        labels = []
        stress_values = []

        for stage, display_label in stage_mapping.items():
            if stage in nitro_stress:
                labels.append(display_label)
                stress_values.append(nitro_stress[stage])

        charts.append({
            'type': 'bar',
            'title': 'Nitrogen Stress',
            'data': {
                'labels': labels,
                'datasets': [{
                    'label': 'Nitrogen Stress (%)',
                    'data': stress_values,
                    'backgroundColor': '#E74C3C',
                    'borderColor': '#E74C3C',
                    'borderWidth': 1
                }]
            },
            'options': {
                'responsive': True,
                'plugins': {
                    'legend': {'display': False},
                    'tooltip': {
                        'callbacks': {
                            'label': 'function(context) { return "Nitrogen Stress: " + context.parsed.y.toFixed(0) + "%"; }'
                        }
                    }
                },
                'scales': {
                    'y': {
                        'title': {'display': True, 'text': 'Stress (%)'},
                        'beginAtZero': True
                    },
                    'x': {
                        'title': {'display': True, 'text': 'Crop Dev. Stage'}
                    }
                }
            }
        })

    return charts

"""
def convert_yield_to_range_segments(yield_data, num_segments=5):
    Convert yield confidence interval data to equally-spaced range segments with probabilities.

    Args:
        yield_data (dict): Yield results with confidence intervals
        num_segments (int): Number of equal segments to create (default 5)

    Returns:
        list: List of range segments with probabilities
    import numpy as np

    # Extract all yield values from confidence intervals
    all_values = []
    probabilities = []

    # Map confidence intervals to cumulative probabilities
    ci_to_prob = {95: 0.025, 75: 0.125, 50: 0.25, 25: 0.375}  # Lower bounds
    ci_to_prob_upper = {95: 0.975, 75: 0.875, 50: 0.75, 25: 0.625}  # Upper bounds

    for ci in [95, 75, 50, 25]:
        series_name = f"Simulated yield ({ci}% CI)"
        if series_name in yield_data:
            low = yield_data[series_name]['low']
            high = yield_data[series_name]['high']
            all_values.extend([low, high])
            probabilities.extend([ci_to_prob[ci], ci_to_prob_upper[ci]])

    # Find overall min and max
    min_yield = min(all_values)
    max_yield = max(all_values)

    # Create equal-width segments
    segment_width = (max_yield - min_yield) / num_segments
    segments = []

    for i in range(num_segments):
        range_min = min_yield + i * segment_width
        range_max = min_yield + (i + 1) * segment_width
        range_center = (range_min + range_max) / 2

        # Calculate probability for this range based on how many data points fall within it
        # This is a simplified approach - in reality, you'd want to use the actual distribution
        prob_in_range = 0
        for j, val in enumerate(all_values):
            if range_min <= val <= range_max:
                # Weight by the probability density around this point
                weight = 1.0 / len(all_values)  # Equal weighting for simplification
                prob_in_range += weight

        # Convert to percentage and ensure reasonable values
        probability = max(5.0, prob_in_range * 100)  # Minimum 5% to ensure visibility

        segments.append({
            'range_min': range_min,
            'range_max': range_max,
            'range_height': segment_width,  # Height of this segment in the stacked bar
            'range_label': f"{range_min:.1f}-{range_max:.1f}",
            'probability': probability
        })

    return segments
"""

def build_comparison_chart(labeled_results):
    """
    Build Chart.js configs for comparing multiple simulation results side-by-side.

    Args:
        labeled_results: list of (label, simulation_result_data) tuples

    Returns:
        list: Chart configurations ready for Chart.js
    """
    charts = []

    # Extract yield values from each result
    labels = []
    yield_values = []
    biomass_values = []
    maturity_values = []

    for label, result in labeled_results:
        labels.append(label)

        # Try to extract yield from various result formats
        yield_val = None
        biomass_val = None
        maturity_val = None

        # Monte Carlo results
        quantiles = result.get('quantiles', {})
        if quantiles:
            y_q = quantiles.get('yield_kg_ha', {})
            yield_val = y_q.get('mean') or y_q.get('p50')
            m_q = quantiles.get('maturity_days', {})
            maturity_val = m_q.get('mean') or m_q.get('p50')

        # Standard results
        if yield_val is None:
            yield_val = result.get('yield_kg_ha') or result.get('yield')
            # Check nested
            summary = result.get('summary', {})
            if isinstance(summary, dict):
                yield_val = yield_val or summary.get('yield_kg_ha') or summary.get('yield')
                biomass_val = summary.get('biomass_kg_ha') or summary.get('biomass')
                maturity_val = summary.get('maturity_days') or summary.get('maturity')

        # Yield results format (CI-based)
        yield_results = result.get('yield_results', {})
        if yield_results and yield_val is None:
            ci50 = yield_results.get('Simulated yield (50% CI)', {})
            if ci50:
                yield_val = (ci50.get('low', 0) + ci50.get('high', 0)) / 2

        yield_values.append(yield_val)
        biomass_values.append(biomass_val)
        maturity_values.append(maturity_val)

    # Build yield comparison chart
    if any(v is not None for v in yield_values):
        safe_yields = [v if v is not None else 0 for v in yield_values]

        charts.append({
            'type': 'bar',
            'title': 'Yield Comparison',
            'data': {
                'labels': labels,
                'datasets': [{
                    'label': 'Yield (kg/ha)',
                    'data': safe_yields,
                    'backgroundColor': [
                        '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b',
                        '#ef4444', '#06b6d4', '#ec4899', '#14b8a6',
                    ][:len(labels)],
                    'borderWidth': 1,
                }],
            },
            'options': {
                'responsive': True,
                'plugins': {
                    'legend': {'display': False},
                    'tooltip': {
                        'callbacks': {
                            'label': 'function(context) { return "Yield: " + context.parsed.y.toFixed(0) + " kg/ha"; }'
                        }
                    },
                },
                'scales': {
                    'y': {
                        'title': {'display': True, 'text': 'Yield (kg/ha)'},
                        'beginAtZero': True,
                    },
                    'x': {
                        'title': {'display': True, 'text': 'Scenario'},
                    },
                },
            },
        })

    return charts


def convert_yield_to_range_segments(yield_data, num_segments=5):
    """
    Convert yield confidence interval data to equally-spaced range segments with probabilities.
    Probabilities are estimated from overlapping CIs (does not assume normal distribution).

    Args:
        yield_data (dict): Yield results with confidence intervals
        num_segments (int): Number of equal segments to create (default 5)

    Returns:
        list: List of range segments with probabilities
    """
    import numpy as np

    # Collect all CI ranges
    ci_list = [95, 75, 50, 25]
    ci_ranges = []
    for ci in ci_list:
        series_name = f"Simulated yield ({ci}% CI)"
        if series_name in yield_data:
            low = yield_data[series_name]['low']
            high = yield_data[series_name]['high']
            ci_ranges.append((low, high))

    if not ci_ranges:
        raise ValueError("No CI data found")

    # Determine overall min and max
    min_yield = min(low for low, _ in ci_ranges)
    max_yield = max(high for _, high in ci_ranges)
    segment_width = (max_yield - min_yield) / num_segments

    segments = []
    for i in range(num_segments):
        range_min = min_yield + i * segment_width
        range_max = min_yield + (i + 1) * segment_width

        # Compute probability as the fraction of each CI interval that overlaps this segment
        prob = 0.0
        for ci_low, ci_high in ci_ranges:
            # Overlap between segment and CI
            overlap_low = max(range_min, ci_low)
            overlap_high = min(range_max, ci_high)
            overlap = max(0.0, overlap_high - overlap_low)
            ci_length = ci_high - ci_low
            if ci_length > 0:
                prob += overlap / ci_length  # fractional probability from this CI

        # Average over all CIs and convert to percent
        prob = prob / len(ci_ranges) * 100
        prob = max(prob, 1.0)  # ensure minimum probability for visibility

        segments.append({
            'range_min': range_min,
            'range_max': range_max,
            'range_height': segment_width,
            'range_label': f"{range_min:.2f}-{range_max:.2f}",
            'probability': prob
        })

    return segments


# Example usage:
if __name__ == "__main__":
    # Example data
    example_data = {
        'yield_results': {
            'Simulated yield (95% CI)': {
                'low': 1.4, 'high': 2.6,
                'name': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events',
                'x': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events'
            },
            'Simulated yield (75% CI)': {
                'low': 1.58, 'high': 2.54,
                'name': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events',
                'x': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events'
            },
            'Simulated yield (50% CI)': {
                'low': 1.84, 'high': 2.44,
                'name': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events',
                'x': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events'
            },
            'Simulated yield (25% CI)': {
                'low': 2.0, 'high': 2.39,
                'name': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events',
                'x': 'Maize<br>Planted on Mar 01 2024<br>150 kg N/ha applied in 2 events'
            }
        },
        'water_stress': {
            'Emergence-End Juvenile': 0.65,
            'End Juvenil-Floral Init': 0.13,
            'End Lf Grth-Beg Grn Fil': 0.0,
            'Floral Init-End Lf Grow': 0.0,
            'Grain Filling Phase': 0.0,
            'Planting to Harvest': 0.2
        },
        'nitro_stress': {
            'Emergence-End Juvenile': 0.0,
            'End Juvenil-Floral Init': 0.0,
            'End Lf Grth-Beg Grn Fil': 5.29,
            'Floral Init-End Lf Grow': 0.0,
            'Grain Filling Phase': 34.76,
            'Planting to Harvest': 3.93
        }
    }

    # Example with yield range segments
    formatted_charts = format_agricultural_charts(example_data)

    # Test the range segments conversion
    if 'yield_results' in example_data:
        segments = convert_yield_to_range_segments(example_data['yield_results'])
        print("=== Yield Range Segments ===")
        for i, segment in enumerate(segments):
            print(f"Segment {i + 1}: {segment['range_label']} t/ha, "
                  f"Probability: {segment['probability']:.1f}%, "
                  f"Height: {segment['range_height']:.3f}")

    # Print results for testing
    import json

    print("\n=== Agricultural Charts ===")
    print(json.dumps(formatted_charts, indent=2))
