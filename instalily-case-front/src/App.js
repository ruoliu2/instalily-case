import React, { useState, useRef, useEffect } from "react";
import "./App.css";
import ChatWindow from "./components/ChatWindow";
import Sidebar from "./components/Sidebar";
import ChatSummary from "./components/ChatSummary";

function App() {
  const [chats, setChats] = useState([
    { id: "1", title: "Welcome Chat", messages: [] }
  ]);
  const [activeChatId, setActiveChatId] = useState("1");
  const [showSummary, setShowSummary] = useState(false);
  const [isSourcesOpen, setIsSourcesOpen] = useState(false);
  const jumpToMessageRef = useRef(null);
  const lastNewChatClickAtRef = useRef(0);

  const activeChat = chats.find((chat) => chat.id === activeChatId);
  const activeMessages = activeChat ? activeChat.messages : [];

  useEffect(() => {
    if (activeMessages.length > 0) {
      setShowSummary(true);
    } else {
      setShowSummary(false);
    }
  }, [activeMessages.length]);

  const summarizeTitle = (text) => {
    const cleaned = (text || "").replace(/\s+/g, " ").trim();
    if (!cleaned) return "";
    const maxLength = 30;
    let title = cleaned;
    if (title.length > maxLength) {
      title = title.substring(0, maxLength) + "...";
    }
    return title;
  };

  const generateChatTitleFromMessages = (messages = []) => {
    const firstUserMessage = messages.find((m) => m.role === "user" && m.content);
    if (firstUserMessage) {
      return summarizeTitle(firstUserMessage.content) || "New Chat";
    }
    const firstAssistantMessage = messages.find(
      (m) => m.role === "assistant" && m.content
    );
    if (firstAssistantMessage) {
      return summarizeTitle(firstAssistantMessage.content) || "New Chat";
    }
    return "New Chat";
  };

  const maybeFinalizeChatTitle = (chatId) => {
    setChats((prevChats) =>
      prevChats.map((chat) => {
        if (chat.id !== chatId) return chat;
        if (chat.title !== "Welcome Chat" && chat.title !== "New Chat") return chat;
        if (!chat.messages || chat.messages.length === 0) return chat;
        return {
          ...chat,
          title: generateChatTitleFromMessages(chat.messages),
        };
      })
    );
  };

  const handleSetMessages = (newMessages) => {
    const messages = typeof newMessages === "function" ? newMessages(activeMessages) : newMessages;
    
    setChats((prevChats) =>
      prevChats.map((chat) => {
        if (chat.id === activeChatId) {
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
