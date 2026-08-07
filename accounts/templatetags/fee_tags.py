from django import template
register = template.Library()

@register.filter
def get_attr(obj, name):
    return getattr(obj, name, 0)