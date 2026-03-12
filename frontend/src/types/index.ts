// Global type definitions for the Voice AI Clinic Agent

export type AgentStatus =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'recording'
  | 'processing'
  | 'speaking'
  | 'error';

export type MessageRole = 'user' | 'agent' | 'system';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  timestamp: Date;
  language?: string;
  isStreaming?: boolean;
}

export interface AppointmentSummary {
  appointment_id: number;
  doctor_name: string;
  specialization: string;
  time: string;
  status: string;
}

export interface DoctorRecord {
  doctor_id: number;
  name: string;
  specialization: string;
  languages: string[];
}

export interface SlotRecord {
  slot_id: number;
  start_time: string;
  end_time: string;
  display: string;
}

export interface LanguageInfo {
  code: string;
  name: string;
}

export interface LatencyMetrics {
  asr_ms?: number;
  llm_ms?: number;
  tts_ms?: number;
  total_ms?: number;
}

export interface ToolCallEntry {
  tool: string;
  args: Record<string, unknown>;
  id?: string;
}

// WebSocket message types (server → client)
export type ServerMessageType =
  | 'session_ready'
  | 'transcript'
  | 'language_detected'
  | 'agent_text'
  | 'audio_chunk'
  | 'audio_end'
  | 'latency_metrics'
  | 'tool_calls'
  | 'error'
  | 'pong';

export interface ServerMessage {
  type: ServerMessageType;
  [key: string]: unknown;
}

// WebSocket message types (client → server)
export type ClientMessageType =
  | 'init'
  | 'text_message'
  | 'audio_start'
  | 'audio_end'
  | 'interrupt'
  | 'ping';
