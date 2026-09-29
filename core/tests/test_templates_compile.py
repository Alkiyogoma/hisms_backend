"""Every template must compile. Catches tags that an editor/formatter has
split across lines (e.g. "{%\n endif %}"), which Django can't parse — that
took down the whole Settings page."""
from pathlib import Path

from django.conf import settings
from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.test import SimpleTestCase


class TemplatesCompileTests(SimpleTestCase):
    def test_all_project_templates_compile(self):
        root = Path(settings.BASE_DIR) / "templates"
        broken = []
        for path in sorted(root.rglob("*.html")):
            name = path.relative_to(root).as_posix()
            try:
                get_template(name)
            except TemplateSyntaxError as exc:
                broken.append(f"{name}: {exc}")
        self.assertEqual(broken, [], "\n".join(broken))
