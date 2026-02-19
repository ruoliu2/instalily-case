const API_BASE_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

export const getAIMessage = async (userQuery, conversationHistory = []) => {
  try {
    const response = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ 
        message: userQuery,
        history: conversationHistory
      }),
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    const data = await response.json();

    return {
      role: "assistant",
      content: data.answer,
      intent: data.intent,
      confidence: data.confidence,
      citations: Array.isArray(data.citations) ? data.citations : [],
    };
  } catch (error) {
    console.error("API call failed:", error);
    return {
      role: "assistant",
      content: `Error connecting to backend. Make sure the backend is running at ${API_BASE_URL}\n\nError: ${error.message}`,
      citations: [],
    };
  }
};

export const streamAIMessage = async (
  userQuery,
  conversationHistory = [],
  handlers = {}
) => {
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message: userQuery,
      history: conversationHistory,
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
      let evt = null;
      try {
        evt = JSON.parse(raw);
      } catch (err) {
        continue;
      }
      if (!evt || !evt.type) continue;
      if (evt.type === "thinking_step" && handlers.onThinkingStep) {
        handlers.onThinkingStep(evt);
      }
      if ((evt.type === "thinking" || evt.type === "thinking_token") && handlers.onThinking) {
        handlers.onThinking(evt.content || "");
      }
      if (evt.type === "token" && handlers.onToken) handlers.onToken(evt.content || "");
      if (evt.type === "done" && handlers.onDone) handlers.onDone(evt);
    }
  }
};

export const summarizeChatTitle = async (conversationHistory = []) => {
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

    const data = await response.json();
    return (data?.title || "").trim() || "New Chat";
  } catch (error) {
    console.error("Title API call failed:", error);
    return "New Chat";
  }
};
