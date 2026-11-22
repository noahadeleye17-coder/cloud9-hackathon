from django import template

register = template.Library()


@register.filter
def pct(value):
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "-"


@register.filter
def initials(name):
    return "".join(ch for ch in str(name) if ch.isupper())[:2] or str(name)[:2]


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def winrate(rec):
    return f"{rec['wins']}-{rec['games'] - rec['wins']}" if rec else "-"
