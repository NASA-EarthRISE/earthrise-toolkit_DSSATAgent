"""
Static-hygiene lint for templates and CSS.

Pure-filesystem checks (no DB, no Django app registry needed) that catch
template/CSS bug classes which render as visible breakage:

  1. Multi-line ``{# ... #}`` Django comments — DTL inline comments are
     single-line only; a multi-line one is NOT parsed as a comment and renders
     as literal text on the page.
  2. ``{% extends %}`` not being the first template tag — Django raises
     TemplateSyntaxError ("must be the first tag"); notably a ``{% comment %}``
     block before ``{% extends %}`` triggers it.
  3. A stylesheet bare-redefining a platform *component* class that a
     co-loaded platform sheet already owns — loaded after it, it shadows the
     platform style (this is what forced a white nav bar on explorer pages).
  4. A non-layout stylesheet redefining ``:root`` design tokens — the palette
     is owned by the platform; only the platform's own sheets and a globally
     loaded product-shell token sheet may define tokens.
  5. A ``{% static %}`` reference to a .css/.js asset that doesn't resolve to a
     real file (typo, wrong path, or deleted asset).

This lint is GENERIC: it never names a specific sub-agent or product app. The
"platform" is whatever app this test lives in (derived from ``__file__``);
"globally loaded" sheets are inferred from layout/base templates. So it keeps
working unchanged if the platform is extracted to its own repository.

Run: ``pytest earthrise_agents_base/tests/test_static_hygiene.py``
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The platform is the app THIS lint lives in — derived from __file__, never
# named. Its own static dir is the source of truth for "platform" stylesheets.
_PLATFORM_APP_DIR = Path(__file__).resolve().parents[1]
_PLATFORM_STATIC = _PLATFORM_APP_DIR / "static"

# Packaging markers that identify a self-contained vendored/installable
# package. A subtree carrying one of these ships its own third-party
# templates/CSS that are not part of this project's first-party static
# system, so the hygiene checks skip it. Detected structurally, never by
# package name — so this lint keeps working unchanged if the platform is
# extracted to its own repository.
_VENDOR_MARKERS = ("setup.py", "setup.cfg", "pyproject.toml", "PKG-INFO")


def _is_vendored(path):
    """True if *path* lives inside a vendored package — a subtree below the
    repo root that carries its own packaging metadata (see _VENDOR_MARKERS).
    The repo root itself is not treated as vendored."""
    for parent in path.parents:
        if parent == REPO_ROOT:
            break
        if any((parent / marker).exists() for marker in _VENDOR_MARKERS):
            return True
    return False


def _templates():
    return [
        p for p in REPO_ROOT.rglob("*.html")
        if "/templates/" in str(p) and not _is_vendored(p)
    ]


def _stylesheets():
    return [
        p for p in REPO_ROOT.rglob("*.css")
        if "/static/" in str(p) and not _is_vendored(p)
    ]


def _static_roots():
    return [
        p for p in REPO_ROOT.rglob("static")
        if p.is_dir() and not _is_vendored(p)
        and "staticfiles" not in str(p)
    ]


def _rel(p):
    return str(p.relative_to(REPO_ROOT))


def _is_platform_sheet(path):
    """A stylesheet owned by the platform = one under the platform app's own
    static dir. Path-based, so no app name is hard-coded."""
    return _PLATFORM_STATIC in path.parents


def _bare_classes(text):
    """Classes defined by a BARE single-class rule: ``.foo {`` (not ``.a.b`` or
    ``.a .b`` or ``.a:hover``). These are the ones that override globally."""
    return set(re.findall(r"(?m)^\.([A-Za-z0-9_-]+)\s*\{", text))


_CSS_REF_RE = re.compile(r"""\{%\s*static\s+['"]([^'"]+\.css)['"]""")


def _css_refs(text):
    return set(_CSS_REF_RE.findall(text))


def _resolve_static(ref, roots):
    for root in roots:
        candidate = root / ref
        if candidate.exists():
            return candidate
    return None


def _shell_layout_css_refs():
    """CSS refs pulled in by the ``shell/`` layout chain — inherited by every
    page that ``{% extends %}`` it. Detected by the ``shell/`` layout-dir
    convention (a ROLE), never by app name. Standalone document pages (their
    own ``<html>``, no ``extends`` — e.g. auth pages) are NOT the shell layout
    and self-list their own sheets, so they are excluded here."""
    refs = set()
    for tpl in _templates():
        if "/shell/" in _rel(tpl):
            refs |= _css_refs(tpl.read_text(encoding="utf-8"))
    return refs


# ---------------------------------------------------------------------------
# 1. No multi-line {# ... #} comments
# ---------------------------------------------------------------------------
def test_no_multiline_dtl_comments():
    offenders = []
    for tpl in _templates():
        for i, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            # A {# that has no closing #} after it on the same line opens a
            # multi-line comment, which DTL does not support.
            idx = line.rfind("{#")
            if idx != -1 and "#}" not in line[idx:]:
                offenders.append(f"{_rel(tpl)}:{i}")
    assert not offenders, (
        "Multi-line `{# ... #}` comments render as visible text — use "
        "`{% comment %}...{% endcomment %}` instead:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. {% extends %} must be the first template tag
# ---------------------------------------------------------------------------
def test_extends_is_first_template_tag():
    offenders = []
    for tpl in _templates():
        text = tpl.read_text(encoding="utf-8")
        if not re.search(r"\{%\s*extends\b", text):
            continue
        m = re.search(r"\{%\s*(\w+)", text)  # first {% %} tag ({# #} ignored)
        if not m or m.group(1) != "extends":
            first = m.group(1) if m else "(none)"
            offenders.append(f"{_rel(tpl)} (first tag: {{% {first} %}})")
    assert not offenders, (
        "`{% extends %}` must be the FIRST template tag (a {% comment %} or "
        "{% load %} before it raises TemplateSyntaxError):\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 3. No sheet bare-shadows a platform component class it is CO-LOADED with
# ---------------------------------------------------------------------------
def test_no_sheet_shadows_platform_component_class():
    """A bare redefinition of a platform component class is only a *live* bug
    when the offending sheet and the platform sheet that owns the class are
    loaded together on the same page. We detect co-load per page (direct
    ``{% static %}`` refs + globally loaded layout sheets), so a mere
    name-collision with a platform sheet that is never co-loaded is not
    flagged — and no app names are needed."""
    roots = _static_roots()
    shell_refs = _shell_layout_css_refs()
    _bare_cache = {}

    def bares(path):
        if path not in _bare_cache:
            _bare_cache[path] = _bare_classes(path.read_text(encoding="utf-8"))
        return _bare_cache[path]

    offenders = set()
    for tpl in _templates():
        text = tpl.read_text(encoding="utf-8")
        refs = _css_refs(text)
        if re.search(r"\{%\s*extends", text):  # inherits the shell layout's sheets
            refs |= shell_refs
        sheets = {s for s in (_resolve_static(r, roots) for r in refs) if s}
        platform = {s for s in sheets if _is_platform_sheet(s)}
        others = {s for s in sheets if not _is_platform_sheet(s)}
        if not platform or not others:
            continue
        platform_classes = set().union(*(bares(s) for s in platform))
        for sheet in others:
            for cls in sorted(bares(sheet) & platform_classes):
                offenders.add(f"{_rel(sheet)} bare-redefines .{cls}")
    assert not offenders, (
        "These sheets bare-redefine a platform component class that is "
        "co-loaded with them on a page, shadowing the design system (the "
        "white-nav-bar bug class). Scope the rule under the app's own "
        "container selector, or delete it:\n  " + "\n  ".join(sorted(offenders))
    )


# ---------------------------------------------------------------------------
# 4. Only platform sheets + a globally loaded shell token sheet may set :root
# ---------------------------------------------------------------------------
def test_only_platform_or_shell_sheets_define_root_tokens():
    roots = _static_roots()
    # Sheets allowed to define :root design tokens: the platform's own sheets,
    # plus any sheet loaded globally by a layout/base template (the product
    # shell's token override hook). Everything else — per-page sub-agent /
    # accounts sheets — must not. Detected by path/role, not by app name.
    allowed = {css for css in _stylesheets() if _is_platform_sheet(css)}
    for ref in _shell_layout_css_refs():
        resolved = _resolve_static(ref, roots)
        if resolved:
            allowed.add(resolved)

    offenders = []
    for css in _stylesheets():
        if css in allowed:
            continue
        if re.search(r"(?m)^\s*:root\s*\{", css.read_text(encoding="utf-8")):
            offenders.append(_rel(css))
    assert not offenders, (
        "Only platform sheets and a globally loaded product-shell token sheet "
        "may redefine `:root` design tokens; these per-page sheets must not "
        "(scope their custom properties, or move token overrides into the "
        "shell):\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 5. Every {% static 'x.css/.js' %} reference resolves to a real file
# ---------------------------------------------------------------------------
def test_static_refs_resolve():
    roots = _static_roots()
    ref_re = re.compile(r"""\{%\s*static\s+['"]([^'"]+\.(?:css|js))['"]""")
    missing = []
    for tpl in _templates():
        for ref in ref_re.findall(tpl.read_text(encoding="utf-8")):
            if not any((root / ref).exists() for root in roots):
                missing.append(f"{_rel(tpl)} → {ref}")
    assert not missing, (
        "These `{% static %}` references don't resolve to a file under any "
        "app's static/ dir (typo, wrong path, or deleted asset):\n  "
        + "\n  ".join(missing)
    )
