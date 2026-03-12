import { useCallback, useEffect, useRef, useState } from 'react';
import { useAgentStore } from '../store/agentStore';
import { fetchNewSession } from '../services/api';
import { useWebSocket } from './useWebSocket';
import type { ChatMessage, ServerMessage } from '../types';

// Audio playback — buffers all incoming MP3 chunks, then decodes and plays
// the complete file on flush(). Decoding individual small MP3 fragments causes
// glitchy / blurry audio because each fragment lacks a valid MP3 header on its own.
class AudioQueue {
  private chunks: Uint8Array[] = [];
  private ctx: AudioContext | null = null;
  private activeSource: AudioBufferSourceNode | null = null;

  private getContext(): AudioContext {
    if (!this.ctx || this.ctx.state === 'closed') {
      this.ctx = new AudioContext();
    }
    return this.ctx;
  }

  enqueue(chunk: ArrayBuffer) {
    this.chunks.push(new Uint8Array(chunk));
  }

  // Called when audio_end arrives — decode the full concatenated MP3 and play it.
  async flush(): Promise<void> {
    if (this.chunks.length === 0) return;

    // Concatenate all chunks into one complete MP3 buffer
    const totalLen = this.chunks.reduce((n, c) => n + c.byteLength, 0);
    const combined = new Uint8Array(totalLen);
    let offset = 0;
    for (const c of this.chunks) { combined.set(c, offset); offset += c.byteLength; }
    this.chunks = [];

    try {
      const ctx = this.getContext();
      // decodeAudioData needs a copy — transfer ownership
      const decoded = await ctx.decodeAudioData(combined.buffer.slice(0));
      const source = ctx.createBufferSource();
      source.buffer = decoded;
      source.connect(ctx.destination);
      this.activeSource = source;
      return new Promise<void>((resolve) => {
        source.onended = () => { this.activeSource = null; resolve(); };
        source.start();
      });
    } catch (err) {
      console.error('[AudioQueue] decode error:', err);
    }
  }

  clear() {
    this.chunks = [];
    if (this.activeSource) {
      try { this.activeSource.stop(); } catch { /* already stopped */ }
      this.activeSource = null;
    }
  }
}

const audioQueue = new AudioQueue();

export function useVoiceAgent(patientName: string) {
  const {
    sessionId,
    status,
    setSessionId,
    setPatientId,
    setStatus,
    setError,
    addMessage,
    setTranscript,
    clearTranscript,
    setDetectedLanguage,
    setLatencyMetrics,
    setToolCallsTrace,
  } = useAgentStore();

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const isRecordingRef = useRef(false);
  const [isInitialized, setIsInitialized] = useState(false);
  // Synchronous guard prevents StrictMode's double-invocation from creating two sessions
  const initStartedRef = useRef(false);

  // ── Generate unique message ID ──────────────────────────────────────────
  const newMsgId = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

  // ── Handle server messages ─────────────────────────────────────────────
  const handleServerMessage = useCallback(
    (msg: ServerMessage) => {
      switch (msg.type) {
        case 'session_ready': {
          setStatus('ready');
          if (msg.patient_id != null) {
            setPatientId(String(msg.patient_id));
          }
          const welcomeText = (msg.message as string) || 'Session established.';
          addMessage({
            id: newMsgId(),
            role: 'agent',
            text: welcomeText,
            timestamp: new Date(),
          } satisfies ChatMessage);
          break;
        }

        case 'transcript': {
          const text = (msg.text as string) || '';
          const isFinal = (msg.is_final as boolean) || false;
          if (isFinal) {
            addMessage({
              id: newMsgId(),
              role: 'user',
              text,
              timestamp: new Date(),
              language: useAgentStore.getState().detectedLanguage?.code,
            });
            clearTranscript();
            setStatus('processing');
          } else {
            setTranscript(text);
          }
          break;
        }

        case 'language_detected': {
          setDetectedLanguage({
            code: msg.code as string,
            name: msg.name as string,
          });
          break;
        }

        case 'agent_text': {
          if (msg.is_final) {
            addMessage({
              id: newMsgId(),
              role: 'agent',
              text: (msg.text as string) || '',
              timestamp: new Date(),
              language: useAgentStore.getState().detectedLanguage?.code,
            });
            setStatus('speaking');
          }
          break;
        }

        case 'audio_chunk': {
          const b64 = (msg.data as string) || '';
          if (b64) {
            try {
              const binary = atob(b64);
              const bytes = new Uint8Array(binary.length);
              for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
              audioQueue.enqueue(bytes.buffer);
            } catch {
              // Ignore decode errors
            }
          }
          break;
        }

        case 'audio_end': {
          // Flush triggers full-buffer decode+playback; set ready after it finishes
          audioQueue.flush().then(() => setTimeout(() => setStatus('ready'), 100));
          break;
        }

        case 'latency_metrics': {
          setLatencyMetrics({
            asr_ms: msg.asr_ms as number,
            llm_ms: msg.llm_ms as number,
            tts_ms: msg.tts_ms as number,
            total_ms: msg.total_ms as number,
          });
          break;
        }

        case 'tool_calls': {
          // Reasoning trace — tool calls made by the agent this turn
          const calls = (msg.calls as Array<{ tool: string; args: Record<string, unknown>; id?: string }>) || [];
          setToolCallsTrace(calls);
          break;
        }

        case 'error': {
          setError((msg.message as string) || 'An error occurred.');
          setStatus('ready'); // Allow the user to retry
          break;
        }
      }
    },
    [addMessage, clearTranscript, setDetectedLanguage, setError, setLatencyMetrics, setStatus, setTranscript, setToolCallsTrace]
  );

  // ── WebSocket ──────────────────────────────────────────────────────────
  // use a ref so the onOpen closure always has the latest send function
  const sendRef = useRef<((data: object) => void) | null>(null);

  const { send } = useWebSocket({
    sessionId,
    onMessage: handleServerMessage,
    onOpen: () => {
      if (patientName && sendRef.current) {
        sendRef.current({ type: 'init', patient_name: patientName });
      }
    },
    onClose: () => {
      if (status !== 'idle') setStatus('idle');
    },
    onError: () => setError('WebSocket connection failed. Retrying...'),
  });

  // keep ref in sync after hook returns
  sendRef.current = send;

  // ── Initialise session ─────────────────────────────────────────────────
  useEffect(() => {
    if (isInitialized || initStartedRef.current) return;
    initStartedRef.current = true;

    (async () => {
      try {
        setStatus('connecting');
        const id = await fetchNewSession();
        setSessionId(id);
        setIsInitialized(true);
      } catch {
        setError('Unable to connect to the clinic server. Please try again.');
      }
    })();
  }, [isInitialized, setError, setSessionId, setStatus]);

  // ── Start voice recording ─────────────────────────────────────────────
  const startRecording = useCallback(async () => {
    if (isRecordingRef.current) return;
    if (status !== 'ready') return;

    // Interrupt any TTS playback (barge-in)
    audioQueue.clear();
    send({ type: 'interrupt' });

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });

      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      recorder.start(100); // collect in 100ms chunks
      mediaRecorderRef.current = recorder;
      isRecordingRef.current = true;
      setStatus('recording');
      send({ type: 'audio_start' });
    } catch (err) {
      setError('Microphone access denied. Please allow microphone permissions.');
    }
  }, [send, setError, setStatus, status]);

  // ── Stop voice recording ──────────────────────────────────────────────
  const stopRecording = useCallback(() => {
    if (!isRecordingRef.current || !mediaRecorderRef.current) return;
    isRecordingRef.current = false;

    const recorder = mediaRecorderRef.current;
    recorder.onstop = async () => {
      const blob = new Blob(audioChunksRef.current, { type: 'audio/webm;codecs=opus' });
      const arrayBuffer = await blob.arrayBuffer();
      // Safe chunked base64 encoding — avoids stack overflow for large audio blobs
      const bytes = new Uint8Array(arrayBuffer);
      let binary = '';
      const CHUNK = 8192;
      for (let offset = 0; offset < bytes.byteLength; offset += CHUNK) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + CHUNK));
      }
      const b64 = btoa(binary);
      send({ type: 'audio_end', audio: b64 });
      setStatus('processing');

      // Stop all tracks
      recorder.stream.getTracks().forEach((t) => t.stop());
    };

    recorder.stop();
  }, [send, setStatus]);

  // ── Send text message ─────────────────────────────────────────────────
  const sendText = useCallback(
    (text: string) => {
      if (!text.trim() || status === 'idle' || status === 'connecting') return;
      send({ type: 'text_message', text });
      addMessage({
        id: newMsgId(),
        role: 'user',
        text,
        timestamp: new Date(),
      });
      setStatus('processing');
    },
    [addMessage, send, setStatus, status]
  );

  return {
    isInitialized,
    startRecording,
    stopRecording,
    sendText,
  };
}
