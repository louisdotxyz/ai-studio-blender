"""S2N Modalanalyse: STEP -> gmsh (C3D10) -> CalculiX *FREQUENCY -> npz für Blender.

Einheiten: mm, N, t, s  (E in MPa, rho in t/mm3, Masse in t).
Aufruf:  python modal.py configs/<box>.json
"""
import sys, os, json, re, time, math, subprocess
import numpy as np
import gmsh

cfg = json.load(open(sys.argv[1]))
NAME = cfg["name"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "runs", cfg.get("run", NAME))
os.makedirs(RUN, exist_ok=True)
t0 = time.time()
log = lambda *a: print(f"[{time.time()-t0:7.1f}s]", *a, flush=True)

# ------------------------------------------------------------------ geometry
gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 0)
gmsh.option.setNumber("General.NumThreads", 8)
gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
gmsh.option.setNumber("Geometry.ToleranceBoolean", cfg.get("glue_tol_mm", 0.3))
gmsh.model.add(NAME)
if "box" in cfg:                                   # validation: plain plate
    t = gmsh.model.occ.addBox(0, 0, 0, *cfg["box"]); gmsh.model.occ.synchronize()
    gmsh.model.setEntityName(3, t, "Plate")
else:
    gmsh.model.occ.importShapes(os.path.join(ROOT, cfg["step"]))
gmsh.model.occ.synchronize()

exclude = re.compile(cfg.get("exclude", "^$"))
keep, names = [], {}
for d, t in gmsh.model.getEntities(3):
    n = gmsh.model.getEntityName(d, t).split("/")[-1]
    if exclude.search(n):
        gmsh.model.occ.remove([(3, t)], recursive=True)
    else:
        keep.append((3, t)); names[t] = n
gmsh.model.occ.synchronize()
log("parts:", [names[t] for _, t in keep])

# make touching parts conformal (= bonded / verleimt)
pbox = {t: gmsh.model.getBoundingBox(3, t) for _, t in keep}
fmap = {}
if len(keep) > 1:
    _, omap = gmsh.model.occ.fragment(keep, [])
    gmsh.model.occ.synchronize()
    for (d0, tk), news in zip(keep, omap):
        for d1, t1 in news:
            fmap.setdefault(t1, names[tk])
    if cfg.get("dedupe", False):
        gmsh.model.occ.removeAllDuplicates(); gmsh.model.occ.synchronize(); fmap = {}
def owner(t):
    c = gmsh.model.occ.getCenterOfMass(3, t)
    best, bv = None, 1e30
    for tb, b in pbox.items():
        if all(b[i] - 0.5 <= c[i] <= b[i + 3] + 0.5 for i in range(3)):
            v = (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2])
            if v < bv: best, bv = tb, v
    return names[best] if best is not None else "unknown"
vols = [t for _, t in gmsh.model.getEntities(3)]
part_of = {t: fmap.get(t) or owner(t) for t in vols}
log("volumes after fragment:", len(vols), "unknown:", sum(1 for v in part_of.values() if v == "unknown"))

# ------------------------------------------------------------------ driver holes (circle edges of the baffle)
def circle_of_curve(c):
    lo, hi = gmsh.model.getParametrizationBounds(1, c)
    pts = np.array([gmsh.model.getValue(1, c, [lo[0] + (hi[0] - lo[0]) * f]) for f in (0.0, 0.25, 0.5, 0.75)])
    a, b, cc = pts[0], pts[1], pts[2]
    n = np.cross(b - a, cc - a);
    if np.linalg.norm(n) < 1e-9: return None
    n /= np.linalg.norm(n)
    # circumcenter
    ab, ac = b - a, cc - a
    w = np.cross(ab, ac)
    ctr = a + (np.cross(w, ab) * np.dot(ac, ac) + np.cross(ac, w) * np.dot(ab, ab)) / (2 * np.dot(w, w))
    r = np.linalg.norm(pts - ctr, axis=1)
    if r.std() > 0.5: return None
    return ctr, n, r.mean()

drivers = []
for dv in cfg.get("drivers", []):
    if "select_box" in dv:                         # driver without round hole: mount nodes in a box, cg given
        drivers.append({**dv, "center": np.array(dv["cg"], float), "axis": np.array([0, 0, 1.0]), "hole_r": 0.0})
        log(f"driver {dv['label']}: mount box {dv['select_box']}")
        continue
    bvols = [t for t in vols if part_of[t] == dv["baffle"]]
    best = None
    for t in bvols:
        for _, s in gmsh.model.getBoundary([(3, t)], oriented=False):
            for _, c in gmsh.model.getBoundary([(2, s)], oriented=False):
                if gmsh.model.getType(1, c) != "Circle": continue
                res = circle_of_curve(abs(c))
                if res and res[2] < dv["flange_r"] and (best is None or res[2] > best[2]):
                    best = res
    if best is None:
        raise SystemExit(f"no driver hole found in {dv['baffle']}")
    ctr, n, r = best
    # motor side = side of the box centroid
    bb = gmsh.model.getBoundingBox(-1, -1)
    box_c = np.array([(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2])
    if np.dot(box_c - ctr, n) < 0: n = -n
    if dv.get("motor_side") == "flip": n = -n
    drivers.append({**dv, "center": ctr, "axis": n, "hole_r": r})
    log(f"driver {dv['label']}: hole r={r:.1f} mm at {np.round(ctr,1)}, axis {np.round(n,3)}")

# ------------------------------------------------------------------ mesh
h = cfg.get("mesh_mm", 12)
gmsh.option.setNumber("Mesh.MeshSizeMax", h)
gmsh.option.setNumber("Mesh.MeshSizeMin", h * 0.3)
gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
gmsh.option.setNumber("Mesh.ElementOrder", 2)
gmsh.option.setNumber("Mesh.HighOrderOptimize", 0)
gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)   # straight mid-nodes: no negative jacobians
gmsh.option.setNumber("Mesh.Algorithm3D", 10)       # HXT, parallel
def ntets():
    return sum(len(tg) for t in gmsh.model.getEntities(3) for typ, tg in zip(*gmsh.model.mesh.getElements(3, t[1])[:2]) if typ == 11)
ok = False
for attempt, algo in enumerate((10, 1, 1)):
    if attempt == 2:
        log("heal + dedupe, retry")
        gmsh.model.occ.healShapes(); gmsh.model.occ.removeAllDuplicates(); gmsh.model.occ.synchronize()
        vols = [t for _, t in gmsh.model.getEntities(3)]
        part_of = {t: owner(t) for t in vols}
    gmsh.model.mesh.clear()
    gmsh.option.setNumber("Mesh.Algorithm3D", algo)
    try:
        gmsh.model.mesh.generate(3)
        n = ntets()
        if n > 0:
            ok = True; break
        log(f"algo {algo}: no tets")
    except Exception as ex:
        log(f"algo {algo} failed:", str(ex)[:120])
if not ok:
    raise SystemExit("meshing failed")
log("mesh done")

ntags, ncoord, _ = gmsh.model.mesh.getNodes()
X = ncoord.reshape(-1, 3)
nid = ntags.astype(np.int64)
pos = {int(n): i for i, n in enumerate(nid)}
elems, epart = [], []
for t in vols:
    et, etags, enodes = gmsh.model.mesh.getElements(3, t)
    for typ, tg, nd in zip(et, etags, enodes):
        if typ != 11: continue                      # 10-node tet
        nd = nd.reshape(-1, 10).astype(np.int64)
        elems.append(nd); epart += [part_of[t]] * len(nd)
E = np.vstack(elems)
# straighten mid-side nodes (exact edge midpoints) -> no negative jacobians on tiny curved elements
for k, (a, b) in enumerate(((0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3))):   # gmsh tet10 edge order
    X[[pos[int(v)] for v in E[:, 4 + k]]] = 0.5 * (X[[pos[int(v)] for v in E[:, a]]] + X[[pos[int(v)] for v in E[:, b]]])
epart = np.array(epart)
gmsh.finalize()
log(f"nodes {len(nid)}  tets {len(E)}  DOF {3*len(nid)/1e6:.2f} M")

# connectivity check: floating parts give extra rigid-body modes
used = np.unique(E[:, :4])
parent = {}
def find(a):
    while parent.get(a, a) != a:
        parent[a] = parent.get(parent[a], parent[a]); a = parent[a]
    return a
for row in E[:, :4]:
    r0 = find(int(row[0]))
    for v in row[1:]:
        rv = find(int(v))
        if rv != r0: parent[rv] = r0
comps = {}
for i, row in enumerate(E[:, 0]):
    comps.setdefault(find(int(row)), set()).add(epart[i])
log("connected components:", len(comps), [sorted(c)[:4] for c in comps.values()] if len(comps) > 1 else "")

# ------------------------------------------------------------------ CalculiX input
mat = cfg["material"]
parts = sorted(set(epart))
inp = []
inp.append("*HEADING\nS2N " + NAME)
inp.append("*NODE")
inp += [f"{int(n)},{x:.6f},{y:.6f},{z:.6f}" for n, (x, y, z) in zip(nid, X)]
# gmsh 10-node tet order -> ccx C3D10 order: last two mid nodes swapped (gmsh 8,9 = edges 2-3? -> ccx 9,8)
eid = 0
eset = {}
for p in parts:
    idx = np.where(epart == p)[0]
    safe = "P_" + re.sub(r"[^A-Za-z0-9]", "_", p)
    eset[p] = safe
    inp.append(f"*ELEMENT, TYPE=C3D10, ELSET={safe}")
    for i in idx:
        eid += 1
        e = E[i]
        o = [e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[9], e[8]]
        inp.append(f"{eid}," + ",".join(str(int(v)) for v in o))
inp.append("*ELSET, ELSET=EALL")
inp += [eset[p] for p in parts]

def material_block(mname, m):
    s = [f"*MATERIAL, NAME={mname}"]
    if m.get("type") == "orthotropic":
        s.append("*ELASTIC, TYPE=ENGINEERING CONSTANTS")
        s.append(f"{m['E1']},{m['E2']},{m['E3']},{m['nu12']},{m['nu13']},{m['nu23']},{m['G12']},{m['G13']}")
        s.append(f"{m['G23']}")
    else:
        s.append("*ELASTIC"); s.append(f"{m['E']},{m['nu']}")
    s.append("*DENSITY"); s.append(f"{m['rho']}")
    return s

overrides = cfg.get("part_material", {})          # regex -> material name in cfg["materials"]
materials = {"WOOD": mat, **cfg.get("materials", {})}
for k, m in materials.items():
    inp += material_block(k, m)

# orthotropic panels: local 3-axis = panel normal (smallest bbox extent of the part)
for p in parts:
    mname = "WOOD"
    for rx, mn in overrides.items():
        if re.search(rx, p): mname = mn
    m = materials[mname]
    if m.get("type") == "orthotropic":
        idx = np.where(epart == p)[0]
        pts = X[[pos[int(v)] for v in np.unique(E[idx, :4])]]
        c = pts - pts.mean(0)
        w, V = np.linalg.eigh(c.T @ c)                  # smallest eigen-vector = normal
        n3, a1 = V[:, 0], V[:, 2]                       # a1 = longest in-plane direction (grain of face veneer)
        a2 = np.cross(n3, a1)
        on = "OR_" + eset[p][2:]
        inp.append(f"*ORIENTATION, NAME={on}")
        inp.append(",".join(f"{v:.6f}" for v in list(a1) + list(a2)))
        inp.append(f"*SOLID SECTION, ELSET={eset[p]}, MATERIAL={mname}, ORIENTATION={on}")
    else:
        inp.append(f"*SOLID SECTION, ELSET={eset[p]}, MATERIAL={mname}")

# drivers: rigid ring (flange) + mass at the driver's centre of gravity
next_node = int(nid.max()) + 1
next_el = eid + 1
driver_info = []
for k, dv in enumerate(drivers):
    if "select_box" in dv:
        b = dv["select_box"]
        m = np.all((X >= np.array(b[0::2])) & (X <= np.array(b[1::2])), axis=1)
        ring = nid[m]
        ref, rot = next_node, next_node + 1; next_node += 2
        cg = np.array(dv["cg"], float)
        inp.append("*NODE"); inp.append(f"{ref},{cg[0]:.4f},{cg[1]:.4f},{cg[2]:.4f}"); inp.append(f"{rot},{cg[0]:.4f},{cg[1]:.4f},{cg[2]:.4f}")
        inp.append(f"*NSET, NSET=RING{k}")
        inp += [",".join(str(int(v)) for v in ring[i:i + 12]) for i in range(0, len(ring), 12)]
        inp.append(f"*RIGID BODY, NSET=RING{k}, REF NODE={ref}, ROT NODE={rot}")
        inp.append(f"*ELEMENT, TYPE=MASS, ELSET=DMASS{k}"); inp.append(f"{next_el},{ref}"); next_el += 1
        inp.append(f"*MASS, ELSET=DMASS{k}"); inp.append(f"{dv['mass_kg'] / 1000.0}")
        driver_info.append({"label": dv["label"], "ring_nodes": int(len(ring)), "cg": cg.tolist(), "mass_kg": dv["mass_kg"]})
        log(f"driver {dv['label']}: {len(ring)} mount nodes, mass {dv['mass_kg']} kg")
        continue
    ctr, n = dv["center"], dv["axis"]
    rel = X - ctr
    ax = rel @ n
    rad = np.linalg.norm(rel - np.outer(ax, n), axis=1)
    bnodes = np.unique(E[np.isin(epart, dv.get("ring_parts", [dv["baffle"]]))].ravel())
    bi = np.array([pos[int(v)] for v in bnodes])
    sel = bi[(rad[bi] >= dv["hole_r"] - 0.5) & (rad[bi] <= dv["flange_r"]) & (np.abs(ax[bi]) < dv.get("axial_mm", 40))]
    ring = nid[sel]
    ref, rot = next_node, next_node + 1; next_node += 2
    cg = ctr + n * dv["cg_mm"]
    inp.append("*NODE"); inp.append(f"{ref},{cg[0]:.4f},{cg[1]:.4f},{cg[2]:.4f}")
    inp.append(f"{rot},{cg[0]:.4f},{cg[1]:.4f},{cg[2]:.4f}")
    inp.append(f"*NSET, NSET=RING{k}")
    inp += [",".join(str(int(v)) for v in ring[i:i + 12]) for i in range(0, len(ring), 12)]
    inp.append(f"*RIGID BODY, NSET=RING{k}, REF NODE={ref}, ROT NODE={rot}")
    inp.append(f"*ELEMENT, TYPE=MASS, ELSET=DMASS{k}"); inp.append(f"{next_el},{ref}"); next_el += 1
    inp.append(f"*MASS, ELSET=DMASS{k}"); inp.append(f"{dv['mass_kg'] / 1000.0}")
    driver_info.append({"label": dv["label"], "ring_nodes": int(len(ring)), "cg": cg.tolist(), "mass_kg": dv["mass_kg"]})
    log(f"driver {dv['label']}: {len(ring)} ring nodes, mass {dv['mass_kg']} kg at cg offset {dv['cg_mm']} mm")

nm = cfg.get("modes", 20)
rigid = 6 + max(0, len(comps) - 1) * 6
inp.append("*STEP")
inp.append("*FREQUENCY")
inp.append(f"{nm + rigid}")
inp.append("*NODE FILE"); inp.append("U")
inp.append("*END STEP")
jobname = os.path.join(RUN, NAME)
open(jobname + ".inp", "w").write("\n".join(inp) + "\n")
log("inp written", jobname + ".inp")

# ------------------------------------------------------------------ solve
env = dict(os.environ, OMP_NUM_THREADS=str(cfg.get("threads", 8)), CCX_NPROC_EQUATION_SOLVER=str(cfg.get("threads", 8)))
r = subprocess.run(["ccx", "-i", jobname], cwd=RUN, env=env, capture_output=True, text=True)
open(jobname + ".ccx.log", "w").write(r.stdout + r.stderr)
log("ccx rc", r.returncode)
if r.returncode != 0:
    print(r.stdout[-3000:], r.stderr[-2000:]); sys.exit(1)

# ------------------------------------------------------------------ results
dat = open(jobname + ".dat").read()
freqs = []
m = re.search(r"E I G E N V A L U E   O U T P U T(.*?)(?:P A R T I C|\Z)", dat, re.S)
for line in m.group(1).splitlines():
    p = line.split()
    if len(p) >= 4 and p[0].isdigit():
        freqs.append(float(p[3]))
freqs = np.array(freqs)

# frd displacement blocks
modes = []
cur = None
with open(jobname + ".frd") as f:
    for line in f:
        if line.startswith(" -4  DISP"):
            cur = np.zeros((len(nid), 3), np.float32)
        elif cur is not None and line.startswith(" -1"):
            n = int(line[3:13]);
            if n in pos:
                cur[pos[n]] = [float(line[13:25]), float(line[25:37]), float(line[37:49])]
        elif cur is not None and line.startswith(" -3"):
            modes.append(cur); cur = None
modes = np.array(modes)
log("modes read", modes.shape, "freqs", len(freqs))

# skip rigid-body modes (< 2 Hz)
flex = np.where(freqs > cfg.get("rigid_cut_hz", 2.0))[0]
# surface triangles (outer skin of every part, corner nodes only)
faces = {}
for i, e in enumerate(E):
    for a, b, c in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
        key = tuple(sorted((int(e[a]), int(e[b]), int(e[c]))))
        if key in faces: faces[key] = None
        else: faces[key] = (int(e[a]), int(e[b]), int(e[c]), i)
tris = np.array([v[:3] for v in faces.values() if v is not None])
tri_el = np.array([v[3] for v in faces.values() if v is not None])
sv = np.unique(tris)
smap = {int(n): i for i, n in enumerate(sv)}
tris_s = np.vectorize(smap.get)(tris)
verts = X[[pos[int(n)] for n in sv]]
sidx = np.array([pos[int(n)] for n in sv])
pnames = sorted(set(epart))
tri_part = np.array([pnames.index(epart[i]) for i in tri_el])

summary = []
for j in flex:
    u = modes[j]
    mag = np.linalg.norm(u, axis=1)
    per = {p: float(np.max(mag[np.unique([pos[int(v)] for v in E[epart == p, :4].ravel()])])) for p in pnames}
    top = sorted(per.items(), key=lambda kv: -kv[1])[:3]
    summary.append({"mode": int(j + 1), "f_hz": round(float(freqs[j]), 1), "dominant": [t[0] for t in top]})

np.savez_compressed(os.path.join(RUN, NAME + "_modes.npz"),
                    verts=verts.astype(np.float32), tris=tris_s.astype(np.int32), tri_part=tri_part.astype(np.int16),
                    part_names=np.array(pnames), freqs=freqs[flex].astype(np.float32),
                    modes=modes[flex][:, sidx, :].astype(np.float32))
res = {"name": NAME, "mesh_mm": h, "nodes": int(len(nid)), "tets": int(len(E)), "components": len(comps),
       "drivers": driver_info, "all_freqs": [round(float(f), 2) for f in freqs], "modes": summary,
       "wood_mass_kg": None, "runtime_s": round(time.time() - t0, 1)}
# mass of the wood (sanity check vs. known box weights)
vol = 0.0
for p in pnames:
    pass
json.dump(res, open(os.path.join(RUN, NAME + "_result.json"), "w"), indent=1)
log("DONE")
for s in summary: print(f"  mode {s['mode']:3d}  {s['f_hz']:8.1f} Hz   {', '.join(s['dominant'])}")
