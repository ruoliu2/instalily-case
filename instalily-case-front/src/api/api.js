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
