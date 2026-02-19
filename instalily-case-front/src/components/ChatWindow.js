import React, { useState, useEffect, useRef } from "react";
import "./ChatWindow.css";
import { getAIMessage } from "../api/api";
import { marked } from "marked";

function ChatWindow({ messages, setMessages, onJumpToMessage, onSourcesDrawerChange }) {
  const [input, setInput] = useState("");
  const [isSourcesOpen, setIsSourcesOpen] = useState(false);
  const [activeSources, setActiveSources] = useState([]);
  const [activeSourceContext, setActiveSourceContext] = useState("");
  const messagesEndRef = useRef(null);
  const messageRefs = useRef({});

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (input.trim() !== "") {
      const userMessage = { role: "user", content: input };
      const newMessages = [...messages, userMessage];
      setMessages(newMessages);
      setInput("");

      const aiMessage = await getAIMessage(input, newMessages);
      setMessages([...newMessages, aiMessage]);
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

  const buildSourceSample = (citation, context = "") => {
    const url = citation?.url || "";
    const title = citation?.title || "Source";
    if (citation?.snippet) {
      return citation.snippet;
    }
    const compactContext = (context || "").replace(/\s+/g, " ").trim();
    if (compactContext) {
      return compactContext.substring(0, 180) + (compactContext.length > 180 ? "..." : "");
    }
    if (url) {
      return url.replace(/^https?:\/\//, "");
    }
    return title;
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
            snippet: buildSourceSample(citation, msg.content || ""),
          });
        }
      });
    });
    return Array.from(byUrl.values());
  };

  const handleOpenSources = (message) => {
    const citations = getAllCitations();
    if (citations.length === 0) return;
    setActiveSources(citations);
    setActiveSourceContext(message.content || "");
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
                      onClick={() => handleOpenSources(message)}
                    >
                      <span className="sources-pill-icon">🔗</span>
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
                  {citation.title || `Source ${idx + 1}`}
                </a>
                <div className="source-url">{citation.url}</div>
                <div className="source-sample">
                  {buildSourceSample(citation, activeSourceContext)}
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
