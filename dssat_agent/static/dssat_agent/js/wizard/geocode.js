/**
 * Reverse geocoding via Photon (Komoot's free public OSM service).
 *
 *   https://photon.komoot.io/reverse?lat=…&lon=…&lang=en&limit=1
 *
 * Photon is OSM-backed (same data as Nominatim), but with a more lenient
 * usage policy and CORS enabled, so we can call it directly from the
 * browser without a Django proxy or any custom request headers.
 *
 * Returned shape (Photon GeoJSON):
 *   {
 *     "features": [
 *       {
 *         "geometry": {...},
 *         "properties": {
 *           "country": "United States",
 *           "state":   "Alabama",
 *           "county":  "Autauga County",
 *           "city" / "town" / "village" / "hamlet": "Prattville",
 *           "name":    "...",
 *           ...
 *         }
 *       }
 *     ]
 *   }
 *
 * We collapse that to a one-line label suitable for the wizard's
 * `location_label` field — `"Prattville, AL"` for US points,
 * `"<place>, <region/country>"` everywhere else.
 *
 * In-memory cache keyed at ~110 m resolution (4 decimal places) so a
 * single session doesn't hit Photon multiple times for the same point.
 */

const ENDPOINT = 'https://photon.komoot.io/reverse';
const _cache = new Map();  // key: "lat.4|lon.4" -> Promise<string|null>

/**
 * Return a short human-readable label for the given coordinates, or null
 * if the lookup fails or returns no usable place. Always resolves — never
 * throws — so callers can use it as a non-critical enrichment step.
 */
export function reverseGeocode(lat, lon) {
    if (lat == null || lon == null || isNaN(lat) || isNaN(lon)) {
        return Promise.resolve(null);
    }
    const key = `${(+lat).toFixed(4)}|${(+lon).toFixed(4)}`;
    if (_cache.has(key)) return _cache.get(key);
    const promise = _doFetch(lat, lon).catch(() => null);
    _cache.set(key, promise);
    return promise;
}

async function _doFetch(lat, lon) {
    const url = `${ENDPOINT}?lat=${lat}&lon=${lon}&lang=en&limit=1`;
    const resp = await fetch(url);
    if (!resp.ok) return null;
    const data = await resp.json();
    const features = (data && data.features) || [];
    if (!features.length) return null;
    const props = features[0].properties || {};
    return _formatLabel(props);
}

function _formatLabel(p) {
    const place =
        p.city || p.town || p.village || p.hamlet
        || p.locality || p.suburb || p.county
        || p.name;
    const region = _regionShort(p);
    if (place && region) return `${place}, ${region}`;
    if (place) return place;
    if (region) return region;
    if (p.country) return p.country;
    return null;
}

/**
 * Best-effort short region code: 2-letter US state code when applicable;
 * otherwise the state name; otherwise the country.
 */
function _regionShort(p) {
    if (p.country === 'United States' && p.state) {
        return _US_STATE_CODES[p.state] || p.state;
    }
    return p.state || p.country || null;
}

const _US_STATE_CODES = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
    'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT',
    'Delaware': 'DE', 'District of Columbia': 'DC',
    'Florida': 'FL', 'Georgia': 'GA',
    'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN',
    'Iowa': 'IA', 'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA',
    'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA',
    'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
    'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM',
    'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND',
    'Ohio': 'OH', 'Oklahoma': 'OK', 'Oregon': 'OR', 'Pennsylvania': 'PA',
    'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
    'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
    'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
    'Wisconsin': 'WI', 'Wyoming': 'WY',
    'Puerto Rico': 'PR', 'Guam': 'GU', 'American Samoa': 'AS',
    'U.S. Virgin Islands': 'VI', 'Northern Mariana Islands': 'MP',
};
