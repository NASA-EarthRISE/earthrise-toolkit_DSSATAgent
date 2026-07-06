/**
 * Tiny DOM helpers — no framework. Avoids pulling in a library while still
 * keeping component code declarative.
 */

export function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
        for (const [k, v] of Object.entries(attrs)) {
            if (v == null || v === false) continue;
            if (k === 'class') el.className = v;
            else if (k === 'style' && typeof v === 'object') {
                Object.assign(el.style, v);
            } else if (k.startsWith('on') && typeof v === 'function') {
                el.addEventListener(k.slice(2).toLowerCase(), v);
            } else if (k === 'dataset' && typeof v === 'object') {
                for (const [dk, dv] of Object.entries(v)) {
                    el.dataset[dk] = dv;
                }
            } else if (k === 'html') {
                el.innerHTML = v;
            } else if (typeof v === 'boolean') {
                if (v) el.setAttribute(k, '');
            } else {
                el.setAttribute(k, v);
            }
        }
    }
    for (const child of children.flat()) {
        if (child == null || child === false) continue;
        if (child instanceof Node) el.appendChild(child);
        else el.appendChild(document.createTextNode(String(child)));
    }
    return el;
}

export function clear(el) {
    while (el && el.firstChild) el.removeChild(el.firstChild);
}

export function on(el, event, handler) {
    el.addEventListener(event, handler);
    return () => el.removeEventListener(event, handler);
}

export function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
