"""Immutable cases for required_text; candidate never receives expected results."""
CASES = (
    ('test_plain', {'name': 'Alpha'}, 'name', 'Alpha', None),
    ('test_trim', {'name': ' \tAlpha \n'}, 'name', 'Alpha', None),
    ('test_unicode', {'name': '\u2003Café\u2003'}, 'name', 'Café', None),
    ('test_internal_space', {'name': 'A  B'}, 'name', 'A  B', None),
    ('test_empty', {'name': ''}, 'name', None, (ValueError, 'name is required')),
    ('test_blank', {'name': ' \t\n'}, 'name', None, (ValueError, 'name is required')),
    ('test_none', {'name': None}, 'name', None, (ValueError, 'name is required')),
    ('test_missing', {}, 'name', None, (KeyError, 'name')),
    ('test_field', {'other': None}, 'other', None, (ValueError, 'other is required')),
)
