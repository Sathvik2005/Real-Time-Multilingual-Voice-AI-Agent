import { useState } from 'react';
import './App.css';
import { useAgentStore } from './store/agentStore';
import { useVoiceAgent } from './hooks/useVoiceAgent';
import { VoicePanel } from './components/VoicePanel/VoicePanel';
import { ChatPanel } from './components/ChatPanel/ChatPanel';
import { AppointmentStatus } from './components/AppointmentStatus/AppointmentStatus';
import { LanguageIndicator } from './components/LanguageIndicator/LanguageIndicator';

function App() {
  const [patientName] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('name') ?? 'Guest Patient';
  });

  const { status, error, clearError, sessionId, detectedLanguage, latencyMetrics } = useAgentStore();
  const { isInitialized, startRecording, stopRecording, sendText } = useVoiceAgent(patientName);

  const isConnecting = !isInitialized || status === 'idle' || status === 'connecting';

  return (
    <div className="app">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="app-header">
        <div>
          <div className="app-header__title">Clinic Voice Assistant</div>
          <div className="app-header__subtitle">Multilingual Appointment Scheduling</div>
        </div>
        <div className="app-header__meta">
          {sessionId && (
            <LanguageIndicator language={detectedLanguage} />
          )}
        </div>
      </header>

      {/* ── Error banner ────────────────────────────────────────────────── */}
      {error && (
        <div className="app-error-banner" role="alert">
          <span>{error}</span>
          <button type="button" onClick={clearError} aria-label="Dismiss error">
            x
          </button>
        </div>
      )}

      {/* ── Body ────────────────────────────────────────────────────────── */}
      {isConnecting ? (
        <div className="app-loading">
          <div className="app-loading__spinner" />
          <span>Connecting to clinic server...</span>
        </div>
      ) : (
        <div className="app-body">
          {/* Left sidebar */}
          <aside className="app-sidebar">
            <VoicePanel
              status={status}
              onStartRecording={startRecording}
              onStopRecording={stopRecording}
              latencyMetrics={latencyMetrics}
            />
            <AppointmentStatus />
          </aside>

          {/* Chat area */}
          <main className="app-main">
            <ChatPanel
              status={status}
              onSendText={sendText}
              patientName={patientName}
            />
          </main>
        </div>
      )}
    </div>
  );
}

export default App;
