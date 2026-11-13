// Onshape helpers for Claude: paste once per tab (javascript_tool), they live until the next navigation.
// Everything runs with the user's own Onshape session (cookie + XSRF token), nothing leaves the browser
// except the export package, which goes straight to Blender's AI Studio (http://127.0.0.1:<port>/cad).
//
//   await OS.session()                     signed in? {ok, name} – else a clear message what to do
//   await OS.info()                        document, workspace, element of the open tab
//   await OS.parts()                       [{name, material, min, max}] of the Part Studio (one FS eval)
//   await OS.addFeatures([f1, f2, …])      several features in a row, stops at the first ERROR
//   await OS.updateFeatures([f, …])        change several features in one regeneration
//   await OS.setVariables(eid, [{name, type, expression}])   Variable Studio, all in one call
//   await OS.exportPackage({port, token, name, version, folder, floor, chat: {name, url}})
//        GLB (glTF) + <name>.cad.json (ids, materials, boxes, mounts) straight into Blender's folder.
//        chat: where this export comes from, so Blender can send CAD jobs back (blender_jobs):
//          name = the chat's title as the user sees it, url = its link if known (a chat cannot read its
//          own link: ask the user to paste it once, https://claude.ai/chat/<id>), session = the name of
//          a Claude Code session (Blender then also messages it directly while it is open)
//
// Mounting points: mate connectors whose feature name starts with "MOUNT |" (e.g. "MOUNT | Lenker"):
// origin, X and Z of the connector become the empty in Blender (Z points into the part it is mounted on).
(() => {
  const ids = () => {
    const m = location.pathname.match(/documents\/(\w+)\/w\/(\w+)\/e\/(\w+)/);
    if (!m) throw new Error("Open a Part Studio of a document first");
    return {d: m[1], w: m[2], e: m[3]};
  };
  const SIGN_IN = "Not signed in to Onshape: sign in to Onshape in the browser pane of the Claude app " +
                  "(cad.onshape.com), then run this again.";
  const token = () => {
    const c = document.cookie.split(";").map(s => s.trim()).find(s => s.startsWith("XSRF-TOKEN="));
    if (!c) throw new Error(SIGN_IN);
    return c.slice(11);   // raw, not decoded (decoded gives 401)
  };
  const OS = window.OS = {};
  // Signed in? Checked before anything else (info, export …): a clear sentence instead of a 401.
  OS.session = async () => {
    if (!/(^|\.)onshape\.com$/.test(location.hostname)) {
      return {ok: false, message: "This tab is not Onshape: open cad.onshape.com in the browser pane first."};
    }
    const c = document.cookie.split(";").map(s => s.trim()).find(s => s.startsWith("XSRF-TOKEN="));
    if (!c) return {ok: false, message: SIGN_IN};
    const r = await fetch("/api/users/sessioninfo", {credentials: "include",
      headers: {Accept: "application/json;charset=UTF-8; qs=0.09", "X-XSRF-TOKEN": c.slice(11)}});
    if (r.status === 401 || r.status === 403) return {ok: false, message: SIGN_IN};
    if (!r.ok) return {ok: false, message: "Onshape answered " + r.status + ": try again in a moment."};
    const j = await r.json();
    return {ok: true, name: j.name || j.email || ""};
  };
  let signedIn = false;
  const ensureSession = async () => {
    if (signedIn) return;
    const s = await OS.session();
    if (!s.ok) throw new Error(s.message);
    signedIn = true;
  };
  OS.api = async (method, path, body) => {
    await ensureSession();
    const r = await fetch("/api" + path, {
      method, credentials: "include", body: body === undefined ? undefined : JSON.stringify(body),
      headers: {"Content-Type": "application/json", Accept: "application/json;charset=UTF-8; qs=0.09",
                "X-XSRF-TOKEN": token()}});
    const text = await r.text();
    let j; try { j = JSON.parse(text); } catch (e) { j = text; }
    if (r.status === 401) { signedIn = false; throw new Error(SIGN_IN); }
    if (r.status >= 400) throw new Error(method + " " + path + " → " + r.status + ": " + String(text).slice(0, 300));
    return j;
  };
  OS.ps = (e) => { const i = ids(); return `/v6/partstudios/d/${i.d}/w/${i.w}/e/${e || i.e}`; };
  OS.info = async () => {
    const i = ids();
    const els = await OS.api("GET", `/v6/documents/d/${i.d}/w/${i.w}/elements`);
    const el = els.find(x => x.id === i.e) || {};
    return {...i, element: el.name, type: el.elementType, url: location.href.split("?")[0]};
  };

  // FeatureScript values (BTFSValue…) → plain JS
  OS.fs = (v) => {
    if (v === null || v === undefined || typeof v !== "object") return v;
    const t = String(v.btType || v.typeTag || "");
    if (/Undefined/.test(t)) return undefined;
    if (/Map/.test(t) && Array.isArray(v.value)) {
      const out = {};
      for (const entry of v.value) out[OS.fs(entry.key)] = OS.fs(entry.value);
      return out;
    }
    if (Array.isArray(v.value)) return v.value.map(OS.fs);
    if ("value" in v) return v.value;
    return v;
  };
  OS.eval = async (script, e) => {
    const r = await OS.api("POST", OS.ps(e) + "/featurescript?rollbackBarIndex=-1", {script});
    if (r.notices && r.notices.some(n => n.level === "ERROR")) throw new Error("FS: " + JSON.stringify(r.notices.map(n => n.message)).slice(0, 400));
    return OS.fs(r.result);
  };

  // ---------------------------------------------------------------- features
  OS.features = async (e) => OS.api("GET", OS.ps(e) + "/features");
  OS.addFeature = async (feature, e) => {
    const r = await OS.api("POST", OS.ps(e) + "/features", {btType: "BTFeatureDefinitionCall-1406", feature});
    const status = r.featureState && r.featureState.featureStatus;
    return {status, id: r.feature && r.feature.featureId, name: r.feature && r.feature.name};
  };
  OS.addFeatures = async (list, e) => {
    const done = [];
    for (const f of list) {
      const r = await OS.addFeature(f, e);
      done.push(r);
      if (r.status === "ERROR") break;   // stop: everything after it would build on a broken feature
    }
    return done;
  };
  OS.updateFeatures = async (features, e) => OS.api("POST", OS.ps(e) + "/features/updates",
    {btType: "BTUpdateFeaturesCall-1748", features});
  OS.deleteFeature = async (fid, e) => OS.api("DELETE", OS.ps(e) + "/features/featureid/" + fid);

  // ---------------------------------------------------------------- variables (Variable Studio)
  OS.variables = async (eid) => { const i = ids(); return OS.api("GET", `/variables/d/${i.d}/w/${i.w}/e/${eid}/variables`); };
  // Replaces the studio's list: pass every variable (name, type LENGTH/ANGLE/NUMBER/ANY, expression, description).
  OS.setVariables = async (eid, list) => {
    const i = ids();
    return OS.api("POST", `/variables/d/${i.d}/w/${i.w}/e/${eid}/variables`,
      list.map(v => ({...v, type: String(v.type || "LENGTH").toUpperCase()})));
  };

  // ---------------------------------------------------------------- parts, mounts
  // ("box" is reserved in FeatureScript, hence "bb")
  const PARTS_FS = `function(context is Context, queries) {
    var out = [];
    for (var b in evaluateQuery(context, qAllModifiableSolidBodies())) {
      const bb = evBox3d(context, {"topology": b, "tight": true});
      var mat = undefined;
      try silent { mat = getProperty(context, {"entity": b, "propertyType": PropertyType.MATERIAL}); }
      out = append(out, {"name": getProperty(context, {"entity": b, "propertyType": PropertyType.NAME}),
                         "material": mat == undefined ? "" : mat.name,
                         "min": [bb.minCorner[0] / meter, bb.minCorner[1] / meter, bb.minCorner[2] / meter],
                         "max": [bb.maxCorner[0] / meter, bb.maxCorner[1] / meter, bb.maxCorner[2] / meter]});
    }
    return out;
  }`;
  OS.parts = async (e) => OS.eval(PARTS_FS, e);
  OS.mounts = async (e) => {
    const list = await OS.features(e);
    const mounts = (list.features || []).filter(f => /^MOUNT\s*\|/.test(f.name || "") && !f.suppressed);
    const out = [];
    for (const f of mounts) {
      const cs = await OS.eval(`function(context is Context, queries) {
        const cs = evMateConnector(context, {"mateConnector": qCreatedBy(makeId("${f.featureId}"), EntityType.BODY)});
        return {"o": [cs.origin[0] / meter, cs.origin[1] / meter, cs.origin[2] / meter],
                "x": [cs.xAxis[0], cs.xAxis[1], cs.xAxis[2]], "z": [cs.zAxis[0], cs.zAxis[1], cs.zAxis[2]]};
      }`, e);
      out.push({name: f.name.trim(), origin: cs.o, x: cs.x, z: cs.z});
    }
    return out;
  };

  // ---------------------------------------------------------------- export
  OS.exportGLTF = async (e, resolution) => {
    const i = ids();
    const t = await OS.api("POST", OS.ps(e) + "/translations",
      {formatName: "GLTF", storeInDocument: false, yAxisIsUp: false, resolution: resolution || "fine"});
    for (let n = 0; n < 120; n++) {
      await new Promise(r => setTimeout(r, 1000));
      const s = await OS.api("GET", "/v6/translations/" + t.id);
      if (s.requestState === "DONE") {
        const r = await fetch(`/api/v6/documents/d/${i.d}/externaldata/${s.resultExternalDataIds[0]}`,
                              {credentials: "include", headers: {"X-XSRF-TOKEN": token()}});
        if (!r.ok) throw new Error("download " + r.status);
        return await r.blob();
      }
      if (s.requestState === "FAILED") throw new Error("translation failed: " + s.failureReason);
    }
    throw new Error("translation did not finish in 2 minutes");
  };
  OS.sidecar = async ({version, floor, e, parameters, variants, chat} = {}) => {
    const info = await OS.info();
    const parts = await OS.parts(e);
    const names = parts.map(p => p.name);
    const generic = names.filter(n => /^Part \d+$/.test(n));
    const dupes = names.filter((n, k) => names.indexOf(n) !== k);
    return {
      format: "ai-studio-cad/1", program: "onshape", version: version || "", element: info.element,
      source: info.url, up: "Z", floor: !!floor, exported: new Date().toISOString(),
      parts: parts.map(p => ({id: p.name, material: p.material || "", bbox: [p.min, p.max]})),
      mounts: await OS.mounts(e), parameters: parameters || {}, variants: variants || {},
      warnings: [...generic.map(n => "unnamed part " + n), ...dupes.map(n => "two parts named " + n)],
      ...(chat && (chat.name || chat.url || chat.session) ?
          {chat: {name: String(chat.name || ""), url: String(chat.url || ""), session: String(chat.session || "")}} : {}),
    };
  };
  OS.send = async ({port, token: blenderToken, folder}, name, body) => {
    const q = "name=" + encodeURIComponent(name) + (folder ? "&folder=" + encodeURIComponent(folder) : "");
    const r = await fetch(`http://127.0.0.1:${port}/cad?${q}`,
      {method: "POST", headers: {Authorization: "Bearer " + blenderToken}, body});
    const j = await r.json();
    if (!j.ok) throw new Error("Blender: " + j.error);
    return j.path;
  };
  // One call: GLB + sidecar into Blender's folder. Blender side: api.cad_import(<glb path>).
  OS.exportPackage = async ({port, token: blenderToken, name, version, folder, floor, e, parameters, variants, chat}) => {
    await ensureSession();
    const sidecar = await OS.sidecar({version, floor, e, parameters, variants, chat});
    const glb = await OS.exportGLTF(e);
    const target = {port, token: blenderToken, folder};
    const model = await OS.send(target, name + ".glb", glb);
    const side = await OS.send(target, name + ".cad.json",
      new Blob([JSON.stringify(sidecar, null, 1)], {type: "application/json"}));
    return {model, sidecar: side, parts: sidecar.parts.length, mounts: sidecar.mounts.map(m => m.name),
            warnings: sidecar.warnings};
  };
  return "OS helpers ready: " + Object.keys(OS).join(", ");
})();
