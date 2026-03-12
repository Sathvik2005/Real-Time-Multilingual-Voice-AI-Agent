import { create } from 'zustand';
import type {
  AgentStatus,
  AppointmentSummary,
  ChatMessage,
  LanguageInfo,
  LatencyMetrics,
  ToolCallEntry,
} from '../types';

interface AgentStore {
  // Session
  sessionId: string | null;
  patientId: string | null;
  patientName: string;

  // Status
  status: AgentStatus;
  error: string;

  // Conversation
  messages: ChatMessage[];
  liveTranscript: string; // interim transcript while recording

  // Language
  detectedLanguage: LanguageInfo | null;

  // Appointments
  recentAppointments: AppointmentSummary[];

  // Performance
  latencyMetrics: LatencyMetrics | null;

  // Reasoning trace (tool calls from last turn)
  toolCallsTrace: ToolCallEntry[];

  // UI
  isVoiceMode: boolean;

  // Actions
  setSessionId: (id: string) => void;
  setPatientId: (id: string) => void;
  setPatientName: (name: string) => void;
  setStatus: (status: AgentStatus) => void;
  setError: (message: string) => void;
  clearError: () => void;
  addMessage: (message: ChatMessage) => void;
  clearMessages: () => void;
  setTranscript: (text: string) => void;
  clearTranscript: () => void;
  setDetectedLanguage: (lang: LanguageInfo) => void;
  setRecentAppointments: (appts: AppointmentSummary[]) => void;
  setLatencyMetrics: (metrics: LatencyMetrics) => void;
  setToolCallsTrace: (calls: ToolCallEntry[]) => void;
  setVoiceMode: (value: boolean) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  sessionId: null,
  patientId: null,
  patientName: '',
  status: 'idle',
  error: '',
  messages: [],
  liveTranscript: '',
  detectedLanguage: null,
  recentAppointments: [],
  latencyMetrics: null,
  toolCallsTrace: [],
  isVoiceMode: true,

  setSessionId: (id) => set({ sessionId: id }),
  setPatientId: (id) => set({ patientId: id }),
  setPatientName: (name) => set({ patientName: name }),
  setStatus: (status) => set({ status }),
  setError: (message) => set({ status: 'error', error: message }),
  clearError: () => set({ status: 'idle', error: '' }),

  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),

  clearMessages: () => set({ messages: [] }),
  setTranscript: (text) => set({ liveTranscript: text }),
  clearTranscript: () => set({ liveTranscript: '' }),
  setDetectedLanguage: (lang) => set({ detectedLanguage: lang }),
  setRecentAppointments: (appts) => set({ recentAppointments: appts }),
  setLatencyMetrics: (metrics) => set({ latencyMetrics: metrics }),
  setToolCallsTrace: (calls) => set({ toolCallsTrace: calls }),
  setVoiceMode: (value) => set({ isVoiceMode: value }),
}));
