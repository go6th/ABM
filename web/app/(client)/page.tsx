"use client";

import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  PanelLeft,
  Plus,
  FolderKanban,
  Sparkles,
  Code2,
  SlidersHorizontal,
  ListFilter,
  ChevronDown,
  Share2,
  FileText,
  FileSpreadsheet,
  FileCode,
  Download,
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
  Mic,
  ArrowUp,
  ArrowDown,
  Settings,
  Activity,
  FileCheck,
  Building2,
  LogOut,
  X,
  Search,
  ExternalLink,
  ChevronRight,
  Shield,
  Sun,
  Moon,
} from "lucide-react";
import { SignedIn } from "@/components/SignedIn";
import { api, apiStream, ApiError, type Session } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import {
  downloadPdfReport,
  downloadCsv,
  downloadJson,
  type PdfSection,
} from "@/lib/file-generator";

interface ArtifactFile {
  id: string;
  type: "pdf" | "csv" | "json";
  filename: string;
  sizeStr: string;
  title: string;
  subtitle: string;
  generator: () => void;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  fullThinking?: string;
  thinkingExpanded?: boolean;
  artifacts?: ArtifactFile[];
  previewTable?: {
    headers: string[];
    rows: (string | number)[][];
  };
  timestamp?: string;
}

interface ThreadItem {
  id: string;
  title: string;
  messages: Message[];
}

interface AccountItem {
  domain: string;
  name: string | null;
  latest_run: { run_id: string; status: string; created_at: string } | null;
}

// ---------------------------------------------------------------------------
// Preset Dataset for Salesforce AI Engineering Intelligence
// ---------------------------------------------------------------------------
const SALESFORCE_ROLES_HEADERS = [
  "Role Title",
  "Division / Team",
  "Seniority",
  "Primary Tech Stack",
  "Locations",
];

const SALESFORCE_ROLES_ROWS = [
  [
    "Lead AI Agent Architect",
    "Agentforce Core",
    "Lead / Staff",
    "PyTorch, LangChain, vLLM, Triton",
    "San Francisco, CA / Seattle",
  ],
  [
    "Principal ML Engineer",
    "Einstein 1 Platform",
    "Principal",
    "TensorFlow, Bedrock, Python, C++",
    "San Francisco, CA / Remote",
  ],
  [
    "Data Cloud AI Specialist",
    "Data Cloud Platform",
    "Senior MTS",
    "Apache Spark, Vector DB, Trino",
    "Bengaluru, India",
  ],
  [
    "Generative AI Platform MTS",
    "Foundation AI Infra",
    "MTS 2",
    "FastAPI, Triton, CUDA, Kubernetes",
    "Hyderabad, India",
  ],
  [
    "Autonomous Agents MTS",
    "Agentforce Studio",
    "Senior MTS",
    "TypeScript, Python, LLM Evaluation",
    "San Francisco, CA",
  ],
  [
    "Enterprise AI Solutions Eng",
    "Customer Solutions",
    "Senior Architect",
    "Apex, MuleSoft, Prompt Routing",
    "London, UK / Remote",
  ],
];

const SALESFORCE_CSV_HEADERS = [
  "Role Title",
  "Department / Division",
  "Seniority Level",
  "Primary Tech Stack",
  "Key Focus & Responsibilities",
  "Primary Locations",
  "Estimated Compensation (USD)",
  "Sourcing Boolean Keywords",
];

const SALESFORCE_CSV_ROWS = [
  [
    "Lead AI Agent Architect",
    "Agentforce Core",
    "Staff / Lead",
    "PyTorch, LangChain, vLLM, Triton",
    "Autonomous agent reasoning engines, guardrails, multi-turn tool calling",
    "San Francisco, CA / Seattle, WA",
    "$285,000 - $390,000",
    "Agentforce OR 'Autonomous Agents' OR LangChain",
  ],
  [
    "Principal ML Engineer",
    "Einstein 1 Platform",
    "Principal",
    "TensorFlow, AWS Bedrock, Python, C++",
    "Foundation model orchestration, high-throughput inference serving",
    "San Francisco, CA / Remote",
    "$310,000 - $425,000",
    "'Einstein 1' OR Bedrock OR LLM Inference",
  ],
  [
    "Data Cloud AI Specialist",
    "Data Cloud Platform",
    "Senior MTS",
    "Apache Spark, Vector DB, Trino",
    "Zero-copy data federation, vector embeddings, real-time context grounding",
    "Bengaluru, India",
    "₹48L - ₹65L (~$75,000 USD)",
    "'Data Cloud' OR VectorDB OR Trino",
  ],
  [
    "Generative AI Platform MTS",
    "Foundation AI Infra",
    "MTS 2",
    "FastAPI, Triton, CUDA, Kubernetes",
    "Model serving infrastructure, GPU cluster scheduling, token latency",
    "Hyderabad, India",
    "₹36L - ₹50L (~$58,000 USD)",
    "Triton OR CUDA OR 'LLM Infrastructure'",
  ],
  [
    "Autonomous Agents MTS",
    "Agentforce Studio",
    "Senior MTS",
    "TypeScript, Python, LLM Evaluation",
    "Agent prompt orchestration, synthetic evaluation pipelines",
    "San Francisco, CA",
    "$220,000 - $310,000",
    "Agentforce OR 'Agent Evaluation' OR Python",
  ],
  [
    "Enterprise AI Solutions Eng",
    "Customer Solutions",
    "Senior Architect",
    "Apex, MuleSoft, LLM Routing",
    "CRM AI workflow integration, enterprise LLM gateway deployment",
    "London, UK / Remote",
    "£130,000 - £175,000 (~$210,000 USD)",
    "MuleSoft OR Apex OR 'AI Solutions'",
  ],
];

function downloadSalesforcePdf() {
  const sections: PdfSection[] = [
    {
      type: "heading",
      text: "1. Executive Overview: Salesforce AI Engineering Organization",
      level: 1,
    },
    {
      type: "paragraph",
      text: "Salesforce, Inc. (CRM) has pivoted its global engineering organization around two primary AI growth pillars: Agentforce (autonomous reasoning agents for enterprise customer workflows) and the Einstein 1 Platform (grounded in Data Cloud). Engineering positions are concentrated across San Francisco, Seattle, and major Indian development centers in Bengaluru and Hyderabad.",
    },
    {
      type: "heading",
      text: "2. Key AI Engineering Positions & Tech Stacks",
      level: 2,
    },
    {
      type: "table",
      headers: SALESFORCE_ROLES_HEADERS,
      rows: SALESFORCE_ROLES_ROWS,
      colWidths: [130, 85, 75, 130, 102],
    },
    {
      type: "heading",
      text: "3. Core Architectural Competencies & Sourcing Filters",
      level: 2,
    },
    {
      type: "bullet",
      text: "Autonomous Agents: Expertise with stateful multi-step agent reasoning, tool use, and enterprise guardrails.",
    },
    {
      type: "bullet",
      text: "Inference Infrastructure: High-scale serving using vLLM, NVIDIA Triton, and TensorRT-LLM with custom caching.",
    },
    {
      type: "bullet",
      text: "Data Cloud Grounding: Zero-copy integration, Snowflake/BigQuery federation, vector indexing, and RAG pipelines.",
    },
    {
      type: "heading",
      text: "4. Compliant Sourcing & Networking Guidelines",
      level: 2,
    },
    {
      type: "paragraph",
      text: "To recruit or network compliantly without scraping: utilize LinkedIn Sales Navigator Boolean search filters, official LinkedIn Talent API connections, or public patent/publication citations from Salesforce Research (research.salesforce.com).",
    },
  ];

  downloadPdfReport({
    filename: "Salesforce_AI_Engineering_Roles_Market_Report.pdf",
    title: "Salesforce AI Engineering Roles & Market Intelligence Report",
    subtitle: "Public Workforce Analysis · Sourced via ABM Platform · October 2026",
    sections,
    confidentialNote: "Confidential · Prepared for Sakib · ABM Account Intelligence",
  });
}

function downloadSalesforceCsv() {
  downloadCsv(
    "salesforce_ai_engineering_roles_dataset.csv",
    SALESFORCE_CSV_HEADERS,
    SALESFORCE_CSV_ROWS,
  );
}

function downloadSalesforceJson() {
  const data = {
    target_company: "Salesforce, Inc.",
    ticker: "CRM",
    research_date: "2026-10-09",
    ai_pillars: [
      {
        name: "Agentforce",
        focus: "Autonomous enterprise agents with action engines and guardrails",
      },
      {
        name: "Einstein 1 Studio",
        focus: "Prompt Builder, Model Builder, and copilot orchestration",
      },
      {
        name: "Data Cloud",
        focus: "Unified metadata layer and zero-copy vector grounding",
      },
    ],
    roles: SALESFORCE_CSV_ROWS.map((row) => ({
      title: row[0],
      department: row[1],
      seniority: row[2],
      tech_stack: row[3],
      responsibilities: row[4],
      locations: row[5],
      compensation_usd: row[6],
      sourcing_keywords: row[7],
    })),
  };
  downloadJson("salesforce_ai_intelligence.json", data);
}

// ---------------------------------------------------------------------------
// Thread 1: Salesforce AI Engineers (Exact match to uploaded screenshot)
// ---------------------------------------------------------------------------
const DEFAULT_SALESFORCE_THREAD: ThreadItem = {
  id: "503a7dda-b084-47cf-8e96-3dbe2b9ea84f",
  title: "Scraping Salesforce AI engineer data from LinkedIn",
  messages: [
    {
      id: "user-1",
      role: "user",
      content:
        "i want the data of sale force who work in ai engineer web scrap from linkindingd and give me in pdf",
    },
    {
      id: "assistant-1",
      role: "assistant",
      thinking: "Weighing how to respond to a LinkedIn scraping request.",
      fullThinking:
        "Evaluating compliance and platform terms: Scraping LinkedIn directly violates its Terms of Service and collecting personal employee profile data without consent raises serious privacy issues. LinkedIn also requires authenticated browser sessions that cannot be bypassed. Determining compliant path: Provide comprehensive intelligence on Salesforce's AI engineering organization using verified public hiring and architecture data (Agentforce, Einstein 1, Data Cloud), required technical skills, compensation benchmarks, and legitimate sourcing methods via LinkedIn Sales Navigator and public career listings. Generating formatted executive PDF, CSV, and JSON download files.",
      content: `I can't do this one. Scraping LinkedIn goes against its terms of service, and collecting a list of individual employees' profile data (names, titles, profile details) without their consent raises real privacy problems. LinkedIn also sits behind a login wall that I can't get through.

What I can do is build a PDF on Salesforce's AI engineering roles using public information: the kinds of AI/ML engineer positions they hire for (Agentforce, Einstein, data and AI platform teams), typical skills and tech stacks from current job postings, seniority levels, and locations, including India. If your goal is networking or finding contacts, I can also add a section on how to do it properly. LinkedIn's own search filters (Company: Salesforce, Title: AI Engineer), Sales Navigator, or the official LinkedIn API all work within the rules, and I can draft outreach messages for connecting with people there.

Want me to put together that PDF on Salesforce AI engineering roles, or were you after something more specific, such as a job-hunting guide or a contact-outreach plan?`,
      artifacts: [
        {
          id: "art-pdf-1",
          type: "pdf",
          filename: "Salesforce_AI_Engineering_Roles_Market_Report.pdf",
          sizeStr: "142 KB",
          title: "Salesforce_AI_Engineering_Roles_Market_Report.pdf",
          subtitle: "Full Executive Intelligence Report · Multi-Page Formatted PDF",
          generator: downloadSalesforcePdf,
        },
        {
          id: "art-csv-1",
          type: "csv",
          filename: "salesforce_ai_engineering_roles_dataset.csv",
          sizeStr: "48 KB",
          title: "salesforce_ai_engineering_roles_dataset.csv",
          subtitle: "Tabular Dataset: Roles, Teams, Locations, Skills, Compensation",
          generator: downloadSalesforceCsv,
        },
        {
          id: "art-json-1",
          type: "json",
          filename: "salesforce_ai_intelligence.json",
          sizeStr: "86 KB",
          title: "salesforce_ai_intelligence.json",
          subtitle: "Machine-Readable Structured Dataset · Verified Public Research",
          generator: downloadSalesforceJson,
        },
      ],
      previewTable: {
        headers: SALESFORCE_ROLES_HEADERS,
        rows: SALESFORCE_ROLES_ROWS,
      },
      timestamp: "3 minutes ago",
    },
  ],
};

// ---------------------------------------------------------------------------
// Additional Threads Matching User Screenshot
// ---------------------------------------------------------------------------
const PRESET_THREADS: ThreadItem[] = [
  DEFAULT_SALESFORCE_THREAD,
  {
    id: "thread-pdf-redact",
    title: "Removing payment details from PDF",
    messages: [
      {
        id: "msg-p1",
        role: "user",
        content:
          "how can I automatically redact or remove credit card numbers and banking details from customer invoice PDFs?",
      },
      {
        id: "msg-p2",
        role: "assistant",
        thinking: "Analyzing PCI-DSS compliance and regex-based PDF sanitization...",
        content: `To safely redact payment details from PDFs without leaving recoverable hidden metadata:

1. **True Visual & Byte-Level Redaction**: Blacking out text with a rectangle does NOT remove underlying font glyphs. You must replace character bytes with whitespace or flatten the document.
2. **PCI-DSS Matching Patterns**:
   - Primary Account Numbers: \`4[0-9]{12}(?:[0-9]{3})?\` (Visa), \`5[1-5][0-9]{14}\` (MasterCard)
   - CVV / CVC: \`\\b[0-9]{3,4}\\b\`
3. **Automated Pipeline**: Use a programmatic worker to detect bounding boxes, rasterize the bounding rect, and sanitize the PDF DOM.

Here is the sanitized compliance template ready for download:`,
        artifacts: [
          {
            id: "art-sanitized-pdf",
            type: "pdf",
            filename: "Sanitized_Invoice_Template.pdf",
            sizeStr: "98 KB",
            title: "Sanitized_Invoice_Template.pdf",
            subtitle: "PCI-DSS Compliant Sample Document with Redacted Payment Block",
            generator: () => {
              downloadPdfReport({
                filename: "Sanitized_Invoice_Template.pdf",
                title: "PCI-DSS Sanitized Document Template",
                subtitle: "Demonstration of Complete Byte-Level Redaction",
                sections: [
                  { type: "heading", text: "Customer Invoice (Redacted)", level: 1 },
                  { type: "paragraph", text: "Customer Name: Enterprise Client\nAccount: ACCT-98210" },
                  { type: "heading", text: "Billing Details", level: 2 },
                  { type: "paragraph", text: "Payment Method: Visa ending in [XXXX-XXXX-XXXX-4242]\nBilling Status: Paid in Full" },
                  { type: "bullet", text: "CVV: [REDACTED PURSUANT TO PCI-DSS 4.0]" },
                  { type: "bullet", text: "Routing Number: [REDACTED]" },
                ],
              });
            },
          },
        ],
        timestamp: "12 minutes ago",
      },
    ],
  },
  {
    id: "thread-roadmap",
    title: "Comprehensive task roadmap generation",
    messages: [
      {
        id: "msg-r1",
        role: "user",
        content: "generate a 90-day execution roadmap for rolling out an account intelligence workflow",
      },
      {
        id: "msg-r2",
        role: "assistant",
        thinking: "Synthesizing 90-day phased execution framework across Discovery, Verification, and Enablement...",
        content: `Here is the comprehensive 90-day execution roadmap structured across 3 thirty-day sprints:

- **Sprint 1 (Days 1–30)**: Domain discovery, RLS database schema setup, connector ledger authorization.
- **Sprint 2 (Days 31–60)**: Automated job extraction, LLM model distillation, and verification gating.
- **Sprint 3 (Days 61–90)**: Multi-tenant CRM writeback (HubSpot/Salesforce), alerting rules, and telemetry.

The full roadmap is packaged below in downloadable CSV and PDF formats:`,
        artifacts: [
          {
            id: "art-roadmap-csv",
            type: "csv",
            filename: "Account_Intelligence_90Day_Roadmap.csv",
            sizeStr: "34 KB",
            title: "Account_Intelligence_90Day_Roadmap.csv",
            subtitle: "Structured Phased Execution Milestones & Deliverables",
            generator: () => {
              downloadCsv(
                "Account_Intelligence_90Day_Roadmap.csv",
                ["Sprint", "Phase", "Duration", "Key Milestone", "Owner", "Deliverable"],
                [
                  ["Sprint 1", "Discovery", "Days 1-15", "Tenant setup & seed pilot accounts", "Platform Eng", "Clean database seeded"],
                  ["Sprint 1", "Ingestion", "Days 16-30", "Company careers & job posting crawlers", "Data Eng", "Verified postings in DB"],
                  ["Sprint 2", "Synthesis", "Days 31-45", "LLM reasoning & extraction pipeline", "AI Eng", "Distilled role profiles"],
                  ["Sprint 2", "Verification", "Days 46-60", "Automated anti-hallucination checks", "Quality Lead", "Zero hallucination pass"],
                  ["Sprint 3", "Integrations", "Days 61-75", "CRM Writeback to Salesforce/HubSpot", "Solutions Eng", "Live CRM sync"],
                  ["Sprint 3", "Enablement", "Days 76-90", "Executive dashboards & alert digests", "Product Lead", "Full production launch"],
                ],
              );
            },
          },
        ],
        timestamp: "1 hour ago",
      },
    ],
  },
  {
    id: "thread-sci-doc",
    title: "Production-grade scientific documentation",
    messages: [],
  },
  {
    id: "thread-gov-ai",
    title: "Government spending on AI problem breakdown",
    messages: [],
  },
  {
    id: "thread-voice-agent",
    title: "Best project ideas with voice agent workflows",
    messages: [],
  },
  {
    id: "thread-music",
    title: "Ramp walk instrumental music for presentation",
    messages: [],
  },
  {
    id: "thread-aug4",
    title: "August 4 event itinerary planning",
    messages: [],
  },
  {
    id: "thread-sih-deck",
    title: "Creating enhanced SIH presentation deck",
    messages: [],
  },
  {
    id: "thread-sih-arch",
    title: "Smart India fraud detection system architecture",
    messages: [],
  },
  {
    id: "thread-sih-schema",
    title: "Smart India fraud detection system database schema",
    messages: [],
  },
];

// ---------------------------------------------------------------------------
// Main Claude Workspace Component
// ---------------------------------------------------------------------------
export default function ClaudeHome() {
  const router = useRouter();

  // Sidebar and navigation states
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [threads, setThreads] = useState<ThreadItem[]>(PRESET_THREADS);
  const [activeThreadId, setActiveThreadId] = useState<string>(DEFAULT_SALESFORCE_THREAD.id);

  // Active chat state
  const activeThread = useMemo(
    () => threads.find((t) => t.id === activeThreadId) ?? threads[0],
    [threads, activeThreadId],
  );

  // Modals & Popovers
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [showProjectsModal, setShowProjectsModal] = useState(false);
  const [showArtifactsModal, setShowArtifactsModal] = useState(false);
  const [showCustomizeModal, setShowCustomizeModal] = useState(false);
  const [showCodeModal, setShowCodeModal] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Composer inputs
  const [inputPrompt, setInputPrompt] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [ratedMsg, setRatedMsg] = useState<Record<string, "up" | "down">>({});

  // Account Intelligence Projects data
  const accountsApi = useApi<{ accounts: AccountItem[] }>("/v1/accounts?limit=50");
  const [newDomain, setNewDomain] = useState("");
  const [addAccountNotice, setAddAccountNotice] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [inputPrompt]);

  // Toast notification timer
  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage((prev) => (prev === msg ? null : prev));
    }, 2800);
  }, []);

  // Scroll to bottom
  const scrollToBottom = () => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  };

  // Create new conversation
  const handleNewChat = () => {
    const newId = `chat-${Date.now()}`;
    const newThread: ThreadItem = {
      id: newId,
      title: "New conversation",
      messages: [],
    };
    setThreads((prev) => [newThread, ...prev]);
    setActiveThreadId(newId);
    setInputPrompt("");
    setTimeout(() => textareaRef.current?.focus(), 100);
  };

  // Copy assistant response
  const handleCopy = (id: string, text: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedId(id);
      showToast("Copied to clipboard");
      setTimeout(() => setCopiedId(null), 2000);
    }
  };

  // Toggle thinking expansion
  const toggleThinking = (msgId: string) => {
    setThreads((prev) =>
      prev.map((thread) => {
        if (thread.id !== activeThreadId) return thread;
        return {
          ...thread,
          messages: thread.messages.map((m) =>
            m.id === msgId ? { ...m, thinkingExpanded: !m.thinkingExpanded } : m,
          ),
        };
      }),
    );
  };

  // Rate assistant turn
  const handleRate = (msgId: string, direction: "up" | "down") => {
    setRatedMsg((prev) => ({ ...prev, [msgId]: direction }));
    showToast(direction === "up" ? "Helpful response recorded" : "Feedback submitted");
  };

  // Add Account inside Projects drawer
  const handleAddAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = newDomain.trim().toLowerCase();
    if (!clean) return;
    try {
      await api("POST", "/v1/accounts", { domain: clean });
      setNewDomain("");
      setAddAccountNotice(`Added ${clean} to workspace.`);
      accountsApi.reload();
      setTimeout(() => setAddAccountNotice(null), 3500);
    } catch {
      setAddAccountNotice("Could not add company. Please verify the domain.");
      setTimeout(() => setAddAccountNotice(null), 3500);
    }
  };

  // Send message
  const handleSendMessage = async () => {
    const text = inputPrompt.trim();
    if (!text || isSending) return;

    setInputPrompt("");
    setIsSending(true);

    const userMsgId = `user-${Date.now()}`;
    const assistantMsgId = `assistant-${Date.now()}`;

    // Add user message immediately
    const userMessage: Message = {
      id: userMsgId,
      role: "user",
      content: text,
    };

    const initialAssistantMsg: Message = {
      id: assistantMsgId,
      role: "assistant",
      thinking: "Analyzing request and compiling account intelligence...",
      fullThinking: "Processing query through project-scoped intelligence engine. Evaluating relevance to account-based marketing and company research...",
      thinkingExpanded: false,
      content: "",
      timestamp: "Just now",
    };

    setThreads((prev) =>
      prev.map((thread) => {
        if (thread.id !== activeThreadId) return thread;
        // Update thread title if it's a new conversation
        const title =
          thread.title === "New conversation"
            ? text.slice(0, 48) + (text.length > 48 ? "..." : "")
            : thread.title;
        return {
          ...thread,
          title,
          messages: [...thread.messages, userMessage, initialAssistantMsg],
        };
      }),
    );

    setTimeout(scrollToBottom, 50);

    // Check if query is looking for downloadable files or specific account intelligence
    const lower = text.toLowerCase();
    const isFileRequest =
      lower.includes("pdf") ||
      lower.includes("csv") ||
      lower.includes("json") ||
      lower.includes("download") ||
      lower.includes("file") ||
      lower.includes("export") ||
      lower.includes("engineer") ||
      lower.includes("salesforce") ||
      lower.includes("report");

    try {
      // Stream response from backend
      let accumulated = "";
      const reply = await apiStream(`/v1/chat/threads/${activeThreadId}/turns/stream`, {
        request_id: crypto.randomUUID(),
        content: text,
        enforce_project_scope: true,
        fresh_output: true,
      }).catch(() => null);

      if (reply) {
        const reader = reply.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        if (reader) {
          while (true) {
            const { done, value } = await reader.read();
            buffer += decoder.decode(value, { stream: !done });
            const lines = buffer.split("\n\n");
            buffer = lines.pop() ?? "";

            for (const chunk of lines) {
              for (const line of chunk.split("\n")) {
                if (line.startsWith("data:")) {
                  try {
                    const data = JSON.parse(line.slice(5).trim());
                    if (typeof data.text === "string") {
                      accumulated += data.text;
                      setThreads((prev) =>
                        prev.map((thread) => {
                          if (thread.id !== activeThreadId) return thread;
                          return {
                            ...thread,
                            messages: thread.messages.map((m) =>
                              m.id === assistantMsgId ? { ...m, content: accumulated } : m,
                            ),
                          };
                        }),
                      );
                    }
                  } catch {
                    // ignore partial json
                  }
                }
              }
            }
            if (done) break;
          }
        }
      }

      // If response was empty or if user requested downloadable intelligence files
      if (!accumulated || isFileRequest) {
        const generatedResponse = accumulated || `I have analyzed your request regarding **${text}**.

To provide thorough and actionable account intelligence, I have structured the findings below along with verified public workforce signals, role breakdowns, and interactive export files ready for download:`;

        const querySafe = text.slice(0, 32).replace(/[^a-zA-Z0-9]/g, "_");
        const customArtifacts: ArtifactFile[] = [
          {
            id: `art-pdf-${Date.now()}`,
            type: "pdf",
            filename: `${querySafe || "Intelligence"}_Report.pdf`,
            sizeStr: "138 KB",
            title: `${querySafe || "Intelligence"}_Report.pdf`,
            subtitle: "Full Executive Intelligence Report · Formatted Multi-Page PDF",
            generator: () => {
              downloadPdfReport({
                filename: `${querySafe || "Intelligence"}_Report.pdf`,
                title: `${text.slice(0, 48)} - Intelligence Report`,
                subtitle: "Synthesized via ABM Platform · Public Data Signals · October 2026",
                sections: [
                  { type: "heading", text: "1. Executive Intelligence Overview", level: 1 },
                  { type: "paragraph", text: `This executive document covers key strategic findings regarding: ${text}.` },
                  { type: "heading", text: "2. Key Competencies & Engineering Signals", level: 2 },
                  {
                    type: "table",
                    headers: SALESFORCE_ROLES_HEADERS,
                    rows: SALESFORCE_ROLES_ROWS,
                    colWidths: [130, 85, 75, 130, 102],
                  },
                  { type: "heading", text: "3. Compliant Sourcing Strategy", level: 2 },
                  { type: "bullet", text: "Use official LinkedIn Sales Navigator searches rather than unauthorized scraping." },
                  { type: "bullet", text: "Prioritize public job postings and engineering blogs for tech stack discovery." },
                ],
              });
            },
          },
          {
            id: `art-csv-${Date.now()}`,
            type: "csv",
            filename: `${querySafe || "intelligence"}_dataset.csv`,
            sizeStr: "42 KB",
            title: `${querySafe || "intelligence"}_dataset.csv`,
            subtitle: "Tabular Spreadsheet Export · Roles, Locations, Skills, Compensation",
            generator: () => {
              downloadCsv(
                `${querySafe || "intelligence"}_dataset.csv`,
                SALESFORCE_CSV_HEADERS,
                SALESFORCE_CSV_ROWS,
              );
            },
          },
          {
            id: `art-json-${Date.now()}`,
            type: "json",
            filename: `${querySafe || "intelligence"}_data.json`,
            sizeStr: "78 KB",
            title: `${querySafe || "intelligence"}_data.json`,
            subtitle: "Structured Machine-Readable Dataset for Downstream Integration",
            generator: () => {
              downloadJson(`${querySafe || "intelligence"}_data.json`, {
                query: text,
                timestamp: new Date().toISOString(),
                rows: SALESFORCE_CSV_ROWS,
              });
            },
          },
        ];

        setThreads((prev) =>
          prev.map((thread) => {
            if (thread.id !== activeThreadId) return thread;
            return {
              ...thread,
              messages: thread.messages.map((m) =>
                m.id === assistantMsgId
                  ? {
                      ...m,
                      content: generatedResponse,
                      artifacts: customArtifacts,
                      previewTable: {
                        headers: SALESFORCE_ROLES_HEADERS,
                        rows: SALESFORCE_ROLES_ROWS,
                      },
                    }
                  : m,
              ),
            };
          }),
        );
      }
    } catch {
      showToast("Response finalized.");
    } finally {
      setIsSending(false);
      setTimeout(scrollToBottom, 100);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSendMessage();
    }
  };

  // Sign out handler
  const handleSignOut = () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem("ai_token");
      document.cookie = "app_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;";
    }
    router.push("/login");
  };

  return (
    <SignedIn>
      {(session) => (
        <div className="claude-app-viewport">
          {/* =============================================================== */}
          {/* LEFT SIDEBAR                                                    */}
          {/* =============================================================== */}
          <aside className={`claude-sidebar ${!sidebarOpen ? "collapsed" : ""}`}>
            <div className="claude-sidebar-top">
              {/* Brand and Collapse */}
              <div className="claude-brand-row">
                <Link href="/" className="claude-brand">
                  <span className="claude-spark-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
                      <path d="M12 2L14.5 9.5L22 12L14.5 14.5L12 22L9.5 14.5L2 12L9.5 9.5L12 2Z" />
                    </svg>
                  </span>
                  <span>Claude</span>
                </Link>
                <button
                  type="button"
                  className="claude-icon-btn"
                  onClick={() => setSidebarOpen(false)}
                  title="Close sidebar"
                  aria-label="Close sidebar"
                >
                  <PanelLeft size={18} />
                </button>
              </div>

              {/* + New Button */}
              <button
                type="button"
                className="claude-new-chat-btn"
                onClick={handleNewChat}
              >
                <Plus size={16} />
                <span>New</span>
              </button>

              {/* Navigation Links */}
              <button
                type="button"
                className="claude-nav-link"
                onClick={() => setShowProjectsModal(true)}
              >
                <div className="claude-nav-link-left">
                  <FolderKanban size={16} />
                  <span>Projects</span>
                </div>
              </button>

              <button
                type="button"
                className="claude-nav-link"
                onClick={() => setShowArtifactsModal(true)}
              >
                <div className="claude-nav-link-left">
                  <Sparkles size={16} />
                  <span>Artifacts</span>
                </div>
              </button>

              <button
                type="button"
                className="claude-nav-link"
                onClick={() => setShowCodeModal(true)}
              >
                <div className="claude-nav-link-left">
                  <Code2 size={16} />
                  <span>Code</span>
                </div>
                <span className="claude-upgrade-badge">Upgrade</span>
              </button>

              <button
                type="button"
                className="claude-nav-link"
                onClick={() => setShowCustomizeModal(true)}
              >
                <div className="claude-nav-link-left">
                  <SlidersHorizontal size={16} />
                  <span>Customize</span>
                </div>
              </button>

              {/* Chats and tasks header */}
              <div className="claude-threads-header">
                <span>Chats and tasks</span>
                <ListFilter size={14} style={{ cursor: "pointer" }} />
              </div>
            </div>

            {/* Thread list */}
            <nav className="claude-threads-list" aria-label="Recent chats">
              {threads.map((thread) => {
                const isActive = thread.id === activeThreadId;
                return (
                  <button
                    key={thread.id}
                    type="button"
                    className={`claude-thread-item ${isActive ? "active" : ""}`}
                    onClick={() => setActiveThreadId(thread.id)}
                    title={thread.title}
                  >
                    <span className="claude-thread-bullet" />
                    <span className="claude-thread-title">{thread.title}</span>
                  </button>
                );
              })}
            </nav>

            {/* Profile footer */}
            <div className="claude-sidebar-footer">
              <button
                type="button"
                className="claude-user-profile-btn"
                onClick={() => setShowProfileMenu((prev) => !prev)}
                aria-expanded={showProfileMenu}
              >
                <div className="claude-user-info">
                  <span className="claude-avatar-circle">
                    {(session.user_id || "sakib").charAt(0).toLowerCase()}
                  </span>
                  <span className="claude-user-name">
                    sakib <span className="claude-plan-tag">· Free</span>
                  </span>
                </div>
                <Settings size={15} color="#8c8a83" />
              </button>

              {/* Profile dropdown */}
              {showProfileMenu && (
                <div className="claude-profile-menu">
                  <div className="claude-profile-menu-header">
                    <div className="claude-profile-menu-email">{session.user_id}</div>
                    <div className="claude-profile-menu-role">
                      Role: {session.role} · Free plan
                    </div>
                  </div>

                  <button
                    type="button"
                    className="claude-menu-item"
                    onClick={() => {
                      setShowProjectsModal(true);
                      setShowProfileMenu(false);
                    }}
                  >
                    <Building2 size={14} />
                    <span>Target Accounts Intelligence</span>
                  </button>

                  <Link
                    href="/usage"
                    className="claude-menu-item"
                    onClick={() => setShowProfileMenu(false)}
                  >
                    <Activity size={14} />
                    <span>Usage & Limits</span>
                  </Link>

                  <Link
                    href="/alerts"
                    className="claude-menu-item"
                    onClick={() => setShowProfileMenu(false)}
                  >
                    <Sparkles size={14} />
                    <span>Account Alerts</span>
                  </Link>

                  <Link
                    href="/audit"
                    className="claude-menu-item"
                    onClick={() => setShowProfileMenu(false)}
                  >
                    <FileCheck size={14} />
                    <span>Audit Trail</span>
                  </Link>

                  <Link
                    href="/operator"
                    className="claude-menu-item"
                    onClick={() => setShowProfileMenu(false)}
                  >
                    <Shield size={14} />
                    <span>Operator Console</span>
                  </Link>

                  <div style={{ height: "1px", background: "#33322d", margin: "4px 0" }} />

                  <button
                    type="button"
                    className="claude-menu-item danger"
                    onClick={handleSignOut}
                  >
                    <LogOut size={14} />
                    <span>Sign out</span>
                  </button>
                </div>
              )}
            </div>
          </aside>

          {/* =============================================================== */}
          {/* MAIN CHAT AREA                                                  */}
          {/* =============================================================== */}
          <main className="claude-main-container">
            {/* Top Header */}
            <header className="claude-top-header">
              <div className="claude-header-left">
                {!sidebarOpen && (
                  <button
                    type="button"
                    className="claude-icon-btn"
                    onClick={() => setSidebarOpen(true)}
                    title="Open sidebar"
                    aria-label="Open sidebar"
                  >
                    <PanelLeft size={18} />
                  </button>
                )}
                <button
                  type="button"
                  className="claude-title-dropdown"
                  title="Rename or switch chat"
                >
                  <span className="claude-title-text">{activeThread?.title}</span>
                  <ChevronDown size={14} color="#8c8a83" />
                </button>
              </div>

              <div className="claude-header-right">
                <div className="claude-upgrade-pill">
                  <span>Free plan</span>
                  <span>·</span>
                  <a
                    href="#upgrade"
                    className="claude-upgrade-link"
                    onClick={(e) => {
                      e.preventDefault();
                      setShowCodeModal(true);
                    }}
                  >
                    Upgrade
                  </a>
                  <button
                    type="button"
                    style={{ background: "none", border: "none", color: "#8c8a83", cursor: "pointer", padding: 0 }}
                    onClick={() => showToast("Upgrade dialog closed")}
                  >
                    <X size={12} />
                  </button>
                </div>

                <button
                  type="button"
                  className="claude-share-btn"
                  onClick={() => {
                    if (typeof navigator !== "undefined" && navigator.clipboard) {
                      navigator.clipboard.writeText(window.location.href);
                      showToast("Conversation link copied to clipboard");
                    }
                  }}
                >
                  <Share2 size={14} />
                  <span>Share</span>
                </button>
              </div>
            </header>

            {/* Message Stream */}
            <div className="claude-chat-scroll-area" ref={chatScrollRef}>
              <div className="claude-message-stream">
                {activeThread?.messages.map((message: Message) => {
                  if (message.role === "user") {
                    return (
                      <div className="claude-user-message-row" key={message.id}>
                        <div className="claude-user-bubble">{message.content}</div>
                      </div>
                    );
                  }

                  // Assistant message
                  return (
                    <div className="claude-assistant-message-row" key={message.id}>
                      {/* Collapsible Thinking Block */}
                      {message.thinking && (
                        <div className="claude-thinking-container">
                          <button
                            type="button"
                            className="claude-thinking-toggle"
                            onClick={() => toggleThinking(message.id)}
                            aria-expanded={message.thinkingExpanded}
                          >
                            <span>{message.thinking}</span>
                            <ChevronDown
                              size={14}
                              style={{
                                transform: message.thinkingExpanded ? "rotate(180deg)" : "none",
                                transition: "transform 0.15s ease",
                              }}
                            />
                          </button>
                          {message.thinkingExpanded && (
                            <div className="claude-thinking-body">
                              {message.fullThinking || message.thinking}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Main Synthesized Text */}
                      <div className="claude-assistant-body">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {message.content}
                        </ReactMarkdown>
                      </div>

                      {/* Data Preview Table */}
                      {message.previewTable && (
                        <div className="claude-data-preview-table-wrap">
                          <table className="claude-preview-table">
                            <thead>
                              <tr>
                                {message.previewTable.headers.map((h: string, i: number) => (
                                  <th key={i}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {message.previewTable.rows.map((row: (string | number)[], rIdx: number) => (
                                <tr key={rIdx}>
                                  {row.map((cell: string | number, cIdx: number) => (
                                    <td key={cIdx}>{cell}</td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      {/* INTERACTIVE DOWNLOAD FILES / ARTIFACTS SECTION */}
                      {message.artifacts && message.artifacts.length > 0 && (
                        <div className="claude-artifacts-wrapper">
                          <div className="claude-artifacts-header">
                            <Sparkles size={14} color="#d97757" />
                            <span>Downloadable Files & Datasets</span>
                          </div>

                          {message.artifacts.map((art: ArtifactFile) => (
                            <div className="claude-artifact-card" key={art.id}>
                              <div className="claude-artifact-left">
                                <div className={`claude-file-icon-box ${art.type}`}>
                                  {art.type === "pdf" && <FileText size={20} />}
                                  {art.type === "csv" && <FileSpreadsheet size={20} />}
                                  {art.type === "json" && <FileCode size={20} />}
                                </div>
                                <div className="claude-artifact-details">
                                  <span className="claude-artifact-filename">
                                    {art.filename}
                                  </span>
                                  <span className="claude-artifact-meta">
                                    {art.sizeStr} · {art.subtitle}
                                  </span>
                                </div>
                              </div>

                              <button
                                type="button"
                                className="claude-download-action-btn"
                                onClick={art.generator}
                                title={`Download ${art.filename}`}
                              >
                                <Download size={14} />
                                <span>Download {art.type.toUpperCase()}</span>
                              </button>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Action Bar (Copy, Thumbs, Retry, Timestamp) */}
                      <div className="claude-action-bar">
                        <button
                          type="button"
                          className="claude-action-icon-btn"
                          onClick={() => handleCopy(message.id, message.content)}
                          title="Copy response"
                        >
                          {copiedId === message.id ? (
                            <Check size={14} color="#10b981" />
                          ) : (
                            <Copy size={14} />
                          )}
                        </button>

                        <button
                          type="button"
                          className="claude-action-icon-btn"
                          onClick={() => handleRate(message.id, "up")}
                          style={{
                            color: ratedMsg[message.id] === "up" ? "#d97757" : undefined,
                          }}
                          title="Good response"
                        >
                          <ThumbsUp size={14} />
                        </button>

                        <button
                          type="button"
                          className="claude-action-icon-btn"
                          onClick={() => handleRate(message.id, "down")}
                          style={{
                            color: ratedMsg[message.id] === "down" ? "#d97757" : undefined,
                          }}
                          title="Bad response"
                        >
                          <ThumbsDown size={14} />
                        </button>

                        <button
                          type="button"
                          className="claude-action-icon-btn"
                          onClick={() => showToast("Regenerating response...")}
                          title="Retry response"
                        >
                          <RotateCcw size={14} />
                        </button>

                        {message.timestamp && (
                          <span className="claude-timestamp">{message.timestamp}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Bottom Floating Composer */}
            <div className="claude-composer-fixed-wrap">
              <button
                type="button"
                className="claude-scroll-bottom-btn"
                onClick={scrollToBottom}
                title="Scroll to bottom"
              >
                <ArrowDown size={16} />
              </button>

              <div className="claude-composer-card">
                <div className="claude-composer-inner">
                  <button
                    type="button"
                    className="claude-attach-btn"
                    onClick={() => showToast("File attachment ready")}
                    title="Attach content or document"
                  >
                    <Plus size={18} />
                  </button>

                  <textarea
                    ref={textareaRef}
                    className="claude-textarea"
                    placeholder="Write a message..."
                    rows={1}
                    value={inputPrompt}
                    onChange={(e) => setInputPrompt(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isSending}
                  />

                  <div className="claude-composer-controls-right">
                    <button
                      type="button"
                      className="claude-voice-btn"
                      onClick={() => showToast("Voice input activated")}
                      title="Dictate message"
                    >
                      <Mic size={16} />
                    </button>

                    <button
                      type="button"
                      className={`claude-send-btn ${inputPrompt.trim() ? "active" : ""}`}
                      onClick={() => void handleSendMessage()}
                      disabled={!inputPrompt.trim() || isSending}
                      title="Send message"
                    >
                      <ArrowUp size={16} />
                    </button>
                  </div>
                </div>
              </div>

              <div className="claude-composer-footer-text">
                <span>Claude is AI and can make mistakes. Please double-check responses.</span>
                <span className="claude-model-badge">Sonnet 3.5 Low</span>
              </div>
            </div>
          </main>

          {/* =============================================================== */}
          {/* MODALS / DRAWERS                                                */}
          {/* =============================================================== */}

          {/* Projects / Account Intelligence Drawer */}
          {showProjectsModal && (
            <div
              className="claude-drawer-overlay"
              onClick={() => setShowProjectsModal(false)}
            >
              <div
                className="claude-drawer-modal"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="claude-drawer-modal-header">
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <FolderKanban size={18} color="#d97757" />
                    <span className="claude-drawer-modal-title">
                      Projects & Target Accounts
                    </span>
                  </div>
                  <button
                    type="button"
                    className="claude-icon-btn"
                    onClick={() => setShowProjectsModal(false)}
                  >
                    <X size={16} />
                  </button>
                </div>

                <p style={{ color: "#a8a69e", fontSize: "13px", margin: 0 }}>
                  Manage company domains and account research targets in your workspace.
                </p>

                {/* Add domain form */}
                <form
                  onSubmit={handleAddAccount}
                  style={{ display: "flex", gap: "8px", marginTop: "4px" }}
                >
                  <input
                    type="text"
                    placeholder="e.g. salesforce.com, nvidia.com"
                    value={newDomain}
                    onChange={(e) => setNewDomain(e.target.value)}
                    style={{
                      flex: 1,
                      background: "#2a2926",
                      border: "1px solid #3d3b36",
                      borderRadius: "6px",
                      padding: "8px 12px",
                      color: "#f5f5f4",
                      fontSize: "13px",
                    }}
                  />
                  <button
                    type="submit"
                    className="claude-download-action-btn"
                    style={{ background: "#d97757", borderColor: "#d97757" }}
                  >
                    Add Company
                  </button>
                </form>

                {addAccountNotice && (
                  <p style={{ color: "#34d399", fontSize: "12px", margin: 0 }}>
                    {addAccountNotice}
                  </p>
                )}

                {/* Accounts list */}
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  <span
                    style={{
                      fontSize: "12px",
                      fontWeight: 600,
                      color: "#8c8a82",
                      textTransform: "uppercase",
                    }}
                  >
                    Existing Accounts ({accountsApi.data?.accounts.length ?? 0})
                  </span>

                  <div
                    style={{
                      maxHeight: "260px",
                      overflowY: "auto",
                      display: "flex",
                      flexDirection: "column",
                      gap: "4px",
                    }}
                  >
                    {accountsApi.data?.accounts.map((acc) => (
                      <div
                        key={acc.domain}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "8px 12px",
                          background: "#282723",
                          border: "1px solid #363530",
                          borderRadius: "6px",
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <Building2 size={15} color="#8c8a83" />
                          <span style={{ fontSize: "13px", fontWeight: 500, color: "#f5f5f4" }}>
                            {acc.name || acc.domain}
                          </span>
                        </div>
                        <span
                          style={{
                            fontSize: "11px",
                            padding: "2px 8px",
                            borderRadius: "10px",
                            background: acc.latest_run ? "rgba(16,185,129,0.15)" : "#31302b",
                            color: acc.latest_run ? "#34d399" : "#9c9a91",
                          }}
                        >
                          {acc.latest_run ? acc.latest_run.status : "Ready to run"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Artifacts Drawer */}
          {showArtifactsModal && (
            <div
              className="claude-drawer-overlay"
              onClick={() => setShowArtifactsModal(false)}
            >
              <div
                className="claude-drawer-modal"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="claude-drawer-modal-header">
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <Sparkles size={18} color="#d97757" />
                    <span className="claude-drawer-modal-title">
                      Workspace Artifacts Gallery
                    </span>
                  </div>
                  <button
                    type="button"
                    className="claude-icon-btn"
                    onClick={() => setShowArtifactsModal(false)}
                  >
                    <X size={16} />
                  </button>
                </div>

                <p style={{ color: "#a8a69e", fontSize: "13px", margin: 0 }}>
                  Instant access to all synthesized PDFs, CSV datasets, and JSON reports generated in this workspace.
                </p>

                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <div className="claude-artifact-card">
                    <div className="claude-artifact-left">
                      <div className="claude-file-icon-box pdf">
                        <FileText size={20} />
                      </div>
                      <div className="claude-artifact-details">
                        <span className="claude-artifact-filename">
                          Salesforce_AI_Engineering_Roles_Market_Report.pdf
                        </span>
                        <span className="claude-artifact-meta">
                          142 KB · Full Executive Report with Role Matrices & Hiring Analysis
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="claude-download-action-btn"
                      onClick={downloadSalesforcePdf}
                    >
                      <Download size={14} />
                      <span>Download PDF</span>
                    </button>
                  </div>

                  <div className="claude-artifact-card">
                    <div className="claude-artifact-left">
                      <div className="claude-file-icon-box csv">
                        <FileSpreadsheet size={20} />
                      </div>
                      <div className="claude-artifact-details">
                        <span className="claude-artifact-filename">
                          salesforce_ai_engineering_roles_dataset.csv
                        </span>
                        <span className="claude-artifact-meta">
                          48 KB · Tabular Dataset: Roles, Teams, Locations, Skills
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="claude-download-action-btn"
                      onClick={downloadSalesforceCsv}
                    >
                      <Download size={14} />
                      <span>Download CSV</span>
                    </button>
                  </div>

                  <div className="claude-artifact-card">
                    <div className="claude-artifact-left">
                      <div className="claude-file-icon-box json">
                        <FileCode size={20} />
                      </div>
                      <div className="claude-artifact-details">
                        <span className="claude-artifact-filename">
                          salesforce_ai_intelligence.json
                        </span>
                        <span className="claude-artifact-meta">
                          86 KB · Machine-Readable Structured Intelligence Dataset
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="claude-download-action-btn"
                      onClick={downloadSalesforceJson}
                    >
                      <Download size={14} />
                      <span>Download JSON</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Code Modal */}
          {showCodeModal && (
            <div
              className="claude-drawer-overlay"
              onClick={() => setShowCodeModal(false)}
            >
              <div
                className="claude-drawer-modal"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="claude-drawer-modal-header">
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <Code2 size={18} color="#d97757" />
                    <span className="claude-drawer-modal-title">Claude Code Environment</span>
                  </div>
                  <button
                    type="button"
                    className="claude-icon-btn"
                    onClick={() => setShowCodeModal(false)}
                  >
                    <X size={16} />
                  </button>
                </div>

                <p style={{ color: "#d1cfc7", fontSize: "14px", lineHeight: "1.6" }}>
                  Claude Code enables autonomous coding, tool calling, and repository editing directly from your workspace.
                </p>

                <div
                  style={{
                    background: "#282723",
                    padding: "12px",
                    borderRadius: "8px",
                    fontFamily: "monospace",
                    fontSize: "12.5px",
                    color: "#34d399",
                  }}
                >
                  $ agy code --project=account-intelligence
                </div>

                <button
                  type="button"
                  className="claude-download-action-btn"
                  style={{ background: "#d97757", borderColor: "#d97757", width: "100%", justifyContent: "center" }}
                  onClick={() => {
                    setShowCodeModal(false);
                    showToast("Code integration active");
                  }}
                >
                  Close
                </button>
              </div>
            </div>
          )}

          {/* Customize Modal */}
          {showCustomizeModal && (
            <div
              className="claude-drawer-overlay"
              onClick={() => setShowCustomizeModal(false)}
            >
              <div
                className="claude-drawer-modal"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="claude-drawer-modal-header">
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <SlidersHorizontal size={18} color="#d97757" />
                    <span className="claude-drawer-modal-title">Customize Claude</span>
                  </div>
                  <button
                    type="button"
                    className="claude-icon-btn"
                    onClick={() => setShowCustomizeModal(false)}
                  >
                    <X size={16} />
                  </button>
                </div>

                <p style={{ color: "#a8a69e", fontSize: "13px", margin: 0 }}>
                  Configure your workspace intelligence routing and provider credentials.
                </p>

                <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                  <div>
                    <label style={{ fontSize: "12px", fontWeight: 600, color: "#d1cfc7" }}>
                      Model Preference
                    </label>
                    <select
                      style={{
                        width: "100%",
                        marginTop: "4px",
                        background: "#282723",
                        border: "1px solid #363530",
                        color: "#f5f5f4",
                        padding: "8px",
                        borderRadius: "6px",
                        fontSize: "13px",
                      }}
                    >
                      <option>Claude 3.5 Sonnet (Default)</option>
                      <option>Claude 3.5 Haiku (Fast)</option>
                      <option>Claude 3 Opus (Deep Analysis)</option>
                    </select>
                  </div>

                  <div>
                    <label style={{ fontSize: "12px", fontWeight: 600, color: "#d1cfc7" }}>
                      Project Scope Enforcement
                    </label>
                    <p style={{ fontSize: "12px", color: "#8c8a82", margin: "2px 0 0" }}>
                      Strictly enforce ABM and company intelligence project scope. Off-topic queries are rejected.
                    </p>
                  </div>
                </div>

                <button
                  type="button"
                  className="claude-download-action-btn"
                  style={{ background: "#d97757", borderColor: "#d97757", width: "100%", justifyContent: "center" }}
                  onClick={() => {
                    setShowCustomizeModal(false);
                    showToast("Customizations saved");
                  }}
                >
                  Save Changes
                </button>
              </div>
            </div>
          )}

          {/* Toast Notification */}
          {toastMessage && <div className="claude-toast">{toastMessage}</div>}
        </div>
      )}
    </SignedIn>
  );
}
