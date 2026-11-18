"""Deterministic local masking for common contact details and labelled student IDs."""
import re


class PrivacyFilter:
    _patterns = (
        ('EMAIL', re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])')),
        ('ID', re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')),
        ('PHONE', re.compile(r'(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)')),
        ('STUDENT', re.compile(r'(?P<label>学号\s*[:：]?\s*)(?P<value>[A-Za-z0-9-]{5,30})', re.I)),
    )

    def __init__(self):
        self.values = {}

    def apply(self, value):
        changed = False
        for kind, pattern in self._patterns:
            def replace(match):
                nonlocal changed
                changed = True
                raw = match.group('value') if kind == 'STUDENT' else match.group(0)
                key = (kind, raw.casefold())
                if key not in self.values:
                    index = sum(k[0] == kind for k in self.values) + 1
                    self.values[key] = f'[{kind}_{index}]'
                return (match.group('label') if kind == 'STUDENT' else '') + self.values[key]
            value = pattern.sub(replace, value)
        return value, changed

    def task(self, candidate):
        result = dict(candidate)
        for field in ('title', 'description', 'course_name', 'external_ref', 'requirements'):
            if result.get(field):
                result[field] = self.apply(result[field])[0]
        return result
