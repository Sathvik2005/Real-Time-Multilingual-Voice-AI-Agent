import { useEffect, useRef, useState, KeyboardEvent } from 'react';
import './ChatPanel.css';
import { useAgentStore } from '../../store/agentStore';
import type { AgentStatus, ChatMessage } from '../../types';

interface ChatPanelProps {
  status: AgentStatus;
  onSendText: (text: string) => void;
  patientName: string;
}

function formatTime(date: Date | string): string {
  const d = date instanceof Date ? date : new Date(date);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className={`message message--${msg.role}`}>
      <div className="message__role">{msg.role === 'user' ? 'You' : 'Assistant'}</div>
      <div className="message__bubble">{msg.text}</div>
      <div className="message__time">{formatTime(msg.timestamp)}</div>
    </div>
  );
}

function SendIcon() {
  return (
    <svg className="chat-panel__send-icon" viewBox="0 0 24 24" aria-hidden="true">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

export function ChatPanel({ status, onSendText }: ChatPanelProps) {
  const { messages } = useAgentStore();
  const [inputText, setInputText] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isDisabled = status === 'idle' || status === 'connecting' || status === 'processing';

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  function handleSend() {
    const trimmed = inputText.trim();
    if (!trimmed || isDisabled) return;
    onSendText(trimmed);
    setInputText('');
    inputRef.current?.focus();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="chat-panel">
      {/* Header */}
      <div className="chat-panel__header">
        <span className="chat-panel__header-title">Conversation</span>
      </div>

      {/* Message list */}
      <div className="chat-panel__messages" role="log" aria-live="polite" aria-label="Conversation">
        {messages.length === 0 && (
          <div className="chat-panel__empty">
            Your conversation will appear here.
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Text input */}
      <div className="chat-panel__input-row">
        <textarea
          ref={inputRef}
          className="chat-panel__input"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isDisabled ? 'Waiting...' : 'Type a message or use voice input...'}
          disabled={isDisabled}
          rows={1}
          aria-label="Message input"
        />
        <button
          type="button"
          className="chat-panel__send-btn"
          onClick={handleSend}
          disabled={isDisabled || !inputText.trim()}
          aria-label="Send message"
        >
          <SendIcon />
          Send
        </button>
      </div>
    </div>
  );
}
