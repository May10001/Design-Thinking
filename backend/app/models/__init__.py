"""SQLAlchemy models reflected from the Alembic-managed schema.

Call ``reflect_models(engine)`` after migrations. Returned classes are Users,
Tasks, Imports, DraftItems and ReviewBatches; no schema changes occur here.
"""
from collections import namedtuple
from sqlalchemy.ext.automap import automap_base

ModelSet = namedtuple('ModelSet', ['Users', 'Tasks', 'Imports', 'DraftItems', 'ReviewBatches'])


def reflect_models(engine):
    base = automap_base()
    base.prepare(autoload_with=engine, generate_relationship=lambda *args, **kwargs: None)
    return ModelSet(base.classes.users, base.classes.tasks, base.classes.imports,
                    base.classes.draft_items, base.classes.review_batches)
