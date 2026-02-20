export type ChatRole = "user" | "assistant";

export interface Citation {
  url: string;
  title?: string;
  snippet?: string;
}

export interface ThinkingStep {
  status?: "running" | "done" | "cancelled" | string;
  text: string;
  domain?: string;
  detail?: string;
}

export interface ChatMessage {
  role: ChatRole;
  content: string;
  thinking?: string;
  thinkingSteps?: ThinkingStep[];
  thinkingDurationSeconds?: number | null;
  thinkingDomains?: string[];
  citations?: Citation[];
  intent?: string;
  confidence?: number;
}

export interface StreamDoneEvent {
  type: "done";
  status?: string;
  intent?: string;
  confidence?: number;
  citations?: Citation[];
  traces?: Array<Record<string, unknown>>;
}
