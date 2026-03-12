import { useState } from 'react';
import './VoicePanel.css';
import { useAgentStore } from '../../store/agentStore';
import type { AgentStatus, LatencyMetrics } from '../../types';

interface VoicePanelProps {
  status: AgentStatus;
  onStartRecording: () => void;
  onStopRecording: () => void;
  latencyMetrics: LatencyMetrics | null;
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  idle: 'Idle',
  connecting: 'Connecting',
  ready: 'Ready — press to speak',
  recording: 'Recording',
  processing: 'Processing',
  speaking: 'Speaking',
  error: 'Error',
};

function LatencyValue({ ms, threshold }: { ms: number; threshold: number }) {
  const cls = ms === 0 ? '' : ms <= threshold ? 'latency-card__value--good' : ms <= threshold * 1.5 ? 'latency-card__value--warn' : 'latency-card__value--over';
  return (
    <div className={`latency-card__value ${cls}`}>
      {ms > 0 ? `${ms}ms` : '--'}
    </div>
  );
}

// Inline SVG icons — no emoji, no library dependency
function MicIcon() {
  return (
    <svg className="mic-button__icon" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="19" x2="12" y2="22" />
      <line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg className="mic-button__icon" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg className="mic-button__icon" viewBox="0 0 24 24" aria-hidden="true" style={{ animation: 'spin 0.8s linear infinite' }}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" fill="none" strokeDasharray="30 56" />
    </svg>
  );
}

export function VoicePanel({ status, onStartRecording, onStopRecording, latencyMetrics }: VoicePanelProps) {
  const { liveTranscript, toolCallsTrace } = useAgentStore();
  const [reasoningOpen, setReasoningOpen] = useState(true);

  const isRecording = status === 'recording';
  const isProcessing = status === 'processing';
  const isSpeaking = status === 'speaking';
  const canInteract = status === 'ready' || isRecording || isSpeaking;

  function handleMicClick() {
    if (isRecording) {
      onStopRecording();
    } else if (canInteract) {
      onStartRecording();
    }
  }

  return (
    <section className="voice-panel" aria-label="Voice controls">
      <div className="voice-panel__title">Voice Input</div>

      {/* ── Mic button ─────────────────────────────────────────────────── */}
      <div className={`mic-button-wrapper${isRecording ? ' mic-button-wrapper--recording' : ''}`}>
        <button
          type="button"
          className={`mic-button ${isRecording ? 'mic-button--recording' : ''} ${isProcessing ? 'mic-button--processing' : ''}`}
          onClick={handleMicClick}
          disabled={!canInteract || isProcessing}
          aria-label={isRecording ? 'Stop recording' : 'Start recording'}
          title={isRecording ? 'Stop recording' : 'Start recording'}
        >
          {isProcessing ? <SpinnerIcon /> : isRecording ? <StopIcon /> : <MicIcon />}
        </button>
      </div>

      {/* ── Status text ────────────────────────────────────────────────── */}
      <div className={`voice-panel__status voice-panel__status--${status}`} aria-live="polite">
        {STATUS_LABEL[status] ?? status}
      </div>

      {/* ── Live transcript ─────────────────────────────────────────────── */}
      {(isRecording || liveTranscript) && (
        <div className="voice-panel__transcript" aria-live="polite" aria-label="Live transcription">
          {liveTranscript || 'Listening...'}
        </div>
      )}

      {/* ── Latency metrics ─────────────────────────────────────────────── */}
      {latencyMetrics && (
        <div className="voice-panel__latency voice-panel__latency--4col" aria-label="Pipeline latency">
          <div className="latency-card">
            <div className="latency-card__label">ASR</div>
            <LatencyValue ms={latencyMetrics.asr_ms ?? 0} threshold={150} />
          </div>
          <div className="latency-card">
            <div className="latency-card__label">LLM</div>
            <LatencyValue ms={latencyMetrics.llm_ms ?? 0} threshold={200} />
          </div>
          <div className="latency-card">
            <div className="latency-card__label">TTS</div>
            <LatencyValue ms={latencyMetrics.tts_ms ?? 0} threshold={100} />
          </div>
          <div className="latency-card latency-card--total">
            <div className="latency-card__label">Total</div>
            <LatencyValue ms={latencyMetrics.total_ms ?? 0} threshold={450} />
          </div>
        </div>
      )}

      {/* ── Agent reasoning trace ────────────────────────────────────────── */}
      {toolCallsTrace.length > 0 && (
        <div className="voice-panel__reasoning" aria-label="Agent reasoning trace">
          <button
            type="button"
            className="reasoning__toggle"
            onClick={() => setReasoningOpen(o => !o)}
            aria-expanded={reasoningOpen}
          >
            <span>Agent Reasoning</span>
            <span className="reasoning__badge">{toolCallsTrace.length}</span>
            <svg
              className={`reasoning__chevron${reasoningOpen ? ' reasoning__chevron--open' : ''}`}
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
          {reasoningOpen && (
            <ol className="reasoning__list">
              {toolCallsTrace.map((call, i) => (
                <li key={call.id ?? i} className="reasoning__item">
                  <span className="reasoning__tool">{call.tool}</span>
                  <pre className="reasoning__args">{JSON.stringify(call.args, null, 2)}</pre>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {/* ── Hint ────────────────────────────────────────────────────────── */}
      <div className="voice-panel__hint">
        {isRecording
          ? 'Press stop when finished speaking'
          : isSpeaking
          ? 'Press mic to interrupt the assistant'
          : 'Press the mic button or type below'}
      </div>
    </section>
  );
}
