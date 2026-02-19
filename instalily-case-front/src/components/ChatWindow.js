import React, { useState, useEffect, useRef } from "react";
import "./ChatWindow.css";
import { streamAIMessage } from "../api/api";
import { marked } from "marked";

function ChatWindow({ messages, setMessages, onJumpToMessage, onSourcesDrawerChange, chatId }) {
  const [input, setInput] = useState("");
  const [isSourcesOpen, setIsSourcesOpen] = useState(false);
  const [activeSources, setActiveSources] = useState([]);
  const [collapsedThinking, setCollapsedThinking] = useState({});
  const messagesEndRef = useRef(null);
  const messageRefs = useRef({});

  const extractDomains = (text) => {
    const matches = (text || "").match(/https?:\/\/[^\s)]+/g) || [];
    const seen = new Set();
    const domains = [];
    for (const url of matches) {
      try {
        const host = new URL(url).hostname.replace(/^www\./, "");
        if (!seen.has(host)) {
          seen.add(host);
          domains.push(host);
        }
      } catch (err) {
        continue;
      }
    }
    return domains;
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (input.trim() !== "") {
      const thinkingStartedAt = Date.now();
      const userMessage = { role: "user", content: input };
      const assistantMessage = {
        role: "assistant",
        content: "",
        thinking: "",
        thinkingSteps: [],
        thinkingDurationSeconds: null,
        thinkingDomains: extractDomains(input),
        citations: [],
        intent: "general_parts_help",
        confidence: 0,
      };
      const newMessages = [...messages, userMessage, assistantMessage];
      const assistantIndex = newMessages.length - 1;
      setMessages(newMessages);
      const userQuery = input;
      setInput("");
      let answerText = "";
      let thinkingText = "";
      let thinkingSteps = [];
      let thoughtSecondsAtFirstToken = null;

      const updateAssistant = (extra = {}) => {
        setMessages((prev) => {
          const next = [...prev];
          if (!next[assistantIndex] || next[assistantIndex].role !== "assistant") return next;
          next[assistantIndex] = {
            ...next[assistantIndex],
            content: answerText,
            thinking: thinkingText,
            thinkingSteps,
            ...extra,
          };
          return next;
        });
      };

      try {
        await streamAIMessage(userQuery, newMessages, {
          onThinking: (chunk) => {
            if (!chunk) return;
            if (thinkingSteps.length === 0) {
              thinkingText += chunk;
            } else {
              const lastIdx = thinkingSteps.length - 1;
              const curr = thinkingSteps[lastIdx] || { status: "running", text: "Thinking", domain: "" };
              thinkingSteps = [
                ...thinkingSteps.slice(0, lastIdx),
                {
                  ...curr,
                  detail: (curr.detail || "") + chunk,
                },
              ];
            }
            updateAssistant();
          },
          onThinkingStep: (evt) => {
            const text = (evt?.text || "").trim();
            if (!text) return;
            thinkingSteps = [
              ...thinkingSteps,
              {
                status: evt?.status || "running",
                text,
                domain: evt?.domain || "",
                detail: "",
              },
            ];
            updateAssistant();
          },
          onToken: (chunk) => {
            if (answerText.length === 0 && chunk) {
              thoughtSecondsAtFirstToken = Math.max(
                1,
                Math.round((Date.now() - thinkingStartedAt) / 1000)
              );
              setCollapsedThinking((prev) => ({ ...prev, [assistantIndex]: true }));
              updateAssistant({ thinkingDurationSeconds: thoughtSecondsAtFirstToken });
            }
            answerText += chunk || "";
            updateAssistant();
          },
          onDone: (evt) => {
            const seconds = answerText.length > 0 ? thoughtSecondsAtFirstToken : null;
            const domains = (Array.isArray(evt.citations) ? evt.citations : [])
              .map((c) => c?.url)
              .filter(Boolean)
              .map((url) => {
                try {
                  return new URL(url).hostname.replace(/^www\./, "");
                } catch (err) {
                  return "";
                }
              })
              .filter(Boolean)
              .filter((domain, i, arr) => arr.indexOf(domain) === i);
            updateAssistant({
              intent: evt.intent || "general_parts_help",
              confidence: evt.confidence || 0,
              citations: Array.isArray(evt.citations) ? evt.citations : [],
              thinkingDomains: domains,
              thinkingDurationSeconds: seconds,
            });
          },
        });
      } catch (error) {
        updateAssistant({
          content: `Error connecting to backend stream: ${error.message}`,
          citations: [],
        });
      }
    }
  };

  const scrollToMessage = (index) => {
    if (messageRefs.current[index]) {
      messageRefs.current[index].scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      messageRefs.current[index].classList.add("highlight");
      setTimeout(() => {
        messageRefs.current[index]?.classList.remove("highlight");
      }, 2000);
    }
  };

  useEffect(() => {
    if (onJumpToMessage) {
      onJumpToMessage.current = scrollToMessage;
    }
  }, [onJumpToMessage]);

  useEffect(() => {
    if (onSourcesDrawerChange) {
      onSourcesDrawerChange(isSourcesOpen);
    }
  }, [isSourcesOpen, onSourcesDrawerChange]);

  useEffect(() => {
    setIsSourcesOpen(false);
    setActiveSources([]);
  }, [chatId]);

  useEffect(() => {
    setCollapsedThinking((prev) => {
      const next = { ...prev };
      messages.forEach((m, i) => {
        if (m.role === "assistant" && m.thinking && next[i] === undefined) {
          next[i] = false;
        }
      });
      return next;
    });
  }, [messages]);

  const sanitizeSnippetText = (text) => {
    const raw = (text || "").trim();
    if (!raw) return "";
    let s = raw;
    s = s.replace(/!\[([^\]]*)\]\(([^)]*)\)/g, "$1");
    s = s.replace(/\[([^\]]+)\]\(([^)]*)\)/g, "$1");
    s = s.replace(/[`*_>#]+/g, " ");
    s = s.replace(/\s+/g, " ").trim();
    return s;
  };

  const buildSourceSample = (citation) => {
    if (citation?.snippet) {
      const cleaned = sanitizeSnippetText(citation.snippet);
      if (cleaned) return cleaned;
    }
    return "No snippet available.";
  };

  const buildSourceTitle = (citation, idx) => {
    const title = (citation?.title || "").trim();
    const url = (citation?.url || "").trim();
    if (title) {
      return title;
    }
    if (url) return url;
    return `Source ${idx + 1}`;
  };

  const getAllCitations = () => {
    const byUrl = new Map();
    messages.forEach((msg) => {
      if (!Array.isArray(msg.citations)) return;
      msg.citations.forEach((citation) => {
        if (!citation?.url) return;
        if (!byUrl.has(citation.url)) {
          byUrl.set(citation.url, {
            ...citation,
            snippet: buildSourceSample(citation),
          });
        }
      });
    });
    return Array.from(byUrl.values());
  };

  const handleOpenSources = () => {
    const citations = getAllCitations();
    if (citations.length === 0) return;
    setActiveSources(citations);
    setIsSourcesOpen(true);
  };

  return (
    <div className="chat-window">
      <div className="chat-header">
        <div className="chat-title">Chat</div>
        <div className="chat-status">
          {messages.length > 0
            ? `${messages.filter((m) => m.role === "user").length} messages`
            : "Start a conversation"}
        </div>
      </div>
      <div className="messages-container">
        {messages.length === 0 ? (
          <div className="empty-chat">
            <h3>Hi, what can i help you today</h3>
          </div>
        ) : (
          messages.map((message, index) => (
            <div
              key={index}
              ref={(el) => (messageRefs.current[index] = el)}
              className={`${message.role}-message-container message-wrapper`}
            >
              {message.role === "assistant" &&
                (message.thinking ||
                  (Array.isArray(message.thinkingSteps) &&
                    message.thinkingSteps.length > 0)) && (
                <div className="thinking-panel">
                  <button
                    className="thinking-meta-row thinking-toggle"
                    onClick={() =>
                      setCollapsedThinking((prev) => ({
                        ...prev,
                        [index]: !prev[index],
                      }))
                    }
                  >
                    <div className="thinking-title">
                      {message.thinkingDurationSeconds
                        ? `Thought for ${message.thinkingDurationSeconds} second${
                            message.thinkingDurationSeconds === 1 ? "" : "s"
                          }`
                        : "Thinking"}
                    </div>
                  </button>
                  {!collapsedThinking[index] && (
                    <>
                      <div className="thinking-domain-list">
                        {(message.thinkingDomains || []).map((d, i) => (
                          <div className="thinking-domain" key={`${d}-${i}`}>
                            {d}
                          </div>
                        ))}
                      </div>
                      {Array.isArray(message.thinkingSteps) &&
                      message.thinkingSteps.length > 0 ? (
                        <div className="thinking-step-list">
                          {message.thinkingSteps.map((step, i) => (
                            <div className="thinking-step-row" key={`step-${i}`}>
                              <div className={`thinking-step-icon thinking-step-${step.status || "running"}`}>
                                {step.status === "done" ? "✓" : ""}
                              </div>
                              <div className="thinking-step-content">
                                <div className="thinking-step-text">{step.text}</div>
                                {step.domain ? (
                                  <div className="thinking-domain-pill">{step.domain}</div>
                                ) : null}
                                {step.detail ? (
                                  <div className="thinking-step-detail">{step.detail}</div>
                                ) : null}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="thinking-body">{message.thinking}</div>
                      )}
                    </>
                  )}
                </div>
              )}
              {message.content && (
                <div className={`message ${message.role}-message`}>
                  <div
                    dangerouslySetInnerHTML={{
                      __html: marked(message.content),
                    }}
                  ></div>
                </div>
              )}
              {message.role === "assistant" &&
                Array.isArray(message.citations) &&
                message.citations.length > 0 && (
                  <div className="message-followup message-actions source-pill-row">
                    <button
                      className="sources-pill-btn"
                      onClick={handleOpenSources}
                    >
                      <span>{getAllCitations().length} web pages</span>
                    </button>
                  </div>
                )}
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>
      <div className="input-area">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          onKeyPress={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              handleSend();
              e.preventDefault();
            }
          }}
        />
        <button className="send-button" onClick={handleSend}>
          Send
        </button>
      </div>
      {isSourcesOpen && (
        <aside className="sources-drawer">
          <div className="sources-drawer-header">
            <div className="sources-drawer-title">Sources</div>
            <button
              className="sources-close-btn"
              onClick={() => setIsSourcesOpen(false)}
              aria-label="Close sources"
            >
              ✕
            </button>
          </div>
          <div className="sources-drawer-content">
            {activeSources.map((citation, idx) => (
              <div key={`${citation.url || "source"}-${idx}`} className="source-card">
                <a
                  className="source-link"
                  href={citation.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {buildSourceTitle(citation, idx)}
                </a>
                <div className="source-url">{citation.url}</div>
                <div className="source-sample">
                  {buildSourceSample(citation)}
                </div>
              </div>
            ))}
          </div>
        </aside>
      )}
    </div>
  );
}

export default ChatWindow;
