"""Template helpers for the Hodari component kit.

Currently provides ``elided_pages`` — a thin wrapper over Django's
``Paginator.get_elided_page_range`` so the canonical <c-pagination> component
can render a compact ``1 2 … 77`` page list without the view having to
precompute anything.
"""
from django import template
from django.core.paginator import Paginator

register = template.Library()

ELLIPSIS = Paginator.ELLIPSIS


@register.simple_tag
def elided_pages(page_obj, on_each_side=1, on_ends=1):
    """Return a compact list of page numbers with '…' gaps for a page_obj.

    Usage:
        {% elided_pages page_obj as pages %}
        {% for p in pages %} … {% endfor %}
    Yields ints for real pages and the string '…' for elided gaps.
    """
    if page_obj is None:
        return []
    paginator = getattr(page_obj, "paginator", None)
    if paginator is None:
        return []
    try:
        return list(
            paginator.get_elided_page_range(
                page_obj.number, on_each_side=on_each_side, on_ends=on_ends
            )
        )
    except Exception:
        return list(paginator.page_range)


@register.filter
def split(value, sep=","):
    """Split a string into a list. Usage: {{ '20,50,100'|split:',' }}"""
    if value is None:
        return []
    return [part.strip() for part in str(value).split(sep) if part.strip()]
