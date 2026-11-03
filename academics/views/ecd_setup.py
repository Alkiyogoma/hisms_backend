"""ECD Report Setup: what each ECD progress report contains, per class type and term.

Academic Progress and General Traits are lists of sections (NUMERACY, PERSONAL…)
holding items; each item is flagged for the terms (1, 2, 3) it appears in.
Every edit posts back to the page; htmx requests get the re-rendered board.
"""
import json
from types import SimpleNamespace

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic import TemplateView

from academics.ecd_report import DEFAULT_ACADEMIC_KEY, DEFAULT_TRAITS_KEY, ReportStructure, arrange
from academics.models import ECDClassType, ECDEvaluation, ECDItem, ECDReportLayout, ECDSection
from core.permissions import RoleRequiredMixin
from users.models import UserRole

TERMS = (1, 2, 3)


def _pick(value, choices, default):
    return value if value in {c for c, _ in choices} else default


def _layout(class_type, term_number):
    layout, _ = ECDReportLayout.objects.get_or_create(
        class_type=class_type, term_number=term_number,
        defaults={
            "title": f"{ECDClassType(class_type).label.upper()} PROGRESS REPORT",
            "academic_key": DEFAULT_ACADEMIC_KEY, "traits_key": DEFAULT_TRAITS_KEY,
        },
    )
    return layout


def _board_part(sections, term_number):
    """One part's sections arranged as on the printed report, each with its lines
    for this term (``term_items``) and those used only in other terms (``other_items``)."""
    for section in sections:
        items = list(section.items.all())
        for item in items:
            item.flags = [{"n": n, "on": getattr(item, f"in_term_{n}")} for n in TERMS]
        section.term_items = [i for i in items if getattr(i, f"in_term_{term_number}") and i.is_active]
        section.other_items = [i for i in items if i not in section.term_items]
    return {**arrange(sections), "count": sum(len(s.term_items) for s in sections), "empty": not sections}


class ECDReportSetupView(RoleRequiredMixin, TemplateView):
    template_name = "academics/ecd_report_setup.html"
    board_template = "academics/_ecd_setup_board.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ECD_HOD]
    required_permission = "academics.view_ecdsection"

    # ── selection ────────────────────────────────────────────────────────
    def _selection(self, data):
        class_type = _pick(data.get("class"), ECDClassType.choices, ECDClassType.PRE_K)
        term = data.get("term", "")
        term_number = int(term) if term in ("1", "2", "3") else 1
        return class_type, term_number

    def _url(self, class_type, term_number):
        return f"{reverse('academics:ecd_report_setup')}?class={class_type}&term={term_number}"

    def can_edit(self):
        user = self.request.user
        return user.role == UserRole.SUPER_ADMIN or user.has_perm("academics.change_ecdsection")

    # ── context ──────────────────────────────────────────────────────────
    def board_context(self, class_type, term_number):
        sections = list(
            ECDSection.objects.filter(class_type=class_type, is_active=True)
            .prefetch_related("items").order_by("sort_order", "pk")
        )
        layout = _layout(class_type, term_number)
        counts = {
            n: ECDItem.objects.filter(section__class_type=class_type, section__is_active=True).for_term(n).count()
            for n in TERMS
        }
        return {
            "class_type": class_type,
            "class_label": ECDClassType(class_type).label,
            "term_number": term_number,
            "terms": [{"n": n, "count": counts[n], "active": n == term_number} for n in TERMS],
            "other_terms": [n for n in TERMS if n != term_number],
            "layout": layout,
            "layout_json": {
                "title": layout.title, "column_label": layout.column_label, "accent": layout.accent,
                "academic_key": layout.academic_key, "traits_key": layout.traits_key,
                "class_label": ECDClassType(class_type).label,
            },
            "parts": [
                {"key": "academic", "label": "Academic Progress", "scale": "4 · 3 · 2 · 1",
                 "board": _board_part([s for s in sections if s.part == ECDSection.Part.ACADEMIC], term_number)},
                {"key": "traits", "label": "General Traits",
                 "scale": " · ".join(code for code, _ in layout.traits_key),
                 "board": _board_part([s for s in sections if s.part == ECDSection.Part.TRAITS], term_number)},
            ],
            "can_edit": self.can_edit(),
            "column_choices": ECDSection.Column.choices,
            "style_choices": ECDSection.Style.choices,
            "accent_choices": ECDReportLayout.Accent.choices,
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_type, term_number = self._selection(self.request.GET)
        ctx.update(self.board_context(class_type, term_number))
        ctx["classes"] = [
            {"value": v, "label": label, "active": v == class_type,
             "url": self._url(v, term_number)}
            for v, label in ECDClassType.choices
        ]
        ctx["academics_tab"] = "ecd_setup"
        return ctx

    # ── actions ──────────────────────────────────────────────────────────
    def post(self, request, *args, **kwargs):
        if not self.can_edit():
            raise PermissionDenied()
        class_type, term_number = self._selection(request.POST)
        action = request.POST.get("action", "")
        handler = getattr(self, f"do_{action}", None)
        if handler is None:
            message, level = "Unknown action.", "error"
        else:
            with transaction.atomic():
                message, level = handler(request.POST, class_type, term_number)

        if not request.headers.get("HX-Request"):
            getattr(messages, level)(request, message)
            return redirect(self._url(class_type, term_number))

        html = render_to_string(self.board_template, self.board_context(class_type, term_number), request=request)
        response = HttpResponse(html)
        trigger = {"showToast": {"message": message, "level": level}}
        if level == "success" and request.POST.get("close"):
            trigger["hui:closeModal"] = True
        response["HX-Trigger"] = json.dumps(trigger)
        return response

    @staticmethod
    def _pk(data, name):
        value = data.get(name, "")
        if not str(value).isdigit():
            raise Http404(f"No {name} given.")
        return int(value)

    def _section(self, data, class_type):
        return get_object_or_404(ECDSection, pk=self._pk(data, "section"), class_type=class_type)

    def _item(self, data, class_type):
        return get_object_or_404(ECDItem, pk=self._pk(data, "item"), section__class_type=class_type)

    def _section_fields(self, data, section):
        section.title = data.get("title", "").strip()[:80]
        section.column = _pick(data.get("column"), ECDSection.Column.choices, ECDSection.Column.LEFT)
        section.style = _pick(data.get("style"), ECDSection.Style.choices, ECDSection.Style.TABLE)
        section.counts_in_average = (
            section.part == ECDSection.Part.ACADEMIC and data.get("counts_in_average") == "on"
        )

    def do_add_section(self, data, class_type, term_number):
        part = _pick(data.get("part"), ECDSection.Part.choices, ECDSection.Part.ACADEMIC)
        section = ECDSection(class_type=class_type, part=part)
        self._section_fields(data, section)
        if not section.title:
            return "Give the section a title.", "error"
        last = ECDSection.objects.filter(class_type=class_type).aggregate(m=Max("sort_order"))["m"]
        section.sort_order = (last or 0) + 1
        section.save()
        return f'Added section "{section.title}".', "success"

    def do_edit_section(self, data, class_type, term_number):
        section = self._section(data, class_type)
        self._section_fields(data, section)
        if not section.title:
            return "Give the section a title.", "error"
        section.save()
        return f'Saved "{section.title}".', "success"

    def do_delete_section(self, data, class_type, term_number):
        section = self._section(data, class_type)
        title = section.title
        used = ECDEvaluation.objects.filter(item__section=section).values("report_card").distinct().count()
        if used:
            return (f'"{title}" is already rated on {used} report{"s" if used != 1 else ""}; '
                    f"switch its lines off for the terms you no longer want instead."), "error"
        section.delete()
        return f'Deleted section "{title}" and its items.', "success"

    def do_move_section(self, data, class_type, term_number):
        section = self._section(data, class_type)
        # Swap places with the neighbour in the same column; other blocks keep their positions.
        ordered = self._renumber(ECDSection.objects.filter(class_type=class_type).order_by("sort_order", "pk"))
        self._move([s for s in ordered if s.part == section.part and s.column == section.column],
                   section, data.get("dir"))
        return f'Moved "{section.title}".', "success"

    def do_add_item(self, data, class_type, term_number):
        section = self._section(data, class_type)
        labels = [line.strip()[:200] for line in data.get("label", "").splitlines() if line.strip()]
        if not labels:
            return "Type the item's wording first.", "error"
        last = section.items.aggregate(m=Max("sort_order"))["m"] or 0
        terms = self._terms(data) or [term_number]
        for i, label in enumerate(labels, start=1):
            ECDItem.objects.create(
                section=section, label=label, sort_order=last + i,
                **{f"in_term_{n}": n in terms for n in TERMS},
            )
        noun = "item" if len(labels) == 1 else f"{len(labels)} items"
        return f"Added {noun} to {section.title} ({', '.join(f'T{n}' for n in terms)}).", "success"

    def do_edit_item(self, data, class_type, term_number):
        item = self._item(data, class_type)
        label = data.get("label", "").strip()[:200]
        if not label:
            return "The item needs some wording.", "error"
        item.label = label
        item.allows_text = data.get("allows_text") == "on"
        for n in TERMS:
            setattr(item, f"in_term_{n}", n in self._terms(data))
        item.save()
        return "Item saved.", "success"

    def do_toggle_item_term(self, data, class_type, term_number):
        item = self._item(data, class_type)
        n = int(data.get("n")) if data.get("n") in ("1", "2", "3") else term_number
        field = f"in_term_{n}"
        setattr(item, field, not getattr(item, field))
        item.save(update_fields=[field, "updated_at"])
        state = "added to" if getattr(item, field) else "removed from"
        return f'"{item.label}" {state} Term {n}.', "success"

    def do_move_item(self, data, class_type, term_number):
        item = self._item(data, class_type)
        self._move(self._renumber(item.section.items.order_by("sort_order", "pk")), item, data.get("dir"))
        return "Item moved.", "success"

    def do_delete_item(self, data, class_type, term_number):
        item = self._item(data, class_type)
        used = item.evaluations.count()
        if used:
            return (f'"{item.label}" is already rated on {used} report{"s" if used != 1 else ""}; '
                    f"switch it off for the terms you no longer want instead."), "error"
        item.delete()
        return f'Deleted "{item.label}".', "success"

    def do_save_layout(self, data, class_type, term_number):
        layout = _layout(class_type, term_number)
        title = data.get("title", "").strip()[:80]
        if not title:
            return "The report needs a title.", "error"
        layout.title = title
        layout.column_label = data.get("column_label", "").strip()[:20] or "Points"
        layout.accent = _pick(data.get("accent"), ECDReportLayout.Accent.choices, layout.accent)
        layout.academic_key = [
            [value, data.get(f"academic_{value}", "").strip()[:40] or dict(DEFAULT_ACADEMIC_KEY)[value]]
            for value in ("4", "3", "2", "1")
        ]
        traits = []
        for i, (code, label) in enumerate(DEFAULT_TRAITS_KEY):
            new_code = data.get(f"traits_code_{i}", "").strip().upper()[:2] or code
            traits.append([new_code, data.get(f"traits_label_{i}", "").strip()[:40] or label])
        if len({code for code, _ in traits}) < len(traits):
            return "Each General Traits rating needs its own letter.", "error"
        layout.traits_key = traits
        layout.save()
        return f"Report settings saved for Term {term_number}.", "success"

    def do_copy_term(self, data, class_type, term_number):
        source = int(data.get("from")) if data.get("from") in ("1", "2", "3") else None
        if not source or source == term_number:
            return "Pick another term to copy from.", "error"
        items = ECDItem.objects.filter(section__class_type=class_type)
        for item in items:
            setattr(item, f"in_term_{term_number}", getattr(item, f"in_term_{source}"))
            item.save(update_fields=[f"in_term_{term_number}", "updated_at"])
        src, dst = _layout(class_type, source), _layout(class_type, term_number)
        for field in ("title", "academic_key", "traits_key", "column_label", "accent"):
            setattr(dst, field, getattr(src, field))
        dst.save()
        return f"Term {term_number} now matches Term {source}.", "success"

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _terms(data):
        return sorted({int(n) for n in data.getlist("terms") if n in ("1", "2", "3")})

    @staticmethod
    def _renumber(queryset):
        """Give the rows distinct positions 1..n in their current order."""
        rows = list(queryset)
        for pos, row in enumerate(rows, start=1):
            if row.sort_order != pos:
                row.sort_order = pos
                row.save(update_fields=["sort_order", "updated_at"])
        return rows

    @staticmethod
    def _move(siblings, obj, direction):
        """Swap ``obj``'s position with the sibling before or after it."""
        i = next(i for i, o in enumerate(siblings) if o.pk == obj.pk)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(siblings):
            a, b = siblings[i], siblings[j]
            a.sort_order, b.sort_order = b.sort_order, a.sort_order
            for row in (a, b):
                row.save(update_fields=["sort_order", "updated_at"])


class ECDReportSetupPreviewView(ECDReportSetupView):
    """The printed report for one class and term, with no learner: checks the layout."""
    template_name = "academics/ecd_report_preview.html"

    def get_context_data(self, **kwargs):
        ctx = TemplateView.get_context_data(self, **kwargs)
        class_type, term_number = self._selection(self.request.GET)
        structure = ReportStructure(class_type, term_number)
        blank = [SimpleNamespace(title=s.title, style=s.style, column=s.column,
                                 lines=[{"label": i.label, "rating": "", "score": None, "note": ""} for i in s.term_items])
                 for s in structure.sections]
        by_pk = {s.pk: b for s, b in zip(structure.sections, blank)}
        parts = [
            {**part, **{place: [by_pk[s.pk] for s in part[place]] for place in ("sections", "top", "left", "right", "bottom")}}
            for part in structure.parts
        ]
        term = SimpleNamespace(name=f"Term {term_number}", get_term_number_display=f"Term {term_number}",
                               academic_year=SimpleNamespace(name=""))
        ctx.update({
            "layout_preview": True,
            "setup_url": self._url(class_type, term_number),
            "class_label": ECDClassType(class_type).label,
            "term_number": term_number,
            "student": SimpleNamespace(get_full_name="", admission_no=""),
            "report": SimpleNamespace(term=term, signed_off_at=None, published_at=None, signed_off_by=None),
            "attendance": {"absent": ""},
            "ecd": {"layout": structure.layout, "parts": parts, "legacy": False,
                    "avg_score": None, "remarks_label": "", "teacher_comments": ""},
        })
        return ctx

    def post(self, request, *args, **kwargs):
        raise PermissionDenied()
