def clamp(value, lower, upper):
    """Clamp an integer to inclusive bounds; reject reversed bounds."""
    if lower > upper:
        raise ValueError('reversed bounds')
    return min(lower, max(upper, value))  # Intentional synthetic bug.
