"""Check groups, in the order they appear in the report.

Each module has GROUP (a label people see) and run(ctx), which returns a list of
issues made with issue(). To add a group, write the module and list it here.
"""

from . import content, images, links, loading, meta, technical

GROUPS = [meta, content, images, links, technical, loading]
