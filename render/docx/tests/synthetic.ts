// An invented account for the tests: every name, address and number here is made up. It covers
// every bundle field the Word renderers read, with one or two rows each.
import type { AccountBundle } from "../src/bundle.js";

const row5 = (a: string, b: string, c: string, d: string, e: string): string[] => [a, b, c, d, e];

export function makeBundle(): AccountBundle {
  return {
    schema_version: "1.0.0",
    meta: {
      account_short: "NWI",
      account_legal: "Northwind Instruments plc",
      domain: "northwind.example",
      careers_site: "jobs.northwind.example",
      run_date: "20 October 2026",
      pull_date: "19 October 2026",
      pull_date_short: "19 Oct 2026",
      window: { from: "2025-10-19", to: "2026-10-19" },
      window_display: { from: "19 October 2025", to: "19 October 2026" },
      pursuing_client_display: "Client",
      pursuing_client_internal: null,
      byline: "Research by 6th agents.",
      lanes: ["Salesforce", "SAP", "ServiceNow"],
      people_lanes: ["Salesforce", "SAP", "ServiceNow", "Technology leadership"],
      entities: [
        { label: "NWI", counted_as_account: true },
        { label: "NWI (Harbor Supply)", counted_as_account: true },
        { label: "Harbor Analytics (divested business)", counted_as_account: false, phrase: "Harbor positions" },
      ],
      source_ids: { feed: "S2", detections: "S3", teams: "S3", news_feed: "S3", live_board: "S2" },
    },
    sources: {
      S1: { name: "Northwind leadership page – who we are", url: "https://www.northwind.example/about/leadership", type: "Company site", date: "Captured 19 Oct 2026", how: "Page read", supports: "Executive names" },
      S2: { name: "Northwind careers feed", url: "https://jobs.northwind.example/feed", type: "Data pull", date: "19 Oct 2026", how: "Feed pulled", supports: "Postings" },
      S3: { name: "Technology detections feed", url: "https://detect.example/northwind", type: "Data pull", date: "19 Oct 2026", how: "Feed pulled", supports: "Detections and teams" },
    },
    jobs_read: {
      J1: { name: "Salesforce Platform Architect (Leeds, open, first seen 2026-10-01)", url: "https://jobs.northwind.example/job/1001", plats: "Salesforce", cls: "Build role", what: "Salesforce Platform Architect (Leeds): designs the global CRM org; owns Service Cloud and Experience Cloud.", status: "Open", f: "2026-10-01", ent: "NWI" },
      J2: { name: "SAP Finance Lead (Lisbon, closed, first seen 2026-03-02)", url: "https://jobs.northwind.example/job/1002", plats: "SAP, Salesforce", cls: "Admin and owner role", what: "SAP Finance Lead (Lisbon): runs the S/4HANA finance template → rollout to two plants.", status: "Closed", f: "2026-03-02", ent: "NWI" },
    },
    jobs_rows: [{
      requisition: "1001", title: "Salesforce Platform Architect", platforms: ["Salesforce"], cls: "Build role", status: "Open",
      first_seen: "2026-10-01", last_seen: "2026-10-19", location: "Leeds", seniority: "Senior", entity: "NWI",
      what: "Designs the global CRM org.", platform_sentences: "Owns Service Cloud.", description: "A long description.", url: "https://jobs.northwind.example/job/1001",
    }],
    people_rows: [{ name: "Priya Example", title: "Director, CRM Technology", level: 4, senior: true, lane: "Salesforce", priority: "A", found_by: "6th", profile_url: "https://social.example/in/priya-example", location: "Leeds" }],
    people_excluded: { domain: ["Sam Collision"], security: ["Kim Guard, Director IT Security"], unresolved: [], below_rule_no_stack: ["Lee Junior"] },
    overrides: [],
    stack_rows: [{ business_function: "Sales", category: "CRM", technology: "Salesforce", postings: 40, lane: "Salesforce" }],
    team_rows: [{ team: "Commercial Systems", lane_technologies: "Salesforce", postings: 12, first_activity: "2025-11-01", last_activity: "2026-10-10", technologies: "Salesforce, Copado", locations: "Leeds", job_functions: "Engineering" }],
    ranking: { base: 300, rows: [{ platform: "Salesforce", category: "CRM", lane: "Salesforce", count: 40, open: 6, last_seen: "2026-10-19", in_stack: "Yes", checked: "Yes" }], seen_once: [{ platform: "Qlik", category: "Analytics", lane: "Tableau" }] },
    computed: {
      jobs_counts: {
        Salesforce: { total: 40, open: 6, build: 2, admin: 3, tool: 25, user: 10 },
        SAP: { total: 90, open: 12, build: 4, admin: 5, tool: 60, user: 21 },
        ServiceNow: { total: 5, open: 1, build: 0, admin: 0, tool: 4, user: 1 },
      },
      ent_counts: { NWI: 1200, "Harbor Analytics (divested business)": 80, "NWI (Harbor Supply)": 10 },
      lane_rows_account: 130, lane_rows_all: 150, people_total: 42, senior: 20,
      prio: { A: 5, B: 10, C: 5 }, excluded: { domain: 1, security: 1, unresolved: 0, below_rule_no_stack: 1 },
      pf: { senior_with_profiles: 18 },
      tracker_tally: { Done: 2, Partial: 1, "Not done": 1 },
    },
    prose: {
      glossary: "Internal names used by NWI and written out here: NCS is Northwind Commercial Systems. Interpretation is labeled as such in every section.",
      narrative: {
        picture: ["Northwind sells lab instruments – mostly to hospitals (S1).", "It runs one CRM and one ERP."],
        stands_out: ["A new CIO started in September 2026 (S1)."],
        lanes: [["Salesforce", "One global org is being built (J1)."], ["SAP", "Finance template rollout (J2)."]],
        wrong: ["The CRM program may be smaller than the postings suggest."],
        sharpen: ["Ask the CIO's office which partner runs the CRM program."],
      },
      news: [row5("6 Oct 2026", "New CIO appointed – from a rival", "Read", "S1", "Budget owner for every lane")],
      news_not_covered: "Nothing else in the window was read.",
      baseline: ["Revenue: fiscal 2026 £1,200 million (S1).", "Hubs: Leeds and Lisbon (S2)."],
      signals: {
        "Tier 1": [row5("CRM harmonization program hiring architects (J1)", "Confirmed", "Salesforce", "Oct 2026", "J1")],
        "Tier 2": [row5("Finance template rollout (J2)", "Probable", "SAP", "Mar 2026", "J2")],
        "Tier 3": [row5("Shared services growth", "Hypothesis", "ServiceNow", "2026", "S2")],
        "Context and history": [row5("Older ERP migration", "Lead", "SAP", "2023", "S1")],
      },
      platform: [["Salesforce", "Confirmed in use as the global CRM (J1).", "Confirmed", "J1"], ["SAP", "S/4HANA finance (J2).", "Confirmed", "J2"]],
      hiring_interpretation: "What a year of postings says (interpretation): SAP is the platform NWI builds with its own people.",
      people_text: {
        built: "The people list was built from 42 names found by 6th agents.",
        profiles: "Eighteen senior profiles were read.",
        exclusions: "Exclusions on the workbook Summary sheet name every removed security title.",
      },
      key_people: [
        ["Priya Example", "Director, CRM Technology", "Salesforce", "A", "https://social.example/in/priya-example", "Runs the CRM program (S1)."],
        ["Jordan Sample", "Chief Information Officer", "Technology leadership", "A", "", "Joined in September 2026 (S1)."],
      ],
      profile_findings: [["Tenure", "Most leaders joined after 2024", "Probable", "A new team is forming"]],
      status_changes: [["Jordan Sample", "CIO elsewhere", "CIO at NWI", "S1"]],
      people_interpretation: ["The buying center for the Salesforce lane is two people deep."],
      hypotheses: [["The new CIO will start a CRM review in 2027.", "Has the CIO named a CRM review?"]],
      cautions: ["Do not quote the profile notes to the people themselves."],
      first_call: ["Which partner runs the CRM program today?"],
      waiting: ["Whether to include the Harbor positions."],
      tracker_p1: [
        ["Company news", "Done", "High", "Nothing", "Newsroom read", "None"],
        ["Prior customer status", "Partial", "High", "Client confirmation", "Mail searched", "Confirm with the Client"],
      ],
      tracker_p2: [
        ["Patents", "Not done", "Optional", "Nothing", "Not searched", "Search when asked"],
        ["Vendor pages", "Done", "Medium", "Nothing", "Two pages read", "None"],
        ["Earnings calls", "Done", "High", "Transcript access", "Release read", "Transcript"],
        ["Leadership changes", "Done", "High", "Nothing", "Feed checked", "None"],
      ],
      evidence: {
        extracts: [{ id: "S1", title: "Northwind leadership page – who we are", url: "https://www.northwind.example/about/leadership", quotes: ["The CIO reports to the CEO."] }],
        live_rows: [["Salesforce Administrator", "https://jobs.northwind.example/job/2001", "Salesforce", "NWI", "Platform sentence", "Admin and owner role"]],
        live_lane_count: 1,
        leads: [["A vendor case study names NWI", "Searched the vendor site", "Lead, not verified", "S2"]],
        detections: [["Salesforce", "2025-11-02", "2026-10-03", 0.98, 3, 1], ["SAP S/4HANA", "2025-12-01", "2026-09-30", 0.91, 7, 0]],
        news_events: [["2026-10-06", "leadership", "Northwind names new CIO", "northwind.example"]],
        news_events_heading: "News feed events (S3), newest first, 1 event; feed only unless the Summary table says Read",
        board_paragraph: "The live careers site jobs.northwind.example was searched on 19 October 2026 for each lane keyword (S2).",
        part_c_closing: "Classification of the remaining lane postings was done by reading the platform sentences of every posting.",
        part_b_intro: "Each posting below was opened and read in full. Entity shows whether the posting is an NWI position.",
        part_g: "Every page read in this run was saved as text in the workspace folder data/releases.",
        detections_note: "First seen and last seen are the dates the technology name appeared in NWI's public material.",
        teams_note: "The largest teams whose public records name a lane platform are in the Platform teams sheet.",
      },
      workbook_notes: {
        people: ["Titles are self reported."], jobs: ["Entity rules applied."],
        stack: { confirmed_title: "Confirmed stack", confirmed_note: "From detections.", teams_title: "Platform teams", teams_note: "From team records.", ranking_notes: ["Base 300 postings."] },
      },
      lane_highlights: {
        subtitle: "Research date 20 October 2026. The leading opportunities and signals by lane, without source IDs.",
        sections: [{ heading: "Salesforce", bullets: ["One global CRM org is being built.", "A new architect role opened in October."] }, { heading: "SAP", bullets: ["A finance template is rolling out to two plants."] }],
      },
      internal_only: {
        title_suffix: "",
        dates: "19 to 20 October 2026",
        items: [["Client prospecting email, 1 Sep 2026.", "Names NWI among companies the Client says it works with."]],
        why: ["If the Client already counts NWI as a customer, the first conversation is an expansion conversation."],
        instead: "Prior customer status: Partial. Client confirmation needed.",
      },
    },
  };
}
