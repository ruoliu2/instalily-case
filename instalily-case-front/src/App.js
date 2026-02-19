import React, { useState, useRef, useEffect } from "react";
import "./App.css";
import ChatWindow from "./components/ChatWindow";
import Sidebar from "./components/Sidebar";
import ChatSummary from "./components/ChatSummary";
import { summarizeChatTitle } from "./api/api";

function App() {
  const [chats, setChats] = useState([
    { id: "1", title: "Welcome Chat", messages: [] }
  ]);
  const [activeChatId, setActiveChatId] = useState("1");
  const [showSummary, setShowSummary] = useState(false);
  const [isSourcesOpen, setIsSourcesOpen] = useState(false);
  const jumpToMessageRef = useRef(null);
  const lastNewChatClickAtRef = useRef(0);
  const chatsRef = useRef(chats);

  useEffect(() => {
    chatsRef.current = chats;
  }, [chats]);

  const activeChat = chats.find((chat) => chat.id === activeChatId);
  const activeMessages = activeChat ? activeChat.messages : [];

  useEffect(() => {
    if (activeMessages.length > 0) {
      setShowSummary(true);
    } else {
      setShowSummary(false);
    }
  }, [activeMessages.length]);

  const localFallbackTitle = (messages = []) => {
    const first = messages.find((m) => (m?.content || "").trim())?.content || "";
    const cleaned = first.replace(/\s+/g, " ").trim();
    if (!cleaned) return "New Chat";
    return cleaned.slice(0, 36) + (cleaned.length > 36 ? "..." : "");
  };

  const maybeFinalizeChatTitle = async (chatId) => {
    const chat = chatsRef.current.find((c) => c.id === chatId);
    if (!chat) return;
    if (chat.title !== "Welcome Chat" && chat.title !== "New Chat") return;
    if (!Array.isArray(chat.messages) || chat.messages.length === 0) return;

    const llmTitle = await summarizeChatTitle(chat.messages);
    const title =
      llmTitle && llmTitle !== "New Chat"
        ? llmTitle
        : localFallbackTitle(chat.messages);
    setChats((prevChats) =>
      prevChats.map((c) => {
        if (c.id !== chatId) return c;
        if (c.title !== "Welcome Chat" && c.title !== "New Chat") return c;
        if (!Array.isArray(c.messages) || c.messages.length === 0) return c;
        return { ...c, title };
      })
    );
  };

  const handleSetMessages = (newMessages) => {
    setChats((prevChats) =>
      prevChats.map((chat) => {
        if (chat.id === activeChatId) {
          const currentMessages = Array.isArray(chat.messages) ? chat.messages : [];
          const messages =
            typeof newMessages === "function"
              ? newMessages(currentMessages)
              : newMessages;
          return { ...chat, messages };
        }
        return chat;
      })
    );
  };

  const handleSelectChat = (chatId) => {
    if (chatId !== activeChatId) {
      maybeFinalizeChatTitle(activeChatId);
    }
    setActiveChatId(chatId);
  };

  const handleNewChat = () => {
    const now = Date.now();
    if (now - lastNewChatClickAtRef.current < 300) {
      return;
    }
    lastNewChatClickAtRef.current = now;

    maybeFinalizeChatTitle(activeChatId);
    const newId = createChatId();
    const newChat = {
      id: newId,
      title: "New Chat",
      messages: []
    };
    setChats((prevChats) => [newChat, ...prevChats]);
    setActiveChatId(newId);
  };

  const handleDeleteChat = (chatId) => {
    setChats((prevChats) => {
      const filtered = prevChats.filter((chat) => chat.id !== chatId);
      if (filtered.length === 0) {
        const newId = createChatId();
        filtered.push({ id: newId, title: "New Chat", messages: [] });
        setActiveChatId(newId);
      } else if (chatId === activeChatId) {
        setActiveChatId(filtered[0].id);
      }
      return filtered;
    });
  };

  const handleJumpToMessage = (index) => {
    if (jumpToMessageRef.current) {
      jumpToMessageRef.current(index);
    }
  };

  return (
    <div className="app-container">
      <Sidebar
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={handleSelectChat}
        onNewChat={handleNewChat}
        onDeleteChat={handleDeleteChat}
      />
      <ChatWindow
        chatId={activeChatId}
        messages={activeMessages}
        setMessages={handleSetMessages}
        onJumpToMessage={jumpToMessageRef}
        onSourcesDrawerChange={setIsSourcesOpen}
      />
      <ChatSummary
        messages={activeMessages}
        onJumpToMessage={handleJumpToMessage}
        visible={showSummary}
        isLocked={isSourcesOpen}
      />
    </div>
  );
}

function createChatId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

export default App;
