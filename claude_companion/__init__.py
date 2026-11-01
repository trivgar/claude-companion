"""The old name of agent_companion, kept for one release (renamed 2026-11-01).

`import claude_companion.reader` hands back the very module object that
`import agent_companion.reader` does, so nothing is loaded twice and no cache
or lock is split between two copies.
"""
import importlib
import importlib.abc
import importlib.util
import sys

import agent_companion

_OLD, _NEW = "claude_companion", "agent_companion"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.startswith(_OLD + "."):
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Alias())
sys.modules[_OLD] = agent_companion
