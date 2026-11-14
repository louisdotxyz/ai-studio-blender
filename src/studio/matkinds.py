# SPDX-License-Identifier: GPL-3.0-or-later
"""Material kinds: the same few controls for every material, and only the ones that make sense.

A material is a metal, a plastic, glass, … (KINDS). The kind decides what the Principled BSDF is set to
where the kind leaves no choice (a metal is metallic, a plastic is not, glass lets light through) and
which inputs the Material tab shows. The kind is found from the name and the shader (tab_material.py);
picking one by hand stores it in the material.
"""

KINDS = ("Metal", "Plastic", "Glass", "Rubber", "Wood", "Fabric", "Light", "Other")
KIND_KEY = "id_kind"

# What the kind fixes: Principled input → value (only inputs that are not plugged in)
FIXED = {
    "Metal": {"Metallic": 1.0, "Transmission Weight": 0.0},
    "Plastic": {"Metallic": 0.0, "Transmission Weight": 0.0, "IOR": 1.46},
    "Glass": {"Metallic": 0.0, "Transmission Weight": 1.0, "IOR": 1.5},
    "Rubber": {"Metallic": 0.0, "Transmission Weight": 0.0, "Coat Weight": 0.0, "IOR": 1.52},
    "Wood": {"Metallic": 0.0, "Transmission Weight": 0.0},
    "Fabric": {"Metallic": 0.0, "Transmission Weight": 0.0, "Coat Weight": 0.0},
    "Light": {"Metallic": 0.0},
    "Other": {},
}
# What the tab shows: (Principled input, label). Color and Roughness come first for every kind.
BASE = (("Base Color", "Color"), ("Roughness", "Roughness"))
CONTROLS = {
    "Metal": BASE + (("Anisotropic", "Brushed"), ("Coat Weight", "Clear Coat")),
    "Plastic": BASE + (("Coat Weight", "Clear Coat"), ("Subsurface Weight", "Translucent")),
    "Glass": BASE + (("IOR", "IOR"), ("Transmission Weight", "Clear")),
    "Rubber": BASE + (("Sheen Weight", "Sheen"),),
    "Wood": BASE + (("Coat Weight", "Lacquer"), ("Coat Roughness", "Lacquer Roughness")),
    "Fabric": BASE + (("Sheen Weight", "Sheen"), ("Sheen Roughness", "Sheen Roughness")),
    "Light": (("Emission Color", "Light Color"), ("Emission Strength", "Brightness"), ("Base Color", "Color")),
    "Other": BASE + (("Metallic", "Metallic"), ("Coat Weight", "Clear Coat"), ("Transmission Weight", "Clear"),
                     ("Emission Strength", "Brightness")),
}


def stored(material):
    """The kind picked by hand, None when it is found from the name and shader."""
    kind = material.get(KIND_KEY) if material is not None else None
    return kind if kind in KINDS else None


def apply(material, node, kind):
    """Makes the material this kind: stores it and sets what the kind fixes."""
    if kind not in KINDS:
        raise ValueError("Kind must be one of %s" % ", ".join(KINDS))
    material[KIND_KEY] = kind
    if node is None:
        return
    for name, value in FIXED[kind].items():
        socket = node.inputs.get(name)
        if socket is not None and not socket.is_linked:
            socket.default_value = value
    if kind == "Light":
        strength = node.inputs.get("Emission Strength")
        if strength is not None and not strength.is_linked and strength.default_value <= 0.0:
            strength.default_value = 5.0
        color = node.inputs.get("Emission Color")
        if color is not None and not color.is_linked and max(color.default_value[:3]) < 0.01:
            color.default_value = (1.0, 1.0, 1.0, 1.0)
    material.metallic = FIXED[kind].get("Metallic", material.metallic)


def set_values(node, values):
    """Principled inputs by name: "roughness", "base_color" or "color", "ior", "coat_weight" … Returns
    the inputs that were set."""
    done = []
    names = {socket.name.lower(): socket for socket in node.inputs}
    for key, value in values.items():
        key = key.lower().replace("_", " ")
        socket = names.get("base color" if key == "color" else key)
        if socket is None or socket.is_linked or not hasattr(socket, "default_value"):
            continue
        socket.default_value = (tuple(value) + (1.0,))[:4] if socket.type == 'RGBA' else value
        done.append(socket.name)
    return done
