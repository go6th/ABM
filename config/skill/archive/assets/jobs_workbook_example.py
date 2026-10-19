# Example from the Alcon run (Sep 2026). Inputs: pl_jobs_12mo.json, a list of postings with keys
# t (title), d (description), f (first seen), l (last seen), status ("closed" or null), url, loc, sen;
# and jobs12_core.json, the hand classified platform roles [platform, class, title, loc, first, last, status, what, url].
# Writes {Account}_Job_Postings.xlsx: a Jobs sheet with one row per posting and a formula driven Summary sheet.
# Client facing: no tool names, no dashes as punctuation.
import json, re
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

ACCOUNT = "Alcon"
KW = {"Salesforce": r"salesforce|sfdc|service cloud|sales cloud|marketing cloud|\bcpq\b",
      "SAP": r"\bSAP\b|s/4 ?hana|s4hana|\bhana\b", "ServiceNow": r"servicenow", "Tableau": r"tableau",
      "Veeva": r"\bveeva\b|vault crm|promomats|\bmedcomms\b|vault clinical|vault rim|vault quality|vault safety|\bcrossix\b|veeva opendata"}
jobs = json.load(open("pl_jobs_12mo.json")); core = json.load(open("jobs12_core.json"))
coremap = {(c[2].lower()[:40], c[4]): (c[0], c[1], c[7]) for c in core}

def req(url): m = re.search(r"_(R-\d{4}-\d+)", url or ""); return m.group(1) if m else ""
def clean(x): return re.sub(r"\s*[–—]\s*", ", ", re.sub(r"\*\*|##|#", "", x or ""))
def ctx(txt, p):
    t = re.sub(r"\s+", " ", txt); out = []
    for m in re.finditer(p, t, re.I):
        s = t.rfind(".", 0, m.start()) + 1; e = t.find(".", m.end()); e = len(t) if e < 0 else e + 1
        c = t[s:e].strip()
        if c not in out: out.append(c)
        if len(out) >= 3: break
    return " | ".join(out)

rows = []
for j in jobs:
    txt = j["t"] + " " + j["d"]; plats = [k for k, p in KW.items() if re.search(p, txt, re.I)]
    if not plats: continue
    cm = coremap.get((j["t"].lower()[:40], j["f"])) or next((v for (t, d), v in coremap.items() if t == j["t"].lower()[:40]), None)
    cls = cm[1] if cm else "User role"; what = cm[2] if cm else ""
    rows.append([req(j["url"]), clean(j["t"]), ", ".join(plats), cls, "Open" if j["status"] != "closed" else "Closed",
                 j["f"], j["l"], clean(j["loc"] or ""), j.get("sen") or "", what,
                 clean(" || ".join(f"{k}: {ctx(txt, KW[k])}" for k in plats))[:3000], clean(j["d"])[:32000], j["url"]])
rows.sort(key=lambda r: r[5], reverse=True)

F = "Arial"; navy = PatternFill("solid", fgColor="1F3A5F"); thin = Side(style="thin", color="BFC5CC")
wb = Workbook(); ws = wb.active; ws.title = "Jobs"
hdr = ["Requisition", "Title", "Platforms named", "Class", "Status", "First seen", "Last seen", "Location", "Seniority",
       "What the posting says (read)", "Platform sentences", "Full description", "Link"]
ws.append(hdr)
for i, r in enumerate(rows, 1):
    ws.append(r[:12] + ["Open posting"]); c = ws.cell(row=i + 1, column=13); c.hyperlink = r[12]
    c.font = Font(name=F, size=10, color="1155CC", underline="single")
for c in ws[1]: c.font = Font(name=F, size=10, bold=True, color="FFFFFF"); c.fill = navy; c.alignment = Alignment(vertical="center", wrap_text=True)
for row in ws.iter_rows(min_row=2, max_row=len(rows) + 1):
    for c in row:
        if not c.hyperlink: c.font = Font(name=F, size=10)
        c.alignment = Alignment(vertical="top", wrap_text=(c.column in (2, 8, 10, 11))); c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
for col, w in zip("ABCDEFGHIJKLM", [14, 44, 18, 18, 9, 11, 11, 26, 11, 50, 70, 60, 13]): ws.column_dimensions[col].width = w
ws.freeze_panes = "C2"; last = len(rows) + 1; ws.auto_filter.ref = f"A1:M{last}"

s = wb.create_sheet("Summary", 0); T = "Jobs"
s["A1"] = f"{ACCOUNT} platform job postings, last 12 months"; s["A1"].font = Font(name=F, size=14, bold=True, color="1F3A5F")
notes = [f"Every {ACCOUNT} posting first seen in the last 12 months that names a platform, pulled by 6th agents. Open and closed postings are both included.",
         "Class: Build role means the platform is the job. Admin and owner role runs the platform or owns its data. Working tool means daily use. User role is everything else.",
         "What the posting says is filled only for roles read in full. Platform sentences shows the exact sentences where each platform is named. Full description is the complete posting."]
for k, n in enumerate(notes):
    c = s.cell(row=3 + k, column=1, value=n); c.font = Font(name=F, size=10); c.alignment = Alignment(wrap_text=True, vertical="top")
    s.merge_cells(start_row=3 + k, start_column=1, end_row=3 + k, end_column=6); s.row_dimensions[3 + k].height = 42
r0 = 8; s.cell(row=r0, column=1, value="Platform (named anywhere)")
for j, h in enumerate(["Total", "Closed", "Open", "Build role", "Admin and owner role", "Working tool"]): s.cell(row=r0, column=2 + j, value=h)
for k, pl in enumerate(KW):
    r = r0 + 1 + k; s.cell(row=r, column=1, value=pl)
    s.cell(row=r, column=2, value=f'=COUNTIF({T}!$C$2:$C${last},"*{pl}*")')
    s.cell(row=r, column=3, value=f'=COUNTIFS({T}!$C$2:$C${last},"*{pl}*",{T}!$E$2:$E${last},"Closed")')
    s.cell(row=r, column=4, value=f'=COUNTIFS({T}!$C$2:$C${last},"*{pl}*",{T}!$E$2:$E${last},"Open")')
    for j, cl in enumerate(["Platform build", "Platform owner", "Working tool"]):
        s.cell(row=r, column=5 + j, value=f'=COUNTIFS({T}!$C$2:$C${last},"*{pl}*",{T}!$D$2:$D${last},"{cl}")')
s.cell(row=r0 + 5, column=1, value="Distinct postings"); s.cell(row=r0 + 5, column=2, value=f"=COUNTA({T}!$B$2:$B${last})")
for row in s.iter_rows(min_row=r0, max_row=r0 + 5):
    for c in row:
        if c.value is None: continue
        c.font = Font(name=F, size=10); c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
for c in s[r0]:
    if c.value is not None: c.fill = navy; c.font = Font(name=F, size=10, bold=True, color="FFFFFF")
s.column_dimensions["A"].width = 30
for col in "BCDEFG": s.column_dimensions[col].width = 18
wb.save(f"{ACCOUNT}_Job_Postings.xlsx"); print("rows", len(rows))
