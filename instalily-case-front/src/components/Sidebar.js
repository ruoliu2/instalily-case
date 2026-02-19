import React from "react";
import "./Sidebar.css";

function Sidebar({ chats, activeChatId, onSelectChat, onNewChat, onDeleteChat }) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <button className="new-chat-btn" onClick={onNewChat}>
          <span className="plus-icon">+</span>
          New Chat
        </button>
      </div>
      <div className="chat-list">
        {chats.length === 0 ? (
          <div className="no-chats">No conversations yet</div>
        ) : (
          chats.map((chat) => (
            <div
              key={chat.id}
              className={`chat-item ${chat.id === activeChatId ? "active" : ""}`}
              onClick={() => onSelectChat(chat.id)}
            >
              <div className="chat-item-content">
                <span className="chat-title">{chat.title}</span>
              </div>
              <button
                className="delete-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteChat(chat.id);
                }}
                title="Delete chat"
              >
                ×
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default Sidebar;
