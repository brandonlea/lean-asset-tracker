"""Templates must actually render.

Written after a real outage: a dependency upgrade (Starlette 1.x) removed the
legacy `TemplateResponse(name, context)` signature. The whole suite stayed
green because no test ever rendered a template -- they exercised pure logic
and DB-free paths. The public demo and the live tracker both returned 500 and
only a manual curl found it.

So: assert the call signature at source level (cheap, catches it before
deploy) and render every template with a dummy request (catches syntax and
undefined-global errors).
"""
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def test_no_legacy_templateresponse_signature():
    """Starlette >=0.29 deprecated and 1.x REMOVED TemplateResponse(name, ctx).
    The first argument must be the request."""
    offenders = []
    for py in APP.rglob("*.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            # TemplateResponse( followed directly by a string literal = legacy
            if re.search(r'TemplateResponse\(\s*["\']', line):
                offenders.append(f"{py.name}:{i}")
    assert not offenders, (
        "legacy TemplateResponse(name, ...) found - Starlette 1.x raises "
        f"TypeError: unhashable type: 'dict' at runtime: {offenders}"
    )


def test_every_template_parses():
    """A Jinja syntax error only shows up when the page is requested."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(APP / "templates")))
    names = [p.name for p in (APP / "templates").glob("*.html")]
    assert names, "no templates found - wrong path?"
    for name in names:
        env.get_template(name)          # raises TemplateSyntaxError if broken


@pytest.mark.parametrize("name", [p.name for p in (APP / "templates").glob("*.html")])
def test_template_has_no_unclosed_block(name):
    """Cheap structural check: {% block %}/{% endblock %} must balance."""
    text = (APP / "templates" / name).read_text()
    assert text.count("{% block") == text.count("{% endblock"), f"{name} has unbalanced blocks"
