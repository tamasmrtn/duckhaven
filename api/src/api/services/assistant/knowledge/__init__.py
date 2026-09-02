"""What the assistant knows about DuckHaven, and how it looks things up.

The corpus is ``docs/`` itself — copied into the image, never duplicated in the
repository — and the only committed artefact is ``docs_index.yaml``: one line per
page, small enough to review in a diff and small enough to keep resident in the
model's instructions. See ``generate.py`` for how it is produced and ``loader.py``
for how it is read.
"""
