"""Tiny API client for the e2e run (ai_e2e, hermetic only). Cookie is handled by hand because the
session cookie is Secure and the local API is plain http."""
import json, httpx

BASE = "http://127.0.0.1:8094"
ORIGIN = "http://localhost:3009"
PASSWORD = "E2eDemo-2026!x"


class Api:
    def __init__(self, email="priya@meridian.example", base=BASE, origin=ORIGIN):
        self.base, self.origin, self.email = base, origin, email
        self.c = httpx.Client(base_url=base, timeout=120)
        self.cookie = None
        self.csrf = None

    def login(self, password=PASSWORD):
        r = self.c.post("/v1/auth/login", json={"email": self.email, "password": password},
                        headers={"Origin": self.origin})
        if r.status_code != 200:
            return r
        sc = r.headers.get("set-cookie", "")
        self.cookie = sc.split(";")[0]
        s = self.get("/v1/sso/session")
        self.csrf = s.json().get("csrf_token")
        return r

    def h(self, unsafe=False):
        h = {"Cookie": self.cookie or "", "Origin": self.origin}
        if unsafe and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return h

    def get(self, path, **kw):
        return self.c.get(path, headers=self.h(), **kw)

    def post(self, path, body=None, **kw):
        return self.c.post(path, json=body, headers=self.h(True), **kw)

    def delete(self, path, **kw):
        return self.c.delete(path, headers=self.h(True), **kw)


def show(r, n=600):
    print(r.status_code, r.text[:n])
