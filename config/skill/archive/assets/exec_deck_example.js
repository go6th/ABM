// Example executive brief build with three part speaker notes, a native chart and a challenges slide. Replace the account content. Read the pptx skill before running.
const pptxgen = require("pptxgenjs");
const p = new pptxgen(); p.layout = "LAYOUT_16x9";
const INK="12202E", PAPER="FFFFFF", SAND="EEF1F4", TEAL="1F7A74", ORANGE="E2571F", MUTE="5D6873", LINE="D8DDE3", NAVY="1F3A5F";
const HF="Cambria", BF="Calibri";
const notes = (slide, c, g, h) => slide.addNotes(`CONFIRMED (read in a primary source):\n${c}\n\nWHAT WE ARE GUESSING (inferred from one or two sources):\n${g}\n\nWHAT WE HAVE A HUNCH ON (our synthesis, no source says it):\n${h}`);
const title = (s, t, dark=false) => s.addText(t, { x:0.5, y:0.35, w:9, h:0.8, fontFace:HF, fontSize:30, bold:true, color: dark?PAPER:INK, isTextBox:true, margin:0, valign:"top" });
const card = (s, x, y, w, h, head, body, accent=TEAL, fill=SAND) => {
  s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill:{ color: fill }, line:{ color: LINE, width:0.5 }, rectRadius:0.08 });
  s.addShape(p.ShapeType.ellipse, { x:x+0.2, y:y+0.2, w:0.22, h:0.22, fill:{ color: accent }, line:{ color: accent } });
  s.addText(head, { x:x+0.52, y:y+0.12, w:w-0.7, h:0.55, fontFace:BF, fontSize:13, bold:true, color:INK, isTextBox:true, margin:0, valign:"top" });
  s.addText(body, { x:x+0.2, y:y+0.75, w:w-0.4, h:h-0.85, fontFace:BF, fontSize:11, color:MUTE, isTextBox:true, margin:0, valign:"top" });
};
const stat = (s, x, y, w, n, label, color=ORANGE, dark=false) => {
  s.addText(n, { x, y, w, h:0.9, fontFace:HF, fontSize:44, bold:true, color, isTextBox:true, margin:0 });
  s.addText(label, { x, y:y+0.9, w, h:0.8, fontFace:BF, fontSize:12, color: dark?"C9D3DC":MUTE, isTextBox:true, margin:0, valign:"top" });
};
const foot = (s, dark=false) => s.addText("Alcon account brief  ·  6th  ·  September 2026", { x:0.5, y:5.2, w:9, h:0.3, fontFace:BF, fontSize:9, color: dark?"8FA1B3":"9AA5AF", isTextBox:true, margin:0 });

// 1 Cover
let s = p.addSlide(); s.background = { color: INK };
s.addShape(p.ShapeType.ellipse, { x:0.5, y:0.5, w:0.3, h:0.3, fill:{ color: ORANGE }, line:{ color: ORANGE } });
s.addText("Alcon", { x:0.5, y:1.4, w:9, h:1.1, fontFace:HF, fontSize:54, bold:true, color:PAPER, isTextBox:true, margin:0 });
s.addText("Account brief for the platform services pursuit", { x:0.5, y:2.5, w:9, h:0.6, fontFace:BF, fontSize:20, color:"C9D3DC", isTextBox:true, margin:0 });
s.addText("Salesforce  ·  SAP  ·  ServiceNow  ·  Tableau and analytics  ·  Veeva  ·  Data and AI", { x:0.5, y:3.3, w:9, h:0.4, fontFace:BF, fontSize:13, color:"C9D3DC", isTextBox:true, margin:0 });
s.addText("Executive summary. The detailed brief, evidence file, people list and job list carry every source.", { x:0.5, y:4.5, w:9, h:0.4, fontFace:BF, fontSize:11, color:"8FA1B3", isTextBox:true, margin:0 });
s.addNotes("This deck summarizes a full account brief. Every slide's notes separate what is confirmed in a primary source, what we are inferring, and what is a hunch. Only confirmed items should be stated to Alcon; the rest become questions.");

// 2 Account in one page
s = p.addSlide(); s.background = { color: PAPER }; title(s, "The account in one page"); foot(s);
stat(s, 0.5, 1.3, 2.1, "$2.8B", "Q2 2026 sales, up 8 percent, with equipment up 26 percent on the Unity platform");
stat(s, 2.8, 1.3, 2.1, "$121M", "cost of the efficiency measures announced in February 2026, booked so far this year");
stat(s, 5.1, 1.3, 2.1, "2", "large acquisitions terminated in 2026, LENSAR and STAAR");
stat(s, 7.4, 1.3, 2.1, "2nd", "Bengaluru office opened July 2026, covering IT, R&D, analytics and finance");
card(s, 0.5, 3.2, 4.4, 1.8, "A new technology leadership layer", "A Chief Information and Transformation Officer arrived in August 2024 and has brought in colleagues from his former employer, including a new VP of Digital Strategy in December 2025.");
card(s, 5.1, 3.2, 4.4, 1.8, "Growth strain on commercial and service systems", "Equipment sales growing 26 percent, a product launch every quarter, and a global service business built on connected devices put pressure on CRM, ERP and field service.", ORANGE);
notes(s, "Q2 2026 figures and the $121 million are from Alcon's Q2 results filed with the SEC on August 10, 2026: the measures were announced in February 2026, cover restructuring, consulting and other direct costs, and the CFO said on the call they will reach a $100 million savings run rate, $50 million this year. STAAR was terminated on January 6, 2026 after STAAR shareholders, led by 30 percent holder Broadwood, voted the deal down despite a raised offer. LENSAR was terminated on March 16, 2026 after nearly a year of FTC opposition. The Bengaluru opening is from trade press. The CITO's start date is from Alcon's investor site.",
"That the CITO is building his own team: five senior people came from his former employer within 16 months. Timing suggests it, nobody has said it.",
"That growth is straining the platforms. We infer it from hiring patterns and postings, not from any statement by Alcon.");

// 2b Financials
s = p.addSlide(); s.background = { color: PAPER }; title(s, "The numbers behind the account"); foot(s);
s.addChart(p.ChartType.bar, [
  { name: "Q2 2025", labels: ["Implantables","Consumables","Equipment","Contact lenses","Ocular health"], values: [456, 777, 222, 691, 432] },
  { name: "Q2 2026", labels: ["Implantables","Consumables","Equipment","Contact lenses","Ocular health"], values: [466, 825, 279, 726, 486] },
], { x:0.5, y:1.2, w:5.4, h:3.6, barDir:"col", barGrouping:"clustered", chartColors:["C9D3DC", TEAL], showTitle:true, title:"Net sales by category, $ millions", titleFontSize:12, titleFontFace:BF, titleColor:INK,
  showValue:true, dataLabelPosition:"outEnd", dataLabelFontSize:9, dataLabelColor:INK, catAxisLabelFontSize:9, catAxisLabelColor:MUTE, valAxisLabelFontSize:9, valAxisLabelColor:MUTE, valGridLine:{ color: LINE, style:"solid", size:0.5 }, catAxisLineShow:false, valAxisLineShow:false, showLegend:true, legendPos:"b", legendFontSize:9, legendColor:MUTE });
const fin = [
 ["$2.8B quarterly sales, up 8 percent", "Surgical $1.6B, Vision Care $1.2B. Equipment up 26 percent on Unity. Ocular health up 13 percent on Tryptyr and Systane."],
 ["Guidance raised in August", "Sales growth held at 5 to 7 percent. Core margin expansion raised to 90 to 190 basis points. Core EPS growth raised to 12 to 15 percent."],
 ["Cash returned, charge absorbed", "$538M returned to shareholders in the first half. A $402M non-cash charge to end the PowerVision lens program."],
 ["Efficiency measures since February", "$100M run rate savings, $50M this year, about $150M cost, done by year end. Mostly SG&A and people exits. Functions and sites not disclosed."],
 ["Tariffs and refunds", "$40M to $90M full year tariff cost. A $60M refund expected in Q3, two thirds reinvested in launches."],
];
fin.forEach((x,i) => { const y=1.15+i*0.78; s.addShape(p.ShapeType.ellipse, { x:6.2, y:y+0.05, w:0.18, h:0.18, fill:{ color: ORANGE }, line:{ color: ORANGE } });
  s.addText(x[0], { x:6.5, y, w:3.0, h:0.28, fontFace:BF, fontSize:11.5, bold:true, color:INK, isTextBox:true, margin:0 });
  s.addText(x[1], { x:6.5, y:y+0.27, w:3.0, h:0.5, fontFace:BF, fontSize:9.5, color:MUTE, isTextBox:true, margin:0, valign:"top" }); });
notes(s, "All figures are from Alcon's second quarter 2026 results, filed with the SEC on August 10, 2026, and the earnings call of August 11, 2026. Prior year Surgical category figures are the comparatives printed in the release. Prior year contact lens and ocular health figures are derived from the reported growth rates and rounded, so treat them as approximate.",
"That the efficiency measures are mostly people exits in SG&A: the CFO said SG&A leverage drives margin expansion and that savings depend on the timing of the exits. Alcon has not itemized the program.",
"That a company raising margin guidance while absorbing a $402 million charge and $121 million of efficiency costs will keep pressure on IT and shared services spending. Our reading, not Alcon's.");

// 2c Executive voice
s = p.addSlide(); s.background = { color: PAPER }; title(s, "What the executives said on the Q2 call"); foot(s);
const ev = [
 ["Focus", TEAL, ["Launch execution: Unity, PanOptix Pro, Tryptyr, Valeda and Unity CS, with more coming in 2027 including Vivity Pro, Unity DX and an eye whitener", "Invest behind launches, funded partly by a $60M tariff refund", "Finish the efficiency measures: $100M run rate, most exits in the second half", "Keep returning cash, with a $1.5B buyback under way"]],
 ["Opportunity", ORANGE, ["Unity: a 30,000 unit installed base to replace over ten years, early placements running ahead of plan", "Premium lenses: PanOptix Pro converted about 90 percent of PanOptix accounts, and one point of premium lens penetration is worth about $15M a quarter", "Contact lenses at record global share, US growth 8 percent", "Valeda for dry AMD, $100M to $150M in three to five years"]],
 ["Challenge", MUTE, ["US cataract volumes flat, blamed on surgeon capacity and a generational shift, not demand", "New competitive lens launches pressuring IOL growth", "Surgical glaucoma weakened by reimbursement changes", "International lens pricing softer than the US", "Tariffs of 10 to 12.5 percent on US imports", "PowerVision ended after unpredictable outcomes"]],
];
ev.forEach((c,i) => { const x=0.5+i*3.05;
  s.addShape(p.ShapeType.roundRect, { x, y:1.2, w:2.9, h:3.85, fill:{ color: SAND }, line:{ color: LINE, width:0.5 }, rectRadius:0.08 });
  s.addShape(p.ShapeType.ellipse, { x:x+0.2, y:1.38, w:0.22, h:0.22, fill:{ color: c[1] }, line:{ color: c[1] } });
  s.addText(c[0], { x:x+0.52, y:1.3, w:2.2, h:0.4, fontFace:BF, fontSize:15, bold:true, color:INK, isTextBox:true, margin:0 });
  s.addText(c[2].map((t,k)=>({ text:t, options:{ bullet:true, breakLine:k<c[2].length-1, paraSpaceAfter:5 } })), { x:x+0.2, y:1.8, w:2.55, h:3.1, fontFace:BF, fontSize:10, color:INK, isTextBox:true, margin:0, valign:"top" });
});
notes(s, "Everything on this slide is paraphrased from the CEO, David Endicott, and the CFO, Tim Stonesifer, on the August 11, 2026 earnings call, as published in the call transcript, and from the Q2 release.",
"The word focus is ours. The executives described these as priorities and investments without ranking them.",
"That the efficiency measures and the surgeon capacity constraint together make workflow and service technology, such as Unity DX and the connected operating room, a place where Alcon will spend. Nobody on the call tied technology spending to those points.");

// 2d Challenges
s = p.addSlide(); s.background = { color: PAPER }; title(s, "Industry, market and Alcon specific challenges"); foot(s);
const ch = [
 ["Industry", TEAL, ["Surgeon capacity, not patient demand, caps US cataract volumes; practices are adding optometrists, paraprofessionals and ASCs to free up OR time", "Reimbursement pressure: the US cataract physician fee was cut, glaucoma stent reimbursement changed, and China's volume based procurement erodes IOL prices", "Tariffs of 10 to 12.5 percent on US imports, with retaliatory tariffs assumed to stay", "Regulators blocking consolidation: the FTC opposed the LENSAR deal for nearly a year"]],
 ["Market", ORANGE, ["Aggregate eye care markets growing only 3 to 4 percent, with cataract volumes flat in the US and soft in Japan and Asia", "IOL competition intensifying, especially internationally, with new launches every quarter", "Big retail chains in Europe and Japan refusing contact lens price increases", "China AT-IOL demand capped by hospital DRG budgets, with price erosion expected under VBP"]],
 ["Alcon specific", MUTE, ["PowerVision accommodating lens ended after unpredictable outcomes, a $402M charge; accommodation stays a long term goal via RxSight", "Refractive white space still open after the STAAR and LENSAR deals failed", "Surgical glaucoma, the Hydrus stent, a continuing drag", "Legacy contact lenses declining as promotion is cut; multifocal share slipping", "$150M efficiency measures with exits in the second half, on top of a data extortion incident in August and turnover in the SAP leadership"]],
];
ch.forEach((c,i) => { const x=0.5+i*3.05;
  s.addShape(p.ShapeType.roundRect, { x, y:1.2, w:2.9, h:3.85, fill:{ color: SAND }, line:{ color: LINE, width:0.5 }, rectRadius:0.08 });
  s.addShape(p.ShapeType.ellipse, { x:x+0.2, y:1.38, w:0.22, h:0.22, fill:{ color: c[1] }, line:{ color: c[1] } });
  s.addText(c[0], { x:x+0.52, y:1.3, w:2.2, h:0.4, fontFace:BF, fontSize:15, bold:true, color:INK, isTextBox:true, margin:0 });
  s.addText(c[2].map((t,k)=>({ text:t, options:{ bullet:true, breakLine:k<c[2].length-1, paraSpaceAfter:5 } })), { x:x+0.2, y:1.8, w:2.55, h:3.1, fontFace:BF, fontSize:9.5, color:INK, isTextBox:true, margin:0, valign:"top" });
});
notes(s, "Industry and market points are paraphrased from the CEO and CFO on the February 25 and August 11, 2026 earnings calls and the Q2 filing: surgeon capacity and how practices respond, the physician fee cut and facility fee rise, glaucoma reimbursement, China DRG and VBP, tariff rates, chain resistance to price in Europe and Japan, and the FTC opposition on LENSAR. Alcon specific points on PowerVision, STAAR, LENSAR, Hydrus, legacy lenses and the efficiency measures are from the same sources. The data extortion incident is independently catalogued but not confirmed by Alcon. The SAP leadership turnover is from a public profile.",
"The split into industry, market and Alcon specific is ours. The executives did not frame it that way. That legacy lens decline and multifocal share loss are challenges rather than choices: the CEO described the legacy decline as deliberate.",
"That the efficiency exits, the shared services expansion and the leadership turnover together make the next year a period where partner relationships get re-decided. Nobody at Alcon has said that.");

// 3 Platform landscape
s = p.addSlide(); s.background = { color: PAPER }; title(s, "What runs the business today"); foot(s);
const lanes = [
 ["Salesforce", "Sales Cloud, Service Cloud, CPQ and Marketing Cloud, MuleSoft for integration, Copado for releases. Field service on ServiceMax. Platform team in Bangalore.", TEAL],
 ["SAP", "One global SAP instance that Alcon calls IRIS, a $500 million program, still rolling out to manufacturing. ECC to S/4HANA transition under way. Ariba, IBP, Datasphere and Analytics Cloud around it.", NAVY],
 ["ServiceNow", "IT, HR and facilities service management. NTT DATA is the partner and presented with Alcon at Knowledge 2026. Platform administration sits in Poland.", MUTE],
 ["Analytics and AI", "Tableau, Power BI, QuickSight and SAP Analytics Cloud side by side. Snowflake and Databricks both present. An agentic AI program on AWS and Azure.", ORANGE],
];
lanes.forEach((l,i) => card(s, 0.5 + (i%2)*4.6, 1.3 + Math.floor(i/2)*1.95, 4.4, 1.8, l[0], l[1], l[2]));
notes(s, "Sales Cloud, Service Cloud, CPQ, Marketing Cloud, MuleSoft, Copado and ServiceMax: confirmed in Alcon postings and employee titles. IRIS and the S/4HANA transition: named in postings and in an employee's own role description. ServiceNow scope and the partner: confirmed at Knowledge 2026. The AI stack: confirmed in the agentic AI posting.",
"That the S/4HANA transition is mid-flight: ECC and S/4HANA both appear in current postings. That Snowflake and Databricks are both in real use: detected over time, but no current posting names either.",
"That Tableau is nobody's platform: no owner, no admin, no hiring for it in a year. That the analytics estate is drifting toward Microsoft: one new role lists Fabric and Power BI first.");

// 4 Signals
s = p.addSlide(); s.background = { color: PAPER }; title(s, "Signals that money is moving"); foot(s);
const sig = [
 ["Agentic AI is funded and short of hands", "Bengaluru is hiring engineers to build agents for voice, email and chat that must connect to Salesforce, SAP, Genesys and ServiceNow."],
 ["Customer master data has a named program", "A Program Director for Customer Master Data Transformation is in place, and a global Master Data Governance director role has been open since March."],
 ["Salesforce team keeps growing", "Three senior Salesforce developer seats opened in twelve months, the latest requiring six years of CPQ work."],
 ["Analytics teams are hiring builders", "Two Sales Operations roles are building tools in Tableau and Alteryx for Customer and Commercial Operations, under leaders who are new in role since April."],
 ["The S/4HANA leader has left", "The director who ran the ECC to S/4HANA roadmap moved to another company. Alcon has to fill that gap with a hire or a partner."],
 ["New leaders in owning roles", "Thirty one of the senior technology and operations people started their current role in the last twenty months."],
];
sig.forEach((x,i) => card(s, 0.5 + (i%3)*3.05, 1.3 + Math.floor(i/3)*1.95, 2.9, 1.8, x[0], x[1], i<3?TEAL:ORANGE));
notes(s, "Each posting and title named here was read. The master data director role, the agentic AI role, the Salesforce developer roles and the Tableau roles are on Alcon's job board. The departed S/4HANA leader and the new-in-role counts come from public profiles.",
"That the agentic program is short of capacity: the posting was opened three days before we looked and names five platforms, which reads like a program running ahead of its team.",
"That a departure creates a partner opening: Alcon may already have replaced the role internally.");

// 5 People
s = p.addSlide(); s.background = { color: PAPER }; title(s, "Who owns what"); foot(s);
const ppl = [
 ["Executive", "Muru Murugappan, SVP, Chief Information and Transformation Officer, since August 2024. Marc Raman, Chief Data and AI Officer, since 2021. Brian Faz, VP Digital Strategy and Operations, since December 2025."],
 ["Salesforce and commercial", "Shawn Kondratiuk, VP Business Technology and Platforms. Abhishek Nagaraj, Service Cloud platform owner, and Aditya Kumar, Salesforce DevSecOps, both in Bangalore. Dilip Kumar, Marketing Cloud architect."],
 ["SAP and ServiceNow", "Bharat Gosar, VP Enterprise Software and Applications. Prabhakar Vagvala, Head of Global ERP Operations. Ashish Sonone, Senior Director Enterprise Service Management. Tapan Kumar Jena, ServiceNow platform owner."],
 ["Service and analytics", "Brian Pierro, VP US Surgical Equipment Services. Edgar Santoyo, Head of Customer Operations. Casey Street, Director Analytics, Global Customer Operations. Darrin Holder, field service IT lead. Sashank Musti, Head of Enterprise AI."],
];
ppl.forEach((x,i) => card(s, 0.5 + (i%2)*4.6, 1.3 + Math.floor(i/2)*1.8, 4.4, 1.7, x[0], x[1], NAVY));
s.addText("141 senior people identified across the lanes, 39 of them likely owners or executives. Titles are as reported on public profiles. Full list with links and priorities in the people workbook.", { x:0.5, y:4.95, w:9, h:0.3, fontFace:BF, fontSize:10, italic:true, color:MUTE, isTextBox:true, margin:0 });
notes(s, "Murugappan is confirmed on Alcon's investor site, Sonone through the Knowledge 2026 session, Musti through Alcon's own story. All other titles are self reported on public profiles and match across two independent people searches.",
"That Kondratiuk owns the commercial platforms including Salesforce, and Gosar owns SAP and ServiceNow. Their titles say so; no org chart confirms it.",
"That Brian Faz, brought in by the CITO from their shared former employer, is the person resetting digital direction and vendors.");

// 6 Where Wilco can win
s = p.addSlide(); s.background = { color: PAPER }; title(s, "Where Wilco Source can add value"); foot(s);
const win = [
 ["Service operations for the equipment business", "Wilco's med device practice covers asset management, field service and field inventory on Salesforce, with an SAP integration accelerator. Alcon's seam is SAP, Salesforce and ServiceMax.", TEAL],
 ["Pricing, quoting and contracts", "Alcon runs Salesforce CPQ with Vistex, Icertis and MuleSoft around it and keeps hiring analysts to hold it together. Wilco's pricing and quoting solutions and accelerators fit that seam.", TEAL],
 ["Agents that reach production", "Alcon is building agents for voice, email and chat that must touch Salesforce, SAP and Genesys. Wilco is an Agentforce partner with a readiness assessment and a data foundation practice.", TEAL],
 ["Analytics for commercial and service", "Four BI tools and dashboards built inside operations teams. Wilco's Tableau Next practice and med device analytics package put service and commercial analytics on the Salesforce data.", ORANGE],
 ["Org health before anything else", "A 24 hour Org Insights scan on Alcon's Salesforce orgs gives an evidence based first conversation with the platform owners in Bangalore and Fort Worth, and a view of AI readiness.", ORANGE],
 ["New capability, if added: SAP and ServiceNow", "The S/4HANA transition lost its leader, master data governance is hiring, and NTT DATA runs ServiceNow. Adding SAP integration and master data, and ServiceNow for service management, would let Wilco cover the whole seam.", MUTE],
];
win.forEach((x,i) => card(s, 0.5 + (i%3)*3.05, 1.2 + Math.floor(i/3)*1.95, 2.9, 1.85, x[0], x[1], x[2]));
notes(s, "Alcon facts on this slide are confirmed as on earlier slides. Wilco Source capabilities are as listed on wilcosource.com: med device solutions for sales, CPQ, asset management, field service and field inventory, SAP customer integration and pricing accelerators, Agentforce partner status and readiness assessment, Tableau Next, Org Insights, and managed services.",
"That these capabilities map onto Alcon's open needs. The mapping is ours; no one at Alcon has asked for any of it.",
"The sixth box is a proposal, not a capability Wilco has today. SAP and ServiceNow work would need to be built or partnered for, and would put Wilco up against the SAP integrators already inside Alcon and against NTT DATA.");

// 7 Risks
s = p.addSlide(); s.background = { color: PAPER }; title(s, "What could make this harder"); foot(s);
const risk = [
 ["Incumbents are entrenched", "NTT DATA runs ServiceNow and presented with Alcon at Knowledge 2026. Of 148 senior profiles, 12 list Accenture, concentrated in the Salesforce team, so Accenture is the likely incumbent there. SAP and IT are staffed by former Infosys (11), IBM (9), Deloitte (9), PwC (7), Cognizant (6) and TCS (5) people who know how partners price."],
 ["A quiet, long tenured organization", "Median tenure is eight years, and these leaders barely post on LinkedIn. Introductions, referrals and events will matter more than content."],
 ["Sensitive events in the background", "Alcon was named in a data extortion campaign in August 2026. It is context for careful preparation on identity and integration hygiene, never an opening line."],
];
risk.forEach((x,i) => card(s, 0.5 + i*3.05, 1.3, 2.9, 2.6, x[0], x[1], MUTE));
s.addText("Only confirmed facts should be stated to Alcon. Everything graded as a guess or a hunch becomes a question for the first conversation.", { x:0.5, y:4.2, w:9, h:0.6, fontFace:BF, fontSize:13, italic:true, color:INK, isTextBox:true, margin:0 });
notes(s, "NTT DATA as the ServiceNow partner is confirmed at Knowledge 2026. Tenure and LinkedIn activity are measured across 148 profiles. The extortion campaign is catalogued independently, though Alcon has not confirmed it.",
"That Accenture is the incumbent Salesforce partner. Twelve of 148 senior profiles list Accenture, including the Service Cloud owner, the Marketing Cloud architect and the customer data program director, but alumni prove history, not a current contract. The Infosys group is concentrated in ERP: the Head of Global ERP Operations, the ERP warehouse director, the order management director and the manufacturing IT vice president. Counts include any point in a person's career, not only their last employer.",
"That the security event will drive a review of Salesforce access and integrations. Nothing is known about the cause at Alcon.");

// 7b Jobs closed table
s = p.addSlide(); s.background = { color: PAPER }; title(s, "Platform hiring over the last 12 months"); foot(s);
s.addText("Every Alcon posting first seen between September 2025 and September 2026 that names a platform. Build roles develop or configure the platform, admin and owner roles run it or own its data, user roles are business jobs that use it. Postings naming two platforms count under each.", { x:0.5, y:1.15, w:9, h:0.45, fontFace:BF, fontSize:11, color:MUTE, isTextBox:true, margin:0 });
const th = { fontFace:BF, fontSize:11, bold:true, color:PAPER, fill:{ color: NAVY }, align:"left", valign:"middle" };
const td = { fontFace:BF, fontSize:11, color:INK, valign:"middle" };
const tdm = { fontFace:BF, fontSize:10.5, color:MUTE, valign:"middle" };
const rows = [
 [{text:"Platform",options:th},{text:"Postings",options:th},{text:"Closed",options:th},{text:"Still open",options:th},{text:"Build roles",options:th},{text:"Admin and owner roles",options:th},{text:"User roles",options:th}],
 [{text:"SAP",options:td},{text:"385",options:td},{text:"329",options:td},{text:"56",options:td},{text:"13",options:td},{text:"8",options:td},{text:"Finance, customer service, supply chain and warehouse users",options:tdm}],
 [{text:"Salesforce",options:td},{text:"212",options:td},{text:"189",options:td},{text:"23",options:td},{text:"9",options:td},{text:"7",options:td},{text:"Sales, marketing and customer service users",options:tdm}],
 [{text:"Tableau",options:td},{text:"79",options:td},{text:"65",options:td},{text:"14",options:td},{text:"3",options:td},{text:"0",options:td},{text:"Analysts naming Tableau among several tools",options:tdm}],
 [{text:"ServiceNow",options:td},{text:"34",options:td},{text:"26",options:td},{text:"8",options:td},{text:"0",options:td},{text:"0",options:td},{text:"HR, payroll and IT operations users",options:tdm}],
];
s.addTable(rows, { x:0.5, y:1.7, w:9, colW:[1.3,1.0,0.9,1.0,1.4,1.4,2.0], rowH:0.42, border:{ type:"solid", color:LINE, pt:0.5 }, fill:{ color: PAPER } });
s.addText("Full detail for all 595 postings, including descriptions and links, is in the jobs workbook.", { x:0.5, y:4.5, w:9, h:0.35, fontFace:BF, fontSize:11, italic:true, color:MUTE, isTextBox:true, margin:0 });
notes(s, "Counts are from the complete set of Alcon postings over the period, including closed ones, with the platform sentence read in every posting and the 48 platform roles read in full. Reposted requisitions are included.",
"The class column is our judgment from the posting text: a build role is one where the platform is the job, an owner role owns the platform or its data.",
"Distinct positions are somewhat fewer than postings because some requisitions were reposted.");

// 7c What the postings tell us
s = p.addSlide(); s.background = { color: PAPER }; title(s, "What a year of job postings tells us"); foot(s);
const jb = [
 ["Salesforce team keeps growing in Bangalore", "Alcon hired a senior Salesforce developer roughly every four months over the past year, plus three analysts. The team runs releases through Copado and connects CPQ to Vistex, Icertis and MuleSoft."],
 ["IRIS, Alcon's own SAP program, is still rolling out", "IRIS is Alcon's internal name for its SAP implementation, described by its former governance head as a $500 million program. Postings show it still reaching manufacturing sites, a move to SAP Cloud ALM, and S/4HANA beside ECC."],
 ["Master data is where SAP budget shows", "A governance director open since March, two ERP master data managers, three stewards, and customer and pricing data teams in Warsaw, Malaysia and Mexico."],
 ["New SAP products are arriving", "Business Data Cloud and BTP, Datasphere, Analytics Cloud and process mining all appear in 2026 postings."],
 ["ServiceNow hiring is nil", "No developer, administrator or architect role all year. NTT DATA runs the platform with a small Alcon team in Poland."],
 ["Tableau has no dedicated builders", "The one dashboard developer role closed in a day. Power BI, QuickSight and Looker keep appearing beside Tableau."],
];
jb.forEach((x,i) => card(s, 0.5 + (i%3)*3.05, 1.3 + Math.floor(i/3)*1.95, 2.9, 1.8, x[0], x[1], i<3?TEAL:NAVY));
notes(s, "Every claim here is read directly from posting text: the tools named, the dates and the counts. IRIS is named in three postings and in eight employee profiles; the $500 million figure is one employee's description of the program on her profile, so it is her number, not Alcon's.",
"That ServiceNow's zero hiring means an incumbent runs it. The pattern fits, and the partner is confirmed separately, but no posting says so.",
"That the analytics estate is drifting toward Microsoft. One new role lists Fabric and Power BI first, and several list Power BI beside Tableau.");

// 7d What LinkedIn tells us
s = p.addSlide(); s.background = { color: PAPER }; title(s, "What 148 senior profiles tell us"); foot(s);
const lk = [
 ["The CITO is building his own team", "Five people came from BNSF Railway, his former employer, including two VPs hired in 2025."],
 ["Accenture alumni run Salesforce", "Twelve people list Accenture, including the Service Cloud owner, the Marketing Cloud architect and the customer data program director."],
 ["The S/4HANA leader has left", "Udayakanth Kuppa described the ECC to S/4HANA roadmap as his work and now shows a new employer."],
 ["Many owners are new in their seats", "31 of 134 started their current role since January 2025, including the two Customer Operations analytics leaders."],
 ["Long tenure under a new top layer", "Median tenure is eight years. 64 of 148 came from Novartis. Deep institutional habits sit below leaders who want change."],
 ["Quiet on LinkedIn", "Of 32 priority people, 3 posted in three months and 12 commented, mostly congratulations. Relationships and events will open doors, not content."],
];
lk.forEach((x,i) => card(s, 0.5 + (i%3)*3.05, 1.3 + Math.floor(i/3)*1.95, 2.9, 1.8, x[0], x[1], i<3?ORANGE:MUTE));
notes(s, "Counts of prior employers, start dates, tenure and posting activity are computed from the profiles. The S/4HANA roadmap is in Kuppa's own role description.",
"That the BNSF hires mean a direction reset, that Accenture is the incumbent, and that Kuppa's departure leaves a gap Alcon has not filled.",
"That new-in-role leaders are more open to outside views. Common experience, not evidence from Alcon.");

// 7e Other things worth knowing
s = p.addSlide(); s.background = { color: PAPER }; title(s, "Other things worth knowing"); foot(s);
const oth = [
 ["The best Salesforce opening was hidden", "The senior Salesforce developer role was titled Principal, DevSecOps. Only the description showed it. Title searches miss Alcon's real platform roles."],
 ["Salesforce Field Service appeared, then stopped", "The name was in Alcon postings from 2022 to 2024 and has not appeared since, while ServiceMax has been named every year since 2019. It may be a recruiter's synonym for ServiceMax. Ask whether field service is settled."],
 ["The contact centre runs on Genesys", "Genesys Cloud has been in place since 2019 with a named platform owner, and Observe.AI is being added. Agents for voice, email and chat will land here."],
 ["Microsoft tools appear beside Salesforce and Tableau", "Two leaders describe Copilot pilots on their profiles, one new analytics role lists Fabric and Power BI ahead of Tableau, and Power BI is named in 10 of the 13 Tableau postings."],
 ["Shared services keep expanding", "Bengaluru opened a second office in July. The head of global shared services took on Malaysia in September. Platform work is following."],
 ["Two big deals fell through", "The LENSAR and STAAR acquisitions were terminated in 2026, so there is no integration program to sell into. LumiThera and Aurion did close."],
];
oth.forEach((x,i) => card(s, 0.5 + (i%3)*3.05, 1.3 + Math.floor(i/3)*1.95, 2.9, 1.8, x[0], x[1], i<3?TEAL:MUTE));
notes(s, "The DevSecOps posting, the Genesys owner, the Copilot mentions, the Bengaluru and Malaysia moves and the deal terminations are all read in primary sources.",
"That Salesforce Field Service was ever evaluated at all: the two year appearance and disappearance of the name is the only evidence, and ServiceMax runs on Salesforce, so a recruiter may have used the name loosely. That Microsoft is gaining ground in analytics: three signals, none of them a decision.",
"That the contact centre is where the first agents will show up, and that the shared services expansion pulls partner work toward India.");

// 8 Next steps
s = p.addSlide(); s.background = { color: INK }; title(s, "Recommended next steps", true); foot(s, true);
const nxt = [
 ["1", "Name the pursuing client", "Unlocks reference matching, prior history and the vendor account team relationships."],
 ["2", "Open with service operations", "The VP of Equipment Services and the Head of Customer Operations, on outcomes, not software."],
 ["3", "Meet the data owners", "The customer master data program director and the master data governance lead, on readiness for agents."],
 ["4", "Route through Bengaluru", "The platform owners sit there. Specialist capacity is a credible first offer."],
];
nxt.forEach((x,i) => {
  const y = 1.3 + i*0.95;
  s.addShape(p.ShapeType.ellipse, { x:0.5, y:y+0.05, w:0.55, h:0.55, fill:{ color: ORANGE }, line:{ color: ORANGE } });
  s.addText(x[0], { x:0.5, y:y+0.05, w:0.55, h:0.55, fontFace:HF, fontSize:20, bold:true, color:PAPER, align:"center", valign:"middle", isTextBox:true, margin:0 });
  s.addText(x[1], { x:1.3, y, w:8.2, h:0.35, fontFace:BF, fontSize:16, bold:true, color:PAPER, isTextBox:true, margin:0 });
  s.addText(x[2], { x:1.3, y:y+0.38, w:8.2, h:0.4, fontFace:BF, fontSize:12, color:"C9D3DC", isTextBox:true, margin:0 });
});
s.addNotes("These steps follow from the confirmed facts and the hypotheses on the previous slides. Step one is the only blocker on our side. Steps two to four are ordered by how close each conversation sits to funded work.");

p.writeFile({ fileName: "/home/claude/alcon/deck/Alcon_Executive_Brief.pptx" }).then(() => console.log("deck ok"));
