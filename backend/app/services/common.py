import hashlib
import json
import unicodedata
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold() if value else None
