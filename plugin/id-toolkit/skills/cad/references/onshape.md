# Onshape through the built-in browser

Onshape has no connector. Work in the browser pane of the Claude desktop app with the user's own login and the
REST API of that session. `onshape-helpers.js` (next to this file) wraps the common calls as `window.OS`.

## Start
1. Open `https://cad.onshape.com/documents`. Signed out (no `XSRF-TOKEN` cookie, "Sign in" page) → ask the user
   to sign in in the browser pane. `OS.session()` says it clearly.
2. Run `onshape-helpers.js` with the JavaScript tool (again after every navigation). `OS.info()` gives document,
   workspace and element of the open tab.
3. JS pitfalls: a top-level `return` gives `undefined` (the last expression counts); scripts longer than ~45 s
   are cut off: split them.

## Two ways to build
- **A · Native features** (the user wants to keep editing): variables, sketches with dimensions on `#variables`,
  planes, extrudes, fillets; one folder per part in the feature tree.
- **B · FeatureScript custom feature**: a Feature Studio with dialog parameters. Good for complex, organic or
  derived geometry.

## API (all through `OS.api(method, path, body)`)
- XSRF token **raw**, not decoded (else 401). Read answers as text first.
- Features: `/v6/partstudios/d/{d}/w/{w}/e/{e}/features` (btType JSON). Unversioned `/api` gives 400.
- Feature Studios: unversioned `/featurestudios/d/{d}/w/{w}[/e/{fs}]`.
- New Part Studio: `POST /partstudios/d/{d}/w/{w}` with `{name}`. Copy: `POST /elements/copyelement/{d}/workspace/{w}`
  with `{documentIdSource, workspaceIdSource, elementIdSource, anotherDocument: false}`; rename via
  `/v6/metadata/d/{d}/w/{w}/e/{e}`.
- Parameter ids of standard features: `GET …/featurespecs`.

### Native features
- Insert with `OS.addFeatures([...])` (`{btType: 'BTFeatureDefinitionCall-1406', feature}`); status must be `OK`
  (check INFO; ERROR = wrong). Safest: clone an existing feature of the same type (drop `nodeId`/`featureId`).
- Parameters: string `BTMParameterString-149`, enum `BTMParameterEnum-145` (enumName, value), quantity
  `BTMParameterQuantity-147` (expression, e.g. `'#housing_length - 3 mm'`), bool `BTMParameterBoolean-144`,
  query list `BTMParameterQueryList-148`.
- Queries: `{btType: 'BTMIndividualQuery-138', queryString: 'query = qCreatedBy(makeId("<featureId>"), EntityType.FACE);'}`;
  standard planes `"Top"`, `"Front"`, `"Right"`; origin in sketches `deterministicIds: ['IB']`.
- Sketch plane axes: Top x=+X, y=+Y · Front x=+X, y=+Z (normal −Y) · Right x=+Y, y=+Z (normal +X).
- Variables: `assignVariable` (mode ASSIGNED, variableType LENGTH, ASCII names; `lengthValue` may only use variables
  defined before) or `OS.setVariables(eid, [...])` for a Variable Studio. Sizes in sketches are in meters.
- Extrude ADD/REMOVE: `defaultScope: false` + `booleanScope`; instead of `startOffset` use an offset plane.
  Fillets before cuts; a failing fillet: delete it right away.
- Part name/appearance: `POST /v6/metadata/d/{d}/w/{w}/e/{e}/p/{partId}`; property ids: name
  `57f3fb8efa3416c06701d60d`, appearance `…d60c`, description `…d60e`, exclude from BOM `…d61e`.

### FeatureScript
1. New Feature Studio: `POST /featurestudios/d/{d}/w/{w}` with `{name}`; take the header (`FeatureScript NNNN;` +
   import version) from a GET.
2. Write: `POST …/featurestudios/d/{d}/w/{w}/e/{fs}` with
   `{contents, serializationVersion, sourceMicroversion, rejectMicroversionSkew: false}`. Keep a local master copy;
   apply changes as exact replacements and compare length before writing.
3. Compiles? `GET …/featurespecs` (empty = error; messages under "FeatureScript notices").
4. Insert: `{btType: 'BTFeatureDefinitionCall-1406', feature: {btType: 'BTMFeature-134', featureType: '<exportName>',
   name, namespace: 'e{fs}::m{sourceMicroversion}', parameters: [...]}}`.
5. After a code change Onshape usually updates the features itself: read `…/features` first; only if not,
   delete and insert again with the same parameters.
6. Find regen errors: wrap the body in `try { } catch (e) { throw regenError("Step " ~ step ~ ": " ~ toString(e)); }`.
   Evaluate values with `OS.eval('function(context is Context, queries){ … }')` (`evDistance`, `evBox3d`).
- Pitfalls: `box` is reserved; ASCII only in `annotation`; unit `millimeter`; flat operation ids (`id + "lBox"`);
  `is array` instead of `is Vector` for arrays; intersect instead of subtract so the target keeps its identity;
  unions step by step with their own fillets; bodies that only touch do not unite (give 0.05 mm overlap).

## UI tasks (only when the API cannot)
- Check `tabs_context` first; a minimized Claude window blocks screenshots: ask the user to bring it to the front.
- Moving tabs into a folder: widen the window, read tab positions from `.os-tab-bar-tab`, drag onto the folder tab.
  The tab context menu has "Delete": never click it.
- Folders in the feature tree: select first and last row (Shift), folder icon, name, Enter. Never press Delete right
  after creating a folder (a hidden selection goes with it; undo with Cmd+Z).
- Finish: click empty 3D space, `P` hides planes, hide the origin and sketches, press `f`, screenshot.
