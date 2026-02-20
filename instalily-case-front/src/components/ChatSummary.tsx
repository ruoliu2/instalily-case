import { useEffect, useMemo, useState } from "react";
import type { ChatMessage } from "../types/chat";

interface ChatSummaryProps {
  messages: ChatMessage[];
  onJumpToMessage: (index: number) => void;
  visible: boolean;
  isLocked?: boolean;
}

interface SummarySection {
  id: string;
  index: number;
  title: string;
}

const summarize = (text: string, max = 46): string => {
  const cleaned = (text || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return "Untitled section";
  return cleaned.length > max ? `${cleaned.slice(0, max)}...` : cleaned;
};

export default function ChatSummary({
  messages,
  onJumpToMessage,
  visible,
  isLocked = false,
}: ChatSummaryProps) {
  const sections = useMemo<SummarySection[]>(
    () =>
      messages
        .map((msg, index) => ({ ...msg, index }))
        .filter((msg) => msg.role === "user")
        .map((msg, idx) => ({
          id: `${msg.index}-${idx}`,
          index: msg.index,
          title: summarize(msg.content || ""),
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
    <div className="pointer-events-none absolute right-3 top-1/2 z-20 -translate-y-1/2">
      <div className="group relative flex items-center">
        <div className="h-28 w-[4px] rounded-full bg-slate-300" />

        <div
          className={`pointer-events-auto absolute right-2 top-1/2 w-80 -translate-y-1/2 rounded-3xl border border-slate-200 bg-white/95 p-3 shadow-soft transition-all duration-200 ${
            isLocked
              ? "pointer-events-none translate-x-3 opacity-0"
              : "translate-x-3 opacity-0 group-hover:translate-x-0 group-hover:opacity-100"
          }`}
        >
          {sections.length === 0 ? (
            <p className="text-sm text-slate-500">No sections yet</p>
          ) : (
            <div className="space-y-2">
              {sections.map((section, idx) => {
                const active = activeSectionId === section.id;
                return (
                  <div key={section.id}>
                    {idx > 0 ? <div className="mb-2 border-t border-slate-200" /> : null}
                    <button
                      className={`flex w-full items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-left text-sm transition ${
                        active ? "text-blue-600" : "text-slate-500 hover:text-slate-700"
                      }`}
                      onClick={() => {
                        setActiveSectionId(section.id);
                        onJumpToMessage(section.index);
                      }}
                      title={section.title}
                    >
                      <span className="truncate">{section.title}</span>
                      <span
                        className={`h-1 w-4 rounded-full ${
                          active ? "bg-blue-600" : "bg-slate-300"
                        }`}
                      />
                    </button>
                  </div>
                );
              })}
              <p className="pt-1 text-right text-xs text-slate-400">{sections.length} sections</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
