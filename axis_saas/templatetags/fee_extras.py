from django import template
register = template.Library()

@register.filter
def map_attribute(lst, attr):
    if not lst:
        return []
    return [getattr(item, attr, None) for item in lst]


@register.filter
def humanize_number(value):
    """Convert a number to a human-readable format with K, M, B suffixes."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    if num is None:
        return ''
    if num < 1000:
        return str(int(num)) if num.is_integer() else f"{num:.1f}"
    if num < 1000000:
        return f"{num/1000:.1f}K" if num % 1000 != 0 else f"{int(num/1000)}K"
    if num < 1000000000:
        return f"{num/1000000:.1f}M" if num % 1000000 != 0 else f"{int(num/1000000)}M"
    return f"{num/1000000000:.1f}B" if num % 1000000000 != 0 else f"{int(num/1000000000)}B"
@register.filter
def has_feature(tenant, feature_name):
    """Return True if tenant has the given feature enabled."""
    return tenant.is_feature_enabled(feature_name, 'desktop')

@register.filter
def has_mobile_feature(tenant, feature_name):
    """Return True if tenant has the given feature enabled on mobile."""
    return tenant.is_feature_enabled(feature_name, 'mobile')

