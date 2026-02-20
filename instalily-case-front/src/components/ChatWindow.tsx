import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { useEffect, useRef, useState } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import { marked } from "marked";

import { cancelChatRun, streamAIMessage } from "../api/api";
import type { ChatMessage, Citation, ThinkingStep } from "../types/chat";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./ui/sheet";

interface ChatWindowProps {
  chatId: string;
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  onJumpToMessage?: MutableRefObject<((index: number) => void) | null>;
  onSourcesDrawerChange?: (open: boolean) => void;
}

interface ActiveRun {
  runId: string;
  controller: AbortController;
}

const sanitizeSnippetText = (text: string): string => {
  const raw = (text || "").trim();
  if (!raw) return "";
  return raw
    .replace(/!\[([^\]]*)\]\(([^)]*)\)/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]*)\)/g, "$1")
    .replace(/[`*_>#]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
};

export default function ChatWindow({
  messages,
  setMessages,
  onJumpToMessage,
  onSourcesDrawerChange,
  chatId,
}: ChatWindowProps) {
  const [input, setInput] = useState("");
  const [isSourcesOpen, setIsSourcesOpen] = useState(false);
  const [activeSources, setActiveSources] = useState<Citation[]>([]);
  const [collapsedThinking, setCollapsedThinking] = useState<Record<number, boolean>>({});

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const messageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const activeStreamRef = useRef<ActiveRun | null>(null);

  const extractDomains = (text: string): string[] => {
    const matches = text.match(/https?:\/\/[^\s)]+/g) || [];
    const seen = new Set<string>();
    const domains: string[] = [];
    for (const url of matches) {
      try {
        const host = new URL(url).hostname.replace(/^www\./, "");
        if (!seen.has(host)) {
          seen.add(host);
          domains.push(host);
        }
      } catch {
        continue;
      }
    }
    return domains;
  };

  const stopActiveRun = () => {
    if (!activeStreamRef.current) return;
    const { runId, controller } = activeStreamRef.current;
    controller.abort();
    void cancelChatRun(runId);
    activeStreamRef.current = null;
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (onJumpToMessage) {
      onJumpToMessage.current = (index: number) => {
        const node = messageRefs.current[index];
        if (!node) return;
        node.scrollIntoView({ behavior: "smooth", block: "center" });
        node.classList.add("message-highlight");
        setTimeout(() => node.classList.remove("message-highlight"), 2000);
      };
    }
  }, [onJumpToMessage]);

  useEffect(() => {
    onSourcesDrawerChange?.(isSourcesOpen);
  }, [isSourcesOpen, onSourcesDrawerChange]);

  useEffect(() => {
    stopActiveRun();
    setIsSourcesOpen(false);
    setActiveSources([]);
  }, [chatId]);

  useEffect(() => {
    return () => stopActiveRun();
  }, []);

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

  const buildSourceSample = (citation: Citation): string => {
    const cleaned = sanitizeSnippetText(citation?.snippet || "");
    return cleaned || "No snippet available.";
  };

  const buildSourceTitle = (citation: Citation, idx: number): string => {
    const title = (citation?.title || "").trim();
    const url = (citation?.url || "").trim();
    if (title) return title;
    if (url) return url;
    return `Source ${idx + 1}`;
  };

  const getAllCitations = (): Citation[] => {
    const byUrl = new Map<string, Citation>();
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

  const handleSend = async () => {
    const normalizedInput = input.trim();
    if (!normalizedInput) return;

    stopActiveRun();

    const thinkingStartedAt = Date.now();
    const userMessage: ChatMessage = { role: "user", content: normalizedInput };
    const assistantMessage: ChatMessage = {
      role: "assistant",
      content: "",
      thinking: "",
      thinkingSteps: [],
      thinkingDurationSeconds: null,
      thinkingDomains: extractDomains(normalizedInput),
      citations: [],
      intent: "general_parts_help",
      confidence: 0,
    };

    const newMessages = [...messages, userMessage, assistantMessage];
    const assistantIndex = newMessages.length - 1;
    setMessages(newMessages);
    setCollapsedThinking((prev) => ({ ...prev, [assistantIndex]: false }));

    const userQuery = normalizedInput;
    setInput("");

    const runId = `${chatId || "chat"}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const controller = new AbortController();
    activeStreamRef.current = { runId, controller };

    let answerText = "";
    let thinkingText = "";
    let thinkingSteps: ThinkingStep[] = [];
    let thoughtSecondsAtFirstToken: number | null = null;

    const updateAssistant = (extra: Partial<ChatMessage> = {}) => {
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
      await streamAIMessage(
        userQuery,
        newMessages,
        {
          onThinking: (chunk) => {
            if (!chunk) return;
            if (thinkingSteps.length === 0) {
              thinkingText += chunk;
            } else {
              let lastIdx = -1;
              for (let i = thinkingSteps.length - 1; i >= 0; i -= 1) {
                if ((thinkingSteps[i]?.status || "running") === "running") {
                  lastIdx = i;
                  break;
                }
              }
              if (lastIdx < 0) {
                thinkingSteps = [...thinkingSteps, { status: "running", text: "Thinking", detail: "" }];
                lastIdx = thinkingSteps.length - 1;
              }
              const curr = thinkingSteps[lastIdx] || { status: "running", text: "Thinking" };
              thinkingSteps = [
                ...thinkingSteps.slice(0, lastIdx),
                { ...curr, detail: `${curr.detail || ""}${chunk}` },
              ];
            }
            updateAssistant();
          },
          onThinkingStep: (evt) => {
            const text = (evt?.text || "").trim();
            if (!text) return;
            const status = evt?.status || "running";
            const domain = evt?.domain || "";
            const lastIdx = thinkingSteps.length - 1;
            const last = lastIdx >= 0 ? thinkingSteps[lastIdx] : null;

            if (status === "done" && last && (last.text || "") === text) {
              thinkingSteps = [
                ...thinkingSteps.slice(0, lastIdx),
                { ...last, status: "done", domain: last.domain || domain },
              ];
            } else if (
              status === "running" &&
              last &&
              (last.text || "") === text &&
              (last.status || "running") === "running"
            ) {
              thinkingSteps = [
                ...thinkingSteps.slice(0, lastIdx),
                { ...last, domain: last.domain || domain },
              ];
            } else {
              thinkingSteps = [...thinkingSteps, { status, text, domain, detail: "" }];
            }
            updateAssistant();
          },
          onToken: (chunk) => {
            if (answerText.length === 0 && chunk) {
              thoughtSecondsAtFirstToken = Math.max(1, Math.round((Date.now() - thinkingStartedAt) / 1000));
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
                  return new URL(url as string).hostname.replace(/^www\./, "");
                } catch {
                  return "";
                }
              })
              .filter(Boolean)
              .filter((domain, i, arr) => arr.indexOf(domain) === i) as string[];
            updateAssistant({
              intent: evt.intent || "general_parts_help",
              confidence: evt.confidence || 0,
              citations: Array.isArray(evt.citations) ? evt.citations : [],
              thinkingDomains: domains,
              thinkingDurationSeconds: seconds,
            });
            setCollapsedThinking((prev) => ({ ...prev, [assistantIndex]: true }));
          },
        },
        { runId, signal: controller.signal }
      );
    } catch (error) {
      if ((error as Error)?.name === "AbortError") return;
      updateAssistant({
        content: `Error connecting to backend stream: ${(error as Error).message}`,
        citations: [],
      });
    } finally {
      if (activeStreamRef.current?.runId === runId) {
        activeStreamRef.current = null;
      }
    }
  };

  return (
    <div className="relative flex h-full flex-1 flex-col bg-white">
      <header className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <h2 className="text-sm font-semibold text-slate-900">Chat</h2>
        <p className="text-xs text-slate-500">
          {messages.length > 0
            ? `${messages.filter((m) => m.role === "user").length} messages`
            : "Start a conversation"}
        </p>
      </header>

      <div className="scrollbar-thin flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-center">
            <h3 className="text-lg text-slate-600">Hi, what can i help you today</h3>
          </div>
        ) : (
          messages.map((message, index) => {
            const showThinking =
              message.role === "assistant" &&
              ((message.thinking || "").length > 0 || (message.thinkingSteps || []).length > 0);

            return (
              <div
                key={index}
                ref={(el) => {
                  messageRefs.current[index] = el;
                }}
                className={`flex max-w-[86%] flex-col ${
                  message.role === "user" ? "ml-auto items-end" : "mr-auto items-start"
                }`}
              >
                {showThinking ? (
                  <Collapsible.Root
                    open={!collapsedThinking[index]}
                    onOpenChange={(open) =>
                      setCollapsedThinking((prev) => ({
                        ...prev,
                        [index]: !open,
                      }))
                    }
                    className="mb-3 w-full"
                  >
                    <Collapsible.Trigger asChild>
                      <button className="text-left text-sm font-medium text-slate-500">
                        {message.thinkingDurationSeconds
                          ? `Thought for ${message.thinkingDurationSeconds} second${message.thinkingDurationSeconds === 1 ? "" : "s"}`
                          : "Thinking"}
                      </button>
                    </Collapsible.Trigger>

                    <Collapsible.Content className="mt-2 space-y-2">
                      {(message.thinkingDomains || []).length > 0 ? (
                        <div className="space-y-1">
                          {(message.thinkingDomains || []).map((d, i) => (
                            <p key={`${d}-${i}`} className="text-xs text-slate-400">
                              {d}
                            </p>
                          ))}
                        </div>
                      ) : null}

                      {(message.thinkingSteps || []).length > 0 ? (
                        <div className="space-y-2 border-l-2 border-slate-200 pl-3 text-sm text-slate-400">
                          {(message.thinkingSteps || []).map((step, i) => (
                            <div key={`step-${i}`} className="space-y-1">
                              <p className="text-slate-500">{step.text}</p>
                              {step.domain ? (
                                <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
                                  {step.domain}
                                </span>
                              ) : null}
                              {step.detail ? <p className="text-slate-400">{step.detail}</p> : null}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="border-l-2 border-slate-200 pl-3 text-sm text-slate-400">
                          {message.thinking}
                        </div>
                      )}
                    </Collapsible.Content>
                  </Collapsible.Root>
                ) : null}

                {message.content ? (
                  <div
                    className={`${
                      message.role === "user"
                        ? "rounded-3xl rounded-br-md bg-slate-200 px-5 py-3 text-base text-black"
                        : "w-full px-0 py-0"
                    }`}
                  >
                    {message.role === "assistant" ? (
                      <div
                        className="prose prose-slate prose-sm max-w-none"
                        dangerouslySetInnerHTML={{
                          __html: marked.parse(message.content) as string,
                        }}
                      />
                    ) : (
                      <div
                        className="whitespace-pre-wrap"
                        dangerouslySetInnerHTML={{
                          __html: marked.parseInline(message.content.replace(/\s+$/, "")) as string,
                        }}
                      />
                    )}
                  </div>
                ) : null}

                {message.role === "assistant" && Array.isArray(message.citations) && message.citations.length > 0 ? (
                  <div className="pt-2">
                    <Button
                      variant="outline"
                      className="h-9 rounded-full text-sm"
                      onClick={() => {
                        const citations = getAllCitations();
                        if (citations.length === 0) return;
                        setActiveSources(citations);
                        setIsSourcesOpen(true);
                      }}
                    >
                      {getAllCitations().length} web pages
                    </Button>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="flex gap-3 border-t border-slate-200 bg-white px-6 py-4">
        <Input
          className="h-12 flex-1"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleSend();
            }
          }}
        />
        <Button
          variant="secondary"
          className="h-12 rounded-lg px-6 text-sm font-medium text-black"
          onClick={() => {
            void handleSend();
          }}
        >
          Send
        </Button>
      </div>

      <Sheet open={isSourcesOpen} onOpenChange={setIsSourcesOpen}>
        <SheetContent side="right" className="w-full max-w-[360px] p-0 sm:max-w-[360px]">
          <SheetHeader className="border-b border-slate-200 px-4 py-3">
            <SheetTitle>Sources</SheetTitle>
          </SheetHeader>
          <div className="scrollbar-thin flex-1 space-y-3 overflow-y-auto p-3">
            {activeSources.map((citation, idx) => (
              <div key={`${citation.url || "source"}-${idx}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <a
                  className="text-sm font-semibold text-slate-800 hover:underline"
                  href={citation.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {buildSourceTitle(citation, idx)}
                </a>
                <p className="mt-1 break-all text-xs text-slate-400">{citation.url}</p>
                <p className="mt-2 text-sm text-slate-600">{buildSourceSample(citation)}</p>
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
