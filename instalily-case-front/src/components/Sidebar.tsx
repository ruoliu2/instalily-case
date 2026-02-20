import { Button } from "./ui/button";
import type { ChatMessage } from "../types/chat";

interface ChatItem {
  id: string;
  title: string;
  messages: ChatMessage[];
}

interface SidebarProps {
  chats: ChatItem[];
  activeChatId: string;
  onSelectChat: (chatId: string) => void;
  onNewChat: () => void;
  onDeleteChat: (chatId: string) => void;
}

export default function Sidebar({
  chats,
  activeChatId,
  onSelectChat,
  onNewChat,
  onDeleteChat,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="p-4">
        <Button variant="secondary" className="w-full gap-2" onClick={onNewChat}>
          <span className="text-base leading-none">+</span>
          New Chat
        </Button>
      </div>

      <div className="scrollbar-thin flex-1 space-y-1 overflow-y-auto px-3 pb-3">
        {chats.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">
            No conversations yet
          </div>
        ) : (
          chats.map((chat) => {
            const isActive = chat.id === activeChatId;
            return (
              <div
                key={chat.id}
                className={`group flex items-center gap-2 rounded-xl border px-3 py-2 transition ${
                  isActive
                    ? "border-slate-300 bg-slate-100"
                    : "border-transparent hover:border-slate-200 hover:bg-slate-50"
                }`}
                onClick={() => onSelectChat(chat.id)}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-800">{chat.title}</p>
                </div>
                <button
                  className="rounded-md px-1.5 py-0.5 text-slate-400 opacity-0 transition hover:bg-slate-200 hover:text-slate-700 group-hover:opacity-100"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteChat(chat.id);
                  }}
                  title="Delete chat"
                >
                  ×
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
