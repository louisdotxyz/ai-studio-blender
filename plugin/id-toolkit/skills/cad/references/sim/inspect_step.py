"""Listet alle Volumen einer STEP-Datei mit Name, Bounding Box und Volumen."""
import sys, gmsh, json

gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 0)
out = {}
for path in sys.argv[1:]:
    gmsh.clear()
    gmsh.model.add("m")
    gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
    ents = gmsh.model.occ.importShapes(path)
    gmsh.model.occ.synchronize()
    vols = []
    for dim, tag in gmsh.model.getEntities(3):
        bb = gmsh.model.getBoundingBox(dim, tag)
        name = gmsh.model.getEntityName(dim, tag)
        v = gmsh.model.occ.getMass(dim, tag)
        vols.append({"tag": tag, "name": name.split("/")[-1], "bb": [round(x, 1) for x in bb],
                     "size": [round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)],
                     "vol_l": round(v / 1e6, 3)})
    out[path.split("/")[-1]] = vols
print(json.dumps(out, indent=1))
gmsh.finalize()
