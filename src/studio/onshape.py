# SPDX-License-Identifier: GPL-3.0-or-later
"""Onshape from Blender: CAD feedback becomes a comment in the Onshape document it came from.

The sidecar of a CAD export names its Onshape element ("source": https://cad.onshape.com/
documents/<d>/w/<w>/e/<e>, also company domains). With Onshape API keys (created once at
https://dev-portal.onshape.com/keys, kept in the macOS keychain, never in the preferences)
the add-on
- posts every new feedback point as a comment on that element (with a check image if there is
  one): the CAD side sees it in Onshape and gets a notification,
- reads the comments back: a comment resolved in Onshape ticks its point off in CAD-Feedback.md.
Basic authentication with the keys (key as user, secret as password), as Onshape's API allows.
No bpy: the caller passes paths and URLs.
"""

import base64
import json
import mimetypes
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid

SERVICE = "AI Studio Onshape API"
API = "/api/v6"
# objectType of a comment on a document element (Onshape's comment API: 0 = document).
DOCUMENT_COMMENT = 0
TIMEOUT = 20
_SOURCE = re.compile(r"^(https://[\w.-]+\.onshape\.com|http://127\.0\.0\.1:\d+)/documents/(\w+)/(w|v)/(\w+)/e/(\w+)")


# ---------------------------------------------------------------- keys (macOS keychain)

def _security(*args):
    return subprocess.run(["/usr/bin/security"] + list(args), capture_output=True, text=True, timeout=20)


def keys():
    """(access key, secret key) from the keychain, or None."""
    try:
        found = _security("find-generic-password", "-s", SERVICE, "-g")
    except (OSError, subprocess.SubprocessError):
        return None
    if found.returncode != 0:
        return None
    account = re.search(r'"acct"<blob>="([^"]*)"', found.stdout)
    secret = re.search(r'^password: "(.*)"$', found.stderr, re.M)
    if not account or not secret:
        return None
    return account.group(1), secret.group(1)


def save_keys(access, secret):
    """Keep the keys in the login keychain (replacing earlier ones)."""
    access, secret = access.strip(), secret.strip()
    if not access or not secret:
        raise ValueError("Both keys are needed: access key and secret key")
    _security("delete-generic-password", "-s", SERVICE)
    result = _security("add-generic-password", "-s", SERVICE, "-a", access, "-w", secret, "-U")
    if result.returncode != 0:
        raise RuntimeError("Keychain: " + (result.stderr or result.stdout).strip()[:200])


def forget_keys():
    _security("delete-generic-password", "-s", SERVICE)


# ---------------------------------------------------------------- the API

def parse_source(url):
    """{"base", "did", "wvm", "wid", "eid"} of an Onshape element URL, or None."""
    match = _SOURCE.match(str(url or ""))
    if not match:
        return None
    base, did, wvm, wid, eid = match.groups()
    return {"base": base, "did": did, "wvm": wvm, "wid": wid, "eid": eid}


class OnshapeError(RuntimeError):
    pass


def request(base, method, path, body=None, credentials=None, content_type="application/json", raw=False):
    credentials = credentials or keys()
    if not credentials:
        raise OnshapeError("No Onshape API keys: add them in Preferences › Studio › Onshape")
    token = base64.b64encode(("%s:%s" % credentials).encode("utf-8")).decode("ascii")
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Basic " + token, "Accept": "application/json;charset=UTF-8; qs=0.09",
               "User-Agent": "Blender-AI-Studio"}
    if data is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            text = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        if exc.code in (401, 403):
            raise OnshapeError("Onshape refused the keys (%d): check them in Preferences › Studio › Onshape, "
                               "they need read and write access to the document" % exc.code)
        raise OnshapeError("Onshape answered %d: %s" % (exc.code, detail))
    except (urllib.error.URLError, OSError) as exc:
        raise OnshapeError("Onshape does not answer: %s" % exc)
    if raw:
        return text
    return json.loads(text.decode("utf-8")) if text else {}


def whoami(base="https://cad.onshape.com", credentials=None):
    """The name of the Onshape user the keys belong to (to test them)."""
    info = request(base, "GET", "/users/sessioninfo", credentials=credentials)
    return info.get("name") or info.get("email") or "?"


KEYS_REFUSED = ("Onshape does not accept the saved API keys (deleted or expired?): add new ones in "
                "Preferences › Studio › Onshape.")
OFFLINE = ("Onshape cannot be reached right now (offline?). The comments stay in CAD-Feedback.md and go to "
           "Onshape at the next sync.")


def check(base="https://cad.onshape.com", credentials=None):
    """(ok, sentence) for the user: do the saved keys work? Never raises."""
    if not (credentials or keys()):
        return False, "No Onshape API keys yet: Preferences › Studio › Onshape › Add Keys…"
    try:
        name = whoami(base, credentials)
    except OnshapeError as exc:
        text = str(exc)
        if "refused the keys" in text:
            return False, KEYS_REFUSED
        if "does not answer" in text:
            return False, OFFLINE
        return False, "Onshape: " + text
    return True, "Onshape keys work (%s)" % name


def post_comment(source, message, image=None, credentials=None):
    """A comment on the source's element (+ the image as an attachment). Returns the comment id."""
    where = parse_source(source)
    if where is None:
        raise OnshapeError("Not an Onshape element: %s" % source)
    if where["wvm"] != "w":
        raise OnshapeError("The export came from a version of the document: comments go to a workspace")
    body = {"documentId": where["did"], "workspaceId": where["wid"], "elementId": where["eid"],
            "objectId": where["did"], "objectType": DOCUMENT_COMMENT, "message": message}
    comment = request(where["base"], "POST", "/comments", body, credentials=credentials)
    cid = comment.get("id")
    if not cid:
        raise OnshapeError("Onshape made no comment: %s" % json.dumps(comment)[:200])
    if image:
        attach(where["base"], cid, image, credentials=credentials)
    return cid


def attach(base, cid, path, credentials=None):
    boundary = uuid.uuid4().hex
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    with open(path, "rb") as f:
        content = f.read()
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
             % (boundary, name, kind)).encode("utf-8") + content + ("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return request(base, "POST", "/comments/%s/attachment" % urllib.parse.quote(cid), body, credentials=credentials,
                   content_type="multipart/form-data; boundary=" + boundary)


def comment_resolved(base, cid, credentials=None):
    """True when the comment was resolved in Onshape (None when it is gone)."""
    try:
        comment = request(base, "GET", "/comments/%s" % urllib.parse.quote(cid), credentials=credentials)
    except OnshapeError as exc:
        if " 404" in str(exc):
            return None
        raise
    resolved, reopened = comment.get("resolvedAt") or "", comment.get("reopenedAt") or ""
    return bool(resolved) and reopened < resolved   # ISO times: a later reopen counts


def resolve_comment(base, cid, credentials=None):
    """Mark the comment resolved in Onshape (the point was done in Blender)."""
    return request(base, "POST", "/comments/%s/resolve" % urllib.parse.quote(cid), {}, credentials=credentials)
