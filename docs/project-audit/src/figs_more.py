"""Figures 7-12: connector call, deliverables, documents intake, Ask, ER, release."""
from svgkit import Diagram


def fig_connector():
    d = Diagram(1000, 330, "One paid or free data call")
    steps = [("validate", "1 validate\nreject bad input"), ("budget", "2 budget\ncap check"),
             ("lease", "3 lease\none caller per key"), ("breaker", "4 breaker\nstop hammering\na sick provider"),
             ("rate", "5 rate limit\nper-provider slots"), ("call", "6 call\nthe provider"),
             ("store", "7 store\nraw response to\nCloud Storage"), ("norm", "8 normalise\nuniform shape"),
             ("ledger", "9 ledger\nwrite cost + result")]
    x = 15
    for i, (k, t) in enumerate(steps):
        kind = "ext" if k == "call" else ("data" if k in ("store", "ledger") else "gate" if k in ("budget", "breaker", "rate") else "api")
        d.box(k, x, 40, 100, 78, t, kind, 10.5)
        if i:
            d.arrow(steps[i - 1][0], k, "r", "l")
        x += 108
    d.box("cache", 15, 190, 300, 70, "Cache check first (config/research_cache.yaml)\nweb 7 days, news/jobs 3, tech 14, org 30, people 14.\nA hit costs nothing.", "done", 11)
    d.box("replay", 345, 190, 300, 70, "Replay\nA finished call of an earlier slice or run is\nreturned from the ledger. Never paid twice.", "done", 11)
    d.box("ssrf", 675, 190, 310, 70, "SSRF shield (connectors/security.py)\nBlocks internal / private addresses\nbefore any fetch.", "gate", 11)
    d.arrow("cache", "validate", "t", "b", via=[(165, 150), (65, 150)])
    d.note(15, 300, "Providers are only ever named by code (p_search, p_scrape, p_signals, p_people, p_org) in client-visible places.", 10.5)
    return d.svg()


def fig_deliverables():
    d = Diagram(1000, 420, "Building the client files")
    d.box("facts", 20, 150, 150, 70, "Graded facts,\nrows, postings,\npeople (database)", "data", 11)
    d.box("snap", 205, 150, 150, 70, "One consistent\nsnapshot", "api", 11)
    d.box("read", 390, 150, 150, 70, "Readers\nrows to a render\nmodel", "api", 11)
    d.box("word", 590, 30, 170, 62, "Word x2\nAccount Summary,\nSources and Evidence", "worker", 11)
    d.box("xl", 590, 125, 170, 78, "Excel x3\nPeople, Job Postings,\nTech Stack and Teams", "worker", 11)
    d.box("zip", 590, 236, 170, 62, "Zip\nthe account workspace", "worker", 11)
    d.box("scan", 795, 100, 185, 70, "Leak scan\ndeny-list: tool, provider\nand person names", "gate", 11)
    d.box("ver", 795, 190, 185, 56, "Verify files\nagainst the snapshot", "gate", 11)
    d.box("gate", 795, 275, 185, 70, "Quality gate\n(documents/gate.py)\nfile-level hold-back", "gate", 11)
    d.box("store", 590, 340, 170, 56, "Store once per\nset hash (safe retry)", "data", 11)
    d.box("deliver", 795, 360, 185, 46, "Deliver passing files", "done", 11)
    d.arrow("facts", "snap"); d.arrow("snap", "read")
    d.line([(540, 185), (565, 185), (565, 61), (590, 61)])
    d.line([(540, 185), (565, 185), (565, 164), (590, 164)])
    d.line([(540, 185), (565, 185), (565, 267), (590, 267)])
    d.line([(760, 61), (780, 61), (780, 135), (795, 135)])
    d.line([(760, 164), (780, 164), (780, 135), (795, 135)], arrow=False)
    d.line([(760, 267), (780, 267), (780, 135), (795, 135)], arrow=False)
    d.arrow("scan", "ver", "b", "t"); d.arrow("ver", "gate", "b", "t")
    d.arrow("gate", "deliver", "b", "t")
    d.arrow("gate", "store", "l", "r", dashed=True, via=[(775, 310), (775, 368)])
    d.note(20, 300, "Screen tables (web) are built from the same row sources\nas the files, so screen and download cannot disagree.", 11)
    d.note(20, 360, "Client files never name tools or providers; they say \"6th agents\".", 11)
    return d.svg()


def fig_request():
    d = Diagram(1000, 430, "From a click to a database row")
    d.box("br", 20, 30, 130, 60, "Browser\ncookie\n__Host-ai_session", "user", 11.5)
    d.box("edge", 180, 30, 150, 60, "Load balancer\nCloud Armor: OWASP,\nrate limits", "plain", 11.5)
    d.box("web", 360, 30, 190, 60, "web (Next.js BFF)\nsame-origin check, CSP nonce,\nheader + cookie allow-list", "web", 11.5)
    d.box("mw", 580, 30, 190, 60, "api middleware\nrequest id, log line,\nsecurity headers", "api", 11.5)
    d.box("rg", 800, 30, 180, 60, "route_guard\ncatalogue lookup:\nno entry means 403", "gate", 11.5)
    d.arrow("br", "edge"); d.arrow("edge", "web"); d.arrow("web", "mw"); d.arrow("mw", "rg")
    d.box("az", 20, 170, 300, 100, "authorize()\n1 find tenant from session hash (definer function)\n2 role must be in catalogue set\n3 membership re-check (60 s cache)\n4 CSRF token on unsafe methods\n5 MFA step-up where required\n6 write read-audit event first", "gate", 10.8)
    d.box("h", 360, 170, 190, 100, "Handler\nbusiness logic\nnever receives a tenant\nid from the request", "api", 11.5)
    d.box("tx", 580, 170, 200, 100, "tenant_txn(tenant)\nBEGIN; set_config\napp.tenant_id, app.user_id,\napp.scope (this txn only)", "api", 11.5)
    d.box("pg", 810, 170, 170, 100, "PostgreSQL\nFORCE ROW LEVEL\nSECURITY; role app_rw\nno bypass", "data", 11.5)
    d.line([(890, 90), (890, 130), (170, 130), (170, 170)])
    d.arrow("az", "h"); d.arrow("h", "tx"); d.arrow("tx", "pg")
    d.zone(20, 310, 960, 100, "Why a leak between customers is hard")
    d.note(36, 352, "The tenant comes from the verified session, never from the request. The setting lives only inside one transaction, so a pooled connection\ncannot carry customer A's id into customer B. If the id is missing the policies match zero rows. Cross-tenant jobs use named SECURITY DEFINER\nfunctions that return ids only.", 11)
    return d.svg()


def fig_ask():
    d = Diagram(1000, 470, "How Ask answers")
    d.box("q", 20, 30, 130, 60, "Question\nPOST /v1/chat/ask\n(streamed SSE)", "web", 10.5)
    d.box("ws", 180, 30, 150, 60, "Load workspace\nfacts, jobs, people,\ntech, files, mail", "api", 10.5)
    d.box("rt", 360, 30, 150, 60, "Route\ntabular / mixed / prose\n(code rules, else Haiku)", "ai", 10.5)
    d.box("pre", 540, 30, 160, 60, "Pre-read\nfile map, whole file in\ncached block, history", "api", 10.5)
    d.box("llm", 730, 30, 250, 60, "Model with tools\nlist_sources, describe_table, query_table,\ncount_tiers, passages, archive, web, python", "ai", 10.5)
    d.arrow("q", "ws"); d.arrow("ws", "rt"); d.arrow("rt", "pre"); d.arrow("pre", "llm")
    d.box("rend", 730, 160, 250, 60, "render_answer (one strict JSON call)\nat least one table; code expands\nstored query results into full tables", "ai", 10.5)
    d.arrow("llm", "rend", "b", "t")
    d.box("cit", 450, 160, 240, 60, "Check citations\n[R#] must be rows that were sent\n[K] own knowledge  [W#] web", "gate", 10.5)
    d.arrow("rend", "cit", "l", "r")
    d.box("scr", 160, 160, 250, 60, "Scrub client output\nno tool, provider or person\nnames; whole answer first", "gate", 10.5)
    d.arrow("cit", "scr", "l", "r")
    d.box("ok", 20, 160, 110, 60, "Stream to\nbrowser", "done", 10.5)
    d.arrow("scr", "ok", "l", "r")
    d.box("loc", 450, 290, 240, 70, "Bad or uncited reply?\nLocal composer writes the answer\nfrom the rows (no model)\nwith a visible notice", "plain", 10.5)
    d.arrow("cit", "loc", "b", "t", dashed=True)
    d.box("rec", 20, 290, 380, 70, "Record the turn\nchat_turns: tokens, cost (incl. web lookups),\nchat count taken after a good answer", "data", 10.5)
    d.zone(20, 390, 960, 66, "Limits", dash=True)
    d.note(36, 430, "Up to 6 tool rounds. Web: 3 searches and 3 reads per turn, only when ASK_WEB is on and the search key is present. Memory = last 6 turns of this thread only.", 11)
    return d.svg()


def fig_documents():
    d = Diagram(1000, 400, "Uploading a file")
    steps = [("a", "1 Upload\nresumable, straight\nto quarantine bucket", "web"),
             ("b", "2 Complete\nsize must match,\nqueue documents task", "api"),
             ("c", "3 Detect type\nfrom the bytes,\nnot the file name", "gate"),
             ("d", "4 Malware scan\nclamd sidecar;\nany error = rejected", "gate"),
             ("e", "5 Hash + move\none raw copy per\ntenant and hash", "data")]
    x = 20
    for i, (k, t, kind) in enumerate(steps):
        d.box(k, x, 30, 175, 78, t, kind, 10.5)
        if i:
            d.arrow(steps[i - 1][0], k)
        x += 195
    s2 = [("f", "6 Plan parts\nin a sandbox process", "worker"),
          ("g", "7 Parse each part\nin its own child process\nno network, no writes", "worker"),
          ("h", "8 Redact PII, chunk\nprose passages;\none row per sheet row", "worker"),
          ("i", "9 Bind to account\nor wait for you to\nconfirm the company", "api"),
          ("j", "10 Embed + index\nbge-small on the server,\nprivate chunks table", "data")]
    x = 20
    for i, (k, t, kind) in enumerate(s2):
        d.box(k, x, 180, 175, 78, t, kind, 10.5)
        if i:
            d.arrow(s2[i - 1][0], k)
        x += 195
    d.line([(107, 108), (107, 144), (107, 144), (107, 180)])
    d.box("fm", 20, 300, 450, 70, "File-mode research (POST /v1/runs, input_mode=file)\nsetup, extract, tracker, internal_file, gate, deliver, finish\nProduces only the Internal Only Word file", "ai", 10.5)
    d.box("ask", 500, 300, 480, 70, "Ask reads the same stored tables and passages\nspreadsheets become queryable tables (describe_table / query_table)", "ai", 10.5)
    d.arrow("j", "fm", "b", "t", via=[(882, 280), (245, 280)])
    return d.svg()


def fig_er():
    d = Diagram(1000, 640, "Database map")
    d.zone(20, 20, 300, 150, "Identity and tenants")
    d.note(36, 62, "tenants, users, memberships\nauth_sessions, idp_connections\nlocal_identities, support_grants\nfeature_flags, client_settings", 14.5)
    d.zone(350, 20, 300, 150, "Accounts and research")
    d.note(366, 62, "accounts (target companies)\nresearch_jobs (the request)\naccount_runs (the live run)\nrun_leases, run_cap_*, pull_manifests", 14.5)
    d.zone(680, 20, 300, 150, "Evidence")
    d.note(696, 62, "facts, fact_evidence, grade_events\nsources, coverage_rows (33-row\nchecklist), lead_verifications\nabm_sense_checks, abm_distill_outputs", 14.5)
    d.zone(20, 200, 300, 150, "Pipelines")
    d.note(36, 242, "job_postings (+ classifications)\npeople, person_profiles, posts\ntech_detections, tech_stack_items\nlink_archive", 14.5)
    d.zone(350, 200, 300, 150, "Outputs")
    d.note(366, 242, "deliverables, export_packages\nexport_verifications, view_rows\nrun_gate_results, leak_checks\naccount_bundles, download_jobs", 14.5)
    d.zone(680, 200, 300, 150, "Money")
    d.note(696, 242, "tool_calls (insert-only ledger)\ncredit_entries (credit ledger)\nmodel_reply_log (reply cache)\nusage_events, plan_limits", 14.5)
    d.zone(20, 380, 300, 150, "Files and search")
    d.note(36, 422, "documents, document_parts\ndocument_passages (tsvector)\ndocument_tables / _rows\nchunks (pgvector, 16 partitions)", 14.5)
    d.zone(350, 380, 300, 150, "Ask / chat")
    d.note(366, 422, "chat_threads\nchat_turns (answer, tokens, cost)\ntenant_model_credentials (KMS)\nuser_preferences", 14.5)
    d.zone(680, 380, 300, 150, "Schedules, audit, ops")
    d.note(696, 422, "audit_events (hash chain)\nnotifications, notification_outbox\nrefresh_*, alerts (unwired)\nisolation_checks, eval_*", 14.5)
    d.zone(20, 550, 960, 80, "Rules that hold for all of them", dash=False)
    d.note(36, 594, "Every tenant table: tenant_id + FORCE ROW LEVEL SECURITY. 102 tables are also private per user. Ledgers are insert-only.\n~205 tables, 96 migrations (head 0097), only 5 ORM models; everything else is hand-written SQL.", 11)
    d.line([(320, 95), (350, 95)])
    d.line([(650, 95), (680, 95)])
    d.line([(500, 170), (500, 200)])
    d.line([(830, 170), (830, 200)], arrow=True)
    d.line([(170, 170), (170, 200)], arrow=False, dashed=True)
    return d.svg()


def fig_models():
    d = Diagram(1000, 520, "Who calls which model")
    d.zone(20, 20, 300, 480, "Callers", dash=False)
    d.box("a", 40, 55, 260, 62, "Research agents\nsetup, pass1, pass2, people,\nsense_check, distill", "ai", 10.5)
    d.box("b", 40, 135, 260, 50, "Job / person classifiers\n(posting, person findings)", "ai", 10.5)
    d.box("c", 40, 203, 260, 50, "Judges\nsupport judge, posting 2nd pass", "gate", 10.5)
    d.box("e", 40, 271, 260, 50, "Uploaded-file extraction", "ai", 10.5)
    d.box("f", 40, 339, 260, 62, "Ask / chat\nuser's chosen model + tools", "web", 10.5)
    d.box("g", 40, 419, 260, 50, "Ask route classifier", "web", 10.5)
    d.zone(350, 20, 290, 480, "Our code in the middle", dash=False)
    d.box("rd", 370, 70, 250, 80, "RunDeps.model\nin-run replay, 7-day reply cache,\ntoken guard, $ cap, cost log\n(guards off when caps are off)", "api", 10.5)
    d.box("ch", 370, 190, 250, 70, "ChoiceLlm / platform/llm.py\nprompt caching, warm gate,\nstructured output, batches", "api", 10.5)
    d.box("cc", 370, 339, 250, 62, "chat/service.py\nprovider per model, tools loop", "api", 10.5)
    d.box("rt", 370, 420, 250, 62, "llm/ Router (dormant)\nonly if MODEL_ROUTER=on;\nno benchmark report yet", "plain", 10.5)
    d.zone(670, 20, 310, 480, "Models", dash=False)
    d.box("op", 690, 55, 270, 62, "Claude Opus 5.5 ($4 / $20 per MTok)\nplanning, extraction, classify,\nsense check, distill", "ai", 10.5)
    d.box("fa", 690, 135, 270, 62, "Claude Fable 5.1 ($10 / $50)\njudging and verification", "ai", 10.5)
    d.box("so", 690, 215, 270, 62, "Claude Sonnet 5.5 ($2 / $10)\ndefault chat model; Ask option", "ai", 10.5)
    d.box("ha", 690, 295, 270, 50, "Claude Haiku 5.5 ($0.10 / $0.50)\nAsk route classifier", "ai", 10.5)
    d.box("ki", 690, 363, 270, 50, "Kimi K3 (Moonshot or Ollama Cloud)\noptional, user-selectable", "ext", 10.5)
    d.box("gr", 690, 431, 270, 50, "Grok 4.5 (xAI)\noptional, user-selectable", "ext", 10.5)
    d.arrow("a", "rd", "r", "l"); d.arrow("b", "rd", "r", "l", via=[(330, 160), (330, 110)])
    d.arrow("c", "rd", "r", "l", via=[(335, 228), (335, 110)]); d.arrow("e", "rd", "r", "l", via=[(340, 296), (340, 110)])
    d.arrow("rd", "ch", "b", "t")
    d.arrow("f", "cc", "r", "l"); d.arrow("g", "cc", "r", "l", via=[(330, 444), (330, 370)])
    d.line([(620, 225), (690, 86)]); d.line([(620, 225), (690, 166)])
    d.line([(620, 370), (690, 246)]); d.line([(620, 370), (690, 320)]); d.line([(620, 370), (690, 388)]); d.line([(620, 370), (690, 456)])
    return d.svg()


def fig_release():
    d = Diagram(1000, 330, "How a change reaches the cloud (as practised)")
    steps = [("c1", "1 Edit code\nlocal repo,\nmake check", "user"), ("c2", "2 Cloud Build\nbuilds api, worker\nand web images", "worker"),
             ("c3", "3 release-check\nTrivy scan,\nimage inspection", "gate"), ("c4", "4 Migrate job\nalembic upgrade head\n(runs first)", "data"),
             ("c5", "5 Deploy\nworker, then api,\nthen web (by digest)", "api"), ("c6", "6 Smoke test\n/readyz must see\nrevision 0097", "gate")]
    x = 20
    for i, (k, t, kind) in enumerate(steps):
        d.box(k, x, 40, 140, 82, t, kind, 11.5)
        if i:
            d.arrow(steps[i - 1][0], k)
        x += 160
    d.box("rb", 340, 170, 300, 56, "Rollback = send traffic to the previous\nCloud Run revision", "plain", 11.5)
    d.arrow("c6", "rb", "b", "t", dashed=True, via=[(890, 150), (490, 150)])
    d.box("gh", 20, 170, 290, 70, "GitHub Actions (ci, deploy-staging, deploy-prod)\nexist and gate quality; whether the deploy\nworkflow ever ran is unverified", "plain", 11.5)
    d.box("tf", 670, 170, 310, 70, "Terraform (infra/) applied by hand\nplan -out, then apply: never an old plan\n(one old plan once rolled the api back)", "plain", 11.5)
    d.note(20, 290, "Staging is live (project abmgenerator, us-central1). Production is prepared in Terraform but not applied.", 11)
    return d.svg()


def fig_web():
    d = Diagram(1000, 560, "The website map")
    d.zone(20, 20, 220, 520, "Public", dash=False)
    d.note(36, 62, "/login\n/forgot\n/reset\n/invite\n/verify-email\n/privacy\n/terms\n/operator/login", 14)
    d.zone(260, 20, 460, 520, "Customer workspace (layout checks the session)", dash=False)
    d.box("ask", 280, 55, 200, 50, "/   Ask (home)", "web", 12.5)
    d.box("acc", 280, 120, 200, 50, "/accounts, /accounts/add", "web", 12.5)
    d.box("ws", 280, 185, 200, 50, "/accounts/[domain]\naccount workspace", "web", 12.5)
    d.box("sh", 500, 185, 200, 50, "/accounts/[d]/[sheet]\ndeep-dive tables", "web", 12)
    d.box("rb", 280, 250, 200, 50, "/research  (board, live)", "web", 12.5)
    d.box("rn", 500, 250, 200, 50, "/research/new\ncompany or file", "web", 12)
    d.box("rr", 280, 315, 200, 50, "/runs/[run_id]\none run's steps", "web", 12)
    d.box("ar", 500, 315, 200, 50, "/accounts/[d]/archive,\n/files, /refresh", "web", 12)
    d.box("sc", 280, 380, 200, 50, "/schedules", "web", 12.5)
    d.box("md", 500, 380, 200, 50, "/mail-and-drive,\n/model-preferences", "web", 12)
    d.box("tm", 280, 445, 200, 50, "/team, /support-access\n(admin only)", "web", 12)
    d.box("ch", 500, 445, 200, 50, "/chats", "web", 12.5)
    d.note(280, 520, "14 old routes (/alerts, /settings/*, /runs ...) only redirect to /", 10.5)
    d.zone(740, 20, 240, 360, "Staff console /operator", dash=False)
    d.note(756, 62, "/operator (overview)\n/operator/clients\n/operator/clients/[id]\n/operator/users\n/operator/library\n/operator/runs\n/operator/defaults\n/operator/view", 14)
    d.box("bff", 740, 410, 240, 120, "Same-origin proxies\n/v1/*  to the API (customers)\n/api/operator/v1/*  (staff)\nstreams SSE; adds cookies, CSRF", "api", 12.5)
    return d.svg()


def fig_timeline():
    import re
    days = "2026-10-01:27 2026-10-02:67 2026-10-03:26 2026-10-04:23 2026-10-05:4 2026-10-06:31 2026-10-07:93 2026-10-08:39 2026-10-09:24 2026-10-10:1 2026-10-11:18 2026-10-12:2 2026-10-13:4 2026-10-14:112 2026-10-15:32 2026-10-16:22 2026-10-17:12 2026-10-18:0 2026-10-19:0 2026-10-20:6 2026-10-21:12 2026-10-22:2 2026-10-23:2 2026-10-24:0 2026-10-25:0 2026-10-26:0 2026-10-27:4 2026-10-28:4 2026-10-29:1 2026-10-30:1 2026-10-31:5 2026-11-01:0 2026-11-02:5 2026-11-03:1".split()
    d = Diagram(1000, 330, "Commits per day")
    left, base, w, h = 50, 250, 920, 190
    d.line([(left, base), (left + w, base)], arrow=False)
    bw = w / len(days)
    for i, s in enumerate(days):
        day, n = s.split(":"); n = int(n)
        bh = n / 112 * h
        x = left + i * bw
        d.parts.append(f'<rect x="{x + 2:.1f}" y="{base - bh:.1f}" width="{bw - 4:.1f}" height="{bh:.1f}" fill="#4a7fd4" rx="2"/>')
        if n >= 20:
            d.note(x + bw / 2, base - bh - 4, str(n), 9.5, anchor="middle")
        if i % 3 == 0:
            d.note(x + bw / 2, base + 14, day[5:], 9.5, anchor="middle")
    d.note(50, 20, "580 commits in 34 days, one committer. Busiest: 14 Oct (112), 7 Oct (93), 2 Oct (67).", 11.5, bold=True)
    for x0, x1, t in ((0, 4, "Oct 1-4: scaffold, docs,\nproviders, grading"), (5, 9, "Oct 6-9: spend, DB views,\nweb shell, chat UI"), (13, 16, "Oct 14-17: replica\nproduct, RLS, uploads"), (19, 33, "Oct 20 - Nov 3: fixes, speed, Ask, repair stage, purge")):
        xs = left + x0 * bw; xe = left + (x1 + 1) * bw
        d.parts.append(f'<rect x="{xs:.1f}" y="285" width="{xe - xs:.1f}" height="2" fill="#94a3b8"/>')
        for j, ln in enumerate(t.split("\n")):
            d.note(xs, 300 + j * 12, ln, 9.5, color="#475569")
    return d.svg()
