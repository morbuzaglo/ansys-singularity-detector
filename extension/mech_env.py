# -*- coding: utf-8 -*-
# ==========================================================================
# mech_env -- Mechanical runtime layer (IronPython 2.7).
#
# WHY THIS EXISTS
# When a Mechanical scripting script is run via `App.execute_script_from_file`
# (embedded PyMechanical) or the `-script` batch entry, the engine injects
# `ExtAPI`, `Model`, `Quantity`, the `Ansys` CLR namespace, enum shortcuts,
# etc. as globals *of the executed script only*.  Modules that script then
# `import` do NOT see those names.
#
# So the executed script calls `mech_env.bind(...)` once, and every other
# Mechanical-side module reads what it needs from `mech_env.G`.
#
# The real ACT extension gets `ExtAPI` for free in every file from the ACT
# loader; this shim is only for the script-driven milestone drivers.
# ==========================================================================


class _Env(object):
    """Attribute bag for engine-injected names."""
    ExtAPI = None
    Model = None
    Quantity = None
    Ansys = None
    LoadDefineBy = None

    def require(self, name):
        v = getattr(self, name, None)
        if v is None:
            raise RuntimeError(
                "mech_env.G.%s is not bound -- the driver script must call "
                "mech_env.bind({...}) before using Mechanical-side modules." % name
            )
        return v


G = _Env()


def bind(mapping):
    """mapping: {'ExtAPI': ExtAPI, 'Model': Model, 'Quantity': Quantity,
                 'Ansys': Ansys, 'LoadDefineBy': LoadDefineBy, ...}"""
    for k, v in mapping.items():
        setattr(G, k, v)
    return G
