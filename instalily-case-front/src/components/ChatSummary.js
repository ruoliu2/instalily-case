import React, { useEffect, useMemo, useState } from "react";
import "./ChatSummary.css";

function ChatSummary({ messages, onJumpToMessage, visible, isLocked = false }) {
  const summarize = (text, max = 46) => {
    const cleaned = (text || "").replace(/\s+/g, " ").trim();
    if (!cleaned) return "Untitled section";
    return cleaned.length > max ? `${cleaned.slice(0, max)}...` : cleaned;
  };

  const sections = useMemo(
    () =>
      messages
        .map((msg, index) => ({ ...msg, index }))
        .filter((msg) => msg.role === "user")
        .map((msg, idx) => ({
          id: `${msg.index}-${idx}`,
          index: msg.index,
          title: summarize(msg.content),
        })),
    [messages]
  );
  const [activeSectionId, setActiveSectionId] = useState("");

  useEffect(() => {
    if (sections.length === 0) {
      setActiveSectionId("");
      return;
    }
    if (!sections.find((s) => s.id === activeSectionId)) {
      setActiveSectionId(sections[sections.length - 1].id);
    }
  }, [sections, activeSectionId]);

  if (!visible) return null;

  return (
    <div className={`chat-summary ${isLocked ? "locked" : ""}`}>
      <div className="summary-rail" />
      <div className="summary-panel">
        <div className="summary-content">
          {sections.length === 0 ? null : (
            <div className="summary-card">
              {sections.map((section, idx) => (
                <div key={section.id} className="summary-row-wrap">
                  {idx > 0 && <div className="summary-divider" />}
                  <button
                    className={`summary-item ${
                      activeSectionId === section.id ? "active" : ""
                    }`}
                    onClick={() => {
                      setActiveSectionId(section.id);
                      onJumpToMessage(section.index);
                    }}
                    title={section.title}
                  >
                    <span className="summary-text">{section.title}</span>
                    <span className="summary-indicator" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {sections.length === 0 ? (
            <div className="no-summary">No sections yet</div>
          ) : null}
          {sections.length > 0 ? (
            <div className="summary-count">{sections.length} sections</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default ChatSummary;
