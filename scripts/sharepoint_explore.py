"""Read-only SharePoint exploration through Microsoft Graph (client-credentials app "Pace SharePoint Reader").

    .venv/bin/python scripts/sharepoint_explore.py            # token check + every site the app can see

Helpers for ad-hoc scripts: token(scope) and get(path_or_url, params) — see docs/11_sharepoint_exploration.md for
the map of what is where. Reads only (the app holds Graph Sites.Read.All); never prints the secret.
"""
import json, os, sys, time
import requests

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
def env():
    d = {}
    for line in open(ENV):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip('"').strip("'")
    return d
E = env()
CID, TID, SEC = E["PACE_SHAREPOINT_READER_APPLICATION_CLIENT_ID"], E["PACE_SHAREPOINT_READER_DIRECTORY_TENANT_ID"], E["PACE_SHAREPOINT_READER_CLIENT_SECRET_VALUE"]
_tok = {}
def token(scope="https://graph.microsoft.com/.default"):
    if scope in _tok and _tok[scope][1] > time.time() + 60:
        return _tok[scope][0]
    r = requests.post("https://login.microsoftonline.com/%s/oauth2/v2.0/token" % TID,
                      data={"client_id": CID, "client_secret": SEC, "grant_type": "client_credentials", "scope": scope}, timeout=30)
    if r.status_code != 200:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise SystemExit("token request failed: HTTP %s %s — %s" % (r.status_code, body.get("error"), (body.get("error_description") or "")[:300]))
    j = r.json()
    _tok[scope] = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
    return _tok[scope][0]
def get(url, params=None, scope="https://graph.microsoft.com/.default", raw=False):
    if url.startswith("/"):
        url = "https://graph.microsoft.com/v1.0" + url
    r = requests.get(url, params=params, headers={"Authorization": "Bearer " + token(scope), "Accept": "application/json"}, timeout=60)
    if raw:
        return r
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except Exception:
            err = {}
        return {"__error__": r.status_code, "code": err.get("code"), "message": (err.get("message") or "")[:300]}
    return r.json()

if __name__ == "__main__":
    import base64
    t = token()
    # decode the token's claims (no signature check) to see which application permissions were granted
    payload = t.split(".")[1]; payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    print("token OK · app roles granted:", claims.get("roles"), "· tenant:", claims.get("tid", "")[:8] + "…")
    root = get("/sites/root")
    print("root site:", {k: root.get(k) for k in ("displayName", "webUrl", "id")} if "__error__" not in root else root)
    sites = get("/sites", params={"search": "*"})
    if "__error__" in sites:
        print("site search:", sites)
    else:
        rows = sites.get("value", [])
        print("sites visible via search: %d" % len(rows))
        for s in rows[:60]:
            print("  -", s.get("displayName"), "|", s.get("webUrl"))
