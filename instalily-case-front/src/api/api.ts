import type { ChatMessage, Citation, StreamDoneEvent, ThinkingStep } from "../types/chat";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ChatApiResponse {
  answer: string;
  intent?: string;
  confidence?: number;
  citations?: Citation[];
}

interface StreamHandlers {
  onThinking?: (chunk: string) => void;
  onThinkingStep?: (step: ThinkingStep) => void;
  onToken?: (chunk: string) => void;
  onDone?: (evt: StreamDoneEvent) => void;
}

interface StreamOptions {
  runId?: string;
  signal?: AbortSignal;
}

export const getAIMessage = async (
  userQuery: string,
  conversationHistory: ChatMessage[] = []
): Promise<ChatMessage> => {
  try {
    const response = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: userQuery,
        history: conversationHistory,
      }),
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    const data = (await response.json()) as ChatApiResponse;

    return {
      role: "assistant",
      content: data.answer,
      intent: data.intent,
      confidence: data.confidence,
      citations: Array.isArray(data.citations) ? data.citations : [],
    };
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return {
      role: "assistant",
      content: `Error connecting to backend. Make sure the backend is running at ${API_BASE_URL}\n\nError: ${msg}`,
      citations: [],
    };
  }
};

export const streamAIMessage = async (
  userQuery: string,
  conversationHistory: ChatMessage[] = [],
  handlers: StreamHandlers = {},
  options: StreamOptions = {}
): Promise<void> => {
  const runId = options.runId || "";
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    signal: options.signal,
    body: JSON.stringify({
      message: userQuery,
      history: conversationHistory,
      run_id: runId,
    }),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Stream API error: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const raw = line.trim();
      if (!raw) continue;
      let evt: Record<string, unknown> | null = null;
      try {
        evt = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        continue;
      }
      if (!evt?.type) continue;

      if (evt.type === "thinking_step" && handlers.onThinkingStep) {
        handlers.onThinkingStep({
          status: String(evt.status || "running"),
          text: String(evt.text || ""),
          domain: String(evt.domain || ""),
          detail: "",
        });
      }
      if ((evt.type === "thinking" || evt.type === "thinking_token") && handlers.onThinking) {
        handlers.onThinking(String(evt.content || ""));
      }
      if (evt.type === "token" && handlers.onToken) handlers.onToken(String(evt.content || ""));
      if (evt.type === "done" && handlers.onDone) handlers.onDone(evt as unknown as StreamDoneEvent);
    }
  }
};

export const cancelChatRun = async (runId: string): Promise<{ ok: boolean; status: string }> => {
  const rid = (runId || "").trim();
  if (!rid) return { ok: false, status: "ignored" };
  try {
    const response = await fetch(`${API_BASE_URL}/chat/cancel`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ run_id: rid }),
    });
    if (!response.ok) {
      return { ok: false, status: `http_${response.status}` };
    }
    const data = (await response.json()) as { ok?: boolean; status?: string };
    return { ok: Boolean(data.ok), status: String(data.status || "unknown") };
  } catch {
    return { ok: false, status: "network_error" };
  }
};

export const summarizeChatTitle = async (conversationHistory: ChatMessage[] = []): Promise<string> => {
  try {
    const response = await fetch(`${API_BASE_URL}/chat/title`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        history: conversationHistory,
      }),
    });

    if (!response.ok) {
      throw new Error(`Title API error: ${response.status}`);
    }

    const data = (await response.json()) as { title?: string };
    return (data?.title || "").trim() || "New Chat";
  } catch {
    return "New Chat";
  }
};
