# Example from the Alcon run (Sep 2026): merges BitScale, Sumble and research people into one
# deduplicated workbook, removes security, compliance and audit titles, assigns level, lane and
# priority from the title, and writes a formula driven Summary sheet. Adapt the inputs per account.
# Inputs it expects beside it: data.txt (tier|grid row|name|title|linkedin slug), ../people.json, ../people_bs.json

import re,json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
LI="https://www.linkedin.com/in/"
def lvl(t):
    x=t.lower()
    if re.search(r"\bsvp\b|chief ",x): return "1 C-level and SVP"
    if re.search(r"vice president|\bvp\b",x): return "2 VP"
    if re.search(r"senior director|sr\.? director",x): return "3 Senior Director"
    if re.search(r"associate director",x): return "5 Associate Director"
    if re.search(r"director",x): return "4 Director"
    if re.search(r"\bhead\b",x): return "6 Head"
    if re.search(r"senior manager|sr\.? manager|senior it manager|senior field service manager",x): return "7 Senior Manager"
    if re.search(r"manager",x): return "8 Manager"
    return "9 Lead, owner, specialist or developer"
def lane(t):
    s=t.lower()
    if re.search(r"salesforce|commercial it|commercial and corporate it|sales technology|customer engagement|customer master data|sales enablement|crm",s): return "Salesforce and commercial"
    if re.search(r"servicenow|itsm|esm\b|enterprise service management|it process owner|hr information|software asset",s): return "ServiceNow"
    if re.search(r"\bveeva\b|\bvault\b|commercial systems|commercial excellence|medical affairs systems|regulatory systems|quality systems|clinical systems",s): return "Veeva and life sciences systems"
    if re.search(r"field service|service operations|tech service|technical service|equipment services|customer operations|intelligent services|avs commercial",s): return "Field service and customer operations"
    if re.search(r"\bsap\b|erp|master data|material master|order management|enterprise software|supply chain|otc",s): return "SAP and ERP"
    if re.search(r"analytics|insights|visualization|data product|data engineering|bi lead|data and ai|enterprise ai|dashboards",s): return "Analytics and data"
    if re.search(r"business technology|digital|vision suite",s): return "Business technology and digital"
    return "IT leadership and other"
SEC=r"security officer|data security|it compliance|audit"
A_PAT=r"salesforce|servicenow|itsm|esm \(|chief information|chief data|enterprise software|business technology and platforms|business technology & platforms|digital strategy and operations|us surgical equipment services|customer master data|commercial it|commercial and corporate it|global erp operations|erp commercial order|technical services and equipment financing|field service operations|us service operations|customer operations|alcon vision suite|us surgical business technology|analytics & automation head|analytics, insights|global head insights|sales technology|head of it, alcon international|integration management|erp transformation|enterprise service management|enterprise ai|intelligent services|alcon global services|transformation projects|field service, commercial"
C_PAT=r"physicochemical|r&d analytics|people analytics|field service engineer$|principal field technical|^director, it$|^head of information technology$|legal technology|market research|vision care$|of sales|developer|devops engineer"
def prio(t,level):
    s=t.lower()
    if re.search(C_PAT,s): return "C"
    if re.search(A_PAT,s): return "A"
    return "B"
people={}  # key -> dict
def add(name,title,li,src,sumble="",note=""):
    key=(li.lower().rstrip("/") if li else "name:"+re.sub(r"[^a-z]","",name.lower()))
    # name based second key to catch same person via different source
    nk=re.sub(r"[^a-z]","",re.sub(r",.*","",name).lower())
    for k,v in people.items():
        if k==key or v["nk"]==nk:
            v["src"].add(src); 
            if sumble and not v["sumble"]: v["sumble"]=sumble
            if li and not v["li"]: v["li"]=li
            return
    people[key]=dict(name=name,title=title,li=li,src={src},sumble=sumble,note=note,nk=nk)
# 1. BitScale senior list
for l in open("data.txt",encoding="utf-8"):
    tier,rid,name,title,slug=l.rstrip("\n").split("|")
    add(name,title,LI+slug,"BitScale senior grid")
# 2. Sumble
for g,rows in json.load(open("../people.json")).items():
    for r in rows: add(r[0],r[1],r[5],"Sumble",sumble=r[4])
# 3. BitScale first grid
for g,rows in json.load(open("../people_bs.json")).items():
    for r in rows: add(r[0],r[1],r[3],"BitScale first grid")
# 4. research only
for n,t,src in [("Udayakanth Kuppa","Director of Digital ERP Transformation","Research, S9"),("Ashish Sonone","Senior Director, Enterprise Service Management and PMO","Research, S10"),
  ("Sashank Musti","Global Head, Enterprise AI and Technology Products, Executive Director","Research, S16"),("Gopal Sharma","Global Leader, Global Shared Services, Operations and Set up, and Head of Alcon Global Services","Research, S32"),
  ("Sara Kelly","VP, Digital Health","Research, S27"),("Aldo Motta","Global Director of Intelligent Services, as of January 2024","Research, S24"),("Ross Bergin","Global Director, AVS Commercial Solutions, as of January 2024","Research, S24"),
  ("Moises Cora","Global Associate Director of Digital Integrations and Solutions, as of January 2024","Research, S24"),("Troop Aschenbeck","Global Director of Supply Chain, as of August 2023","Research, S25"),
  ("Raja C.","Head of Digital Technology, Digital Health","Research, S21")]:
    add(n,t,"http://linkedin.com/in/raja-c-2136589" if n=="Raja C." else "",src)
# manual merges / notes
def find(n): 
    return next(v for v in people.values() if v["name"].startswith(n))
ws_dupes=[]
for k in list(people):
    v=people[k]
    if v["name"]=="Konrad W.": 
        find("Konrad Weickhardt")["note"]="Second profile: "+v["li"]; find("Konrad Weickhardt")["src"].add("BitScale senior grid"); del people[k]
for k in list(people):
    v=people[k]
    if v["name"]=="William Arnold" and "909b779b" in v["li"]:
        find("William Arnold")["note"]="Second profile: "+v["li"]; del people[k]
removed=[v for v in people.values() if re.search(SEC,v["title"].lower())]
people={k:v for k,v in people.items() if not re.search(SEC,v["title"].lower())}
find("Casey Davis")["note"]="Title includes security, but the role is development for enterprise applications. Kept. Remove if not wanted."
find("Magdalena Gradziel")["note"]="Runs a named program, Customer Master Data Transformation."
find("Dilip Kumar")["note"]="Title confirms Salesforce Marketing Cloud is in use."
find("Marc Raman")["note"]="Title reported by two tools, still self reported."
print("removed security:",[v["name"] for v in removed])
rows=[]
for v in people.values():
    L=lvl(v["title"]); senior=int(L[0])<=7
    rows.append([v["name"],v["title"],L,"Yes" if senior else "No",lane(v["title"]),prio(v["title"],L),", ".join(sorted(v["src"])),v["li"],v["sumble"],v["note"]])
rows.sort(key=lambda r:(r[2],r[4],r[0]))
print("total",len(rows),"senior",sum(r[3]=="Yes" for r in rows))
json.dump(rows,open("merged.json","w"))

F="Arial"; navy=PatternFill("solid",fgColor="1F3A5F"); thin=Side(style="thin",color="BFC5CC")
wb=Workbook(); ws=wb.active; ws.title="People"
hdr=["No.","Name","Title as reported","Level","Meets seniority rule","Lane","Priority","Found by","LinkedIn","Sumble profile","Note"]
ws.append(hdr)
for i,r in enumerate(rows,1):
    ws.append([i,r[0],r[1],r[2][2:],r[3],r[4],r[5],r[6],"Open profile" if r[7] else "", "Open in Sumble" if r[8] else "",r[9]])
    for col,url in ((9,r[7]),(10,r[8])):
        if url:
            c=ws.cell(row=i+1,column=col); c.hyperlink=url; c.font=Font(name=F,size=10,color="1155CC",underline="single")
for c in ws[1]: c.font=Font(name=F,size=10,bold=True,color="FFFFFF"); c.fill=navy; c.alignment=Alignment(vertical="center",wrap_text=True)
fills={"A":"DDEFE0","B":"FFF1CC","C":"EEEEEE"}
for r in ws.iter_rows(min_row=2,max_row=len(rows)+1):
    for c in r:
        if not c.hyperlink: c.font=Font(name=F,size=10)
        c.alignment=Alignment(vertical="top",wrap_text=True); c.border=Border(top=thin,bottom=thin,left=thin,right=thin)
    r[6].fill=PatternFill("solid",fgColor=fills[r[6].value]); r[6].alignment=Alignment(horizontal="center",vertical="top")
for col,w in zip("ABCDEFGHIJK",[6,26,56,20,11,30,9,26,13,15,46]): ws.column_dimensions[col].width=w
ws.freeze_panes="C2"; last=len(rows)+1; ws.auto_filter.ref=f"A1:K{last}"; ws.row_dimensions[1].height=32

s=wb.create_sheet("Summary",0); T="People"
s["A1"]="Alcon people list, deduplicated"; s["A1"].font=Font(name=F,size=14,bold=True,color="1F3A5F")
notes=["This is the single people list for the Alcon account. It merges the BitScale senior grid, the first BitScale grid, the Sumble searches and the people named in research, with duplicates removed by LinkedIn profile and by name.",
"Security, IT compliance and IT audit people are excluded at the account owner's instruction: "+", ".join(v["name"] for v in removed)+".",
"Seniority rule: Director and above, Head, and Senior Manager. People below that level are kept because they confirm facts about the technology stack. Filter the column Meets seniority rule to Yes for the target list.",
"Titles are self reported on public profiles and are not verified. Level, lane and priority are our judgment from the title alone. Correct them in the People sheet and the counts below update.",
"Sources: BitScale grids pulled September 20, 2026. Sumble searches run September 19, 2026. Research sources are listed by ID in the Alcon Sources and Evidence file."]
for k,n in enumerate(notes):
    c=s.cell(row=3+k,column=1,value=n); c.font=Font(name=F,size=10); c.alignment=Alignment(wrap_text=True,vertical="top"); s.merge_cells(start_row=3+k,start_column=1,end_row=3+k,end_column=6); s.row_dimensions[3+k].height=42
r0=10; s.cell(row=r0,column=1,value="Level"); s.cell(row=r0,column=2,value="People"); s.cell(row=r0,column=3,value="Priority A")
levels=["C-level and SVP","VP","Senior Director","Director","Associate Director","Head","Senior Manager","Manager","Lead, owner, specialist or developer"]
for k,l in enumerate(levels):
    r=r0+1+k; s.cell(row=r,column=1,value=l); s.cell(row=r,column=2,value=f'=COUNTIF({T}!$D$2:$D${last},A{r})'); s.cell(row=r,column=3,value=f'=COUNTIFS({T}!$D$2:$D${last},A{r},{T}!$G$2:$G${last},"A")')
rt=r0+1+len(levels); s.cell(row=rt,column=1,value="Total"); s.cell(row=rt,column=2,value=f"=SUM(B{r0+1}:B{rt-1})"); s.cell(row=rt,column=3,value=f"=SUM(C{r0+1}:C{rt-1})")
s.cell(row=rt+1,column=1,value="Meets seniority rule"); s.cell(row=rt+1,column=2,value=f'=COUNTIF({T}!$E$2:$E${last},"Yes")')
r2=rt+3; s.cell(row=r2,column=1,value="Lane, senior people only")
for j,h in enumerate(["A","B","C","Total"]): s.cell(row=r2,column=2+j,value=h)
lanes=["Salesforce and commercial","ServiceNow","Veeva and life sciences systems","SAP and ERP","Field service and customer operations","Analytics and data","Business technology and digital","IT leadership and other"]
for k,l in enumerate(lanes):
    r=r2+1+k; s.cell(row=r,column=1,value=l)
    for j,pv in enumerate(["A","B","C"]): s.cell(row=r,column=2+j,value=f'=COUNTIFS({T}!$F$2:$F${last},$A{r},{T}!$G$2:$G${last},"{pv}",{T}!$E$2:$E${last},"Yes")')
    s.cell(row=r,column=5,value=f"=SUM(B{r}:D{r})")
r3=r2+1+len(lanes); s.cell(row=r3,column=1,value="Total")
for j in range(4): col=get_column_letter(2+j); s.cell(row=r3,column=2+j,value=f"=SUM({col}{r2+1}:{col}{r3-1})")
for row in s.iter_rows(min_row=r0,max_row=r3):
    for c in row:
        if c.value is None: continue
        c.font=Font(name=F,size=10,bold=c.row in (rt,r3)); c.border=Border(top=thin,bottom=thin,left=thin,right=thin)
for c in list(s[r0])+list(s[r2]):
    if c.value is not None: c.fill=navy; c.font=Font(name=F,size=10,bold=True,color="FFFFFF")
s.column_dimensions["A"].width=44
for col in "BCDEF": s.column_dimensions[col].width=12
wb.save("Alcon_People_List.xlsx"); print("saved")
