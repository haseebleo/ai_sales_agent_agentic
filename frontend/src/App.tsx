import { useState } from 'react';
import { useVoiceClient } from './hooks/useVoiceClient';
import { TranscriptFeed } from './components/TranscriptFeed';
import { LeadPanel } from './components/LeadPanel';
import { DiagnosticsDrawer } from './components/DiagnosticsDrawer';
import { AudioVisualizer } from './components/AudioVisualizer';
import { DeviceTester } from './components/DeviceTester';
import {
  Phone,
  PhoneOff,
  AlertCircle,
  Bot,
  Activity,
  WifiOff,
  Send,
  Hand,
} from 'lucide-react';

const SESSION_ID = 'sess-' + Math.random().toString(36).substring(2, 9);

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

const STATE_LABELS: Record<string, { label: string; color: string }> = {
  idle: { label: 'Ready', color: 'var(--text-muted)' },
  requesting_permission: { label: 'Requesting Mic…', color: 'var(--accent-alt)' },
  connecting: { label: 'Connecting…', color: 'var(--accent-blue)' },
  listening: { label: 'Listening', color: 'var(--accent-green)' },
  processing: { label: 'Thinking…', color: 'var(--accent-purple)' },
  speaking: { label: 'Speaking', color: 'var(--accent-main)' },
  interrupted: { label: 'Interrupted', color: 'var(--accent-alt)' },
  reconnecting: { label: 'Reconnecting…', color: 'var(--accent-alt)' },
  error: { label: 'Error', color: 'var(--accent-red)' },
  ended: { label: 'Call Ended', color: 'var(--text-muted)' },
};

function App() {
  const {
    callState,
    errorMsg,
    messages,
    partialAgentText,
    leadData,
    diagnostics,
    startCall,
    endCall,
    interrupt,
    sendEndOfSpeech,
    sendTextMessage,
  } = useVoiceClient(SESSION_ID);

  const [textInput, setTextInput] = useState('');
  const [useMock, setUseMock] = useState(false);

  const submitText = (e: React.FormEvent) => {
    e.preventDefault();
    if (textInput.trim()) {
      sendTextMessage(textInput);
      setTextInput('');
    }
  };

  const isCallActive = !['idle', 'error', 'ended'].includes(callState);
  const stateInfo = STATE_LABELS[callState] ?? STATE_LABELS.idle;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '100vw',
        height: '100vh',
        background: 'var(--bg)',
        color: 'var(--text-body)',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <header
        style={{
          padding: '16px 32px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexShrink: 0,
          zIndex: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 'var(--radius-sm)',
              background: 'linear-gradient(135deg, var(--accent-main), var(--accent-alt))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 800,
              fontSize: '16px',
              boxShadow: 'var(--shadow-glow)',
            }}
          >
            TT
          </div>
          <div>
            <h1 style={{ fontSize: '20px', margin: 0, letterSpacing: '-0.5px' }}>
              Trango Tech AI
            </h1>
            <p
              style={{
                color: 'var(--text-muted)',
                fontSize: '11px',
                margin: '1px 0 0',
                textTransform: 'uppercase',
                letterSpacing: 1,
              }}
            >
              Sales Intelligence Console
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          {isCallActive && (
            <span
              style={{
                fontFamily: 'monospace',
                fontSize: '14px',
                color: 'var(--text-muted)',
                background: 'var(--surface-hover)',
                padding: '4px 12px',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              {formatDuration(diagnostics.callDuration)}
            </span>
          )}
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '12px',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={useMock}
              onChange={(e) => setUseMock(e.target.checked)}
              disabled={isCallActive}
              style={{ accentColor: 'var(--accent-alt)' }}
            />
            <WifiOff size={14} /> Mock
          </label>
        </div>
      </header>

      {/* Main content */}
      <div
        style={{
          flex: 1,
          padding: '24px 32px',
          overflow: 'hidden',
          display: 'flex',
          gap: '24px',
          maxWidth: '1600px',
          margin: '0 auto',
          width: '100%',
        }}
      >
        {/* Left Column: Controls */}
        <div
          style={{
            width: '320px',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
            flexShrink: 0,
            overflowY: 'auto',
          }}
        >
          {/* Agent Card */}
          <div
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              padding: '20px',
            }}
          >
            <h2
              style={{
                fontSize: '16px',
                margin: '0 0 12px',
                borderBottom: '1px solid var(--border)',
                paddingBottom: '10px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              <Bot size={18} color="var(--accent-blue)" /> Alex — AI Sales Agent
            </h2>

            {!isCallActive ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <p
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-muted)',
                    lineHeight: '1.5',
                    margin: 0,
                  }}
                >
                  Start a voice conversation with Alex, your AI Sales architect
                  at Trango Tech.
                </p>
                <button
                  onClick={() => startCall(useMock)}
                  style={{
                    width: '100%',
                    background: 'linear-gradient(135deg, var(--accent-main), #d63031)',
                    color: '#fff',
                    fontSize: '15px',
                    fontWeight: 600,
                    padding: '14px',
                    borderRadius: 'var(--radius-md)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    gap: '10px',
                    boxShadow: 'var(--shadow-glow)',
                  }}
                >
                  <Phone size={18} /> Start Call
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                {/* State badge */}
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    background: 'var(--surface-hover)',
                    padding: '10px 14px',
                    borderRadius: 'var(--radius-md)',
                  }}
                >
                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--text-muted)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.5px',
                    }}
                  >
                    Status
                  </span>
                  <span
                    style={{
                      color: stateInfo.color,
                      fontWeight: 700,
                      fontSize: '13px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: stateInfo.color,
                        display: 'inline-block',
                        animation:
                          callState === 'listening' || callState === 'speaking'
                            ? 'pulse-ring 1.5s infinite'
                            : 'none',
                      }}
                    />
                    {stateInfo.label}
                  </span>
                </div>

                <AudioVisualizer
                  isActive={['listening', 'speaking', 'processing'].includes(callState)}
                  state={callState}
                />

                {/* Action buttons */}
                <button
                  onClick={interrupt}
                  disabled={callState !== 'speaking'}
                  style={{
                    background:
                      callState === 'speaking'
                        ? 'rgba(255, 107, 107, 0.1)'
                        : 'var(--surface-hover)',
                    color:
                      callState === 'speaking'
                        ? 'var(--accent-red)'
                        : 'var(--text-muted)',
                    fontSize: '13px',
                    fontWeight: 600,
                    padding: '12px',
                    borderRadius: 'var(--radius-md)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    gap: '8px',
                    border: `1px solid ${callState === 'speaking' ? 'var(--accent-main)' : 'var(--border)'}`,
                  }}
                >
                  <Hand size={16} /> Interrupt
                </button>

                <button
                  onClick={sendEndOfSpeech}
                  disabled={callState !== 'listening'}
                  style={{
                    background: 'var(--surface-hover)',
                    color: 'var(--text-heading)',
                    fontSize: '13px',
                    fontWeight: 600,
                    padding: '12px',
                    borderRadius: 'var(--radius-md)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    gap: '8px',
                    border: '1px solid var(--border)',
                  }}
                >
                  <Activity size={16} /> Send Now
                </button>

                <button
                  onClick={endCall}
                  style={{
                    background: 'transparent',
                    color: 'var(--accent-red)',
                    fontSize: '13px',
                    fontWeight: 500,
                    padding: '10px',
                    borderRadius: 'var(--radius-md)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    gap: '8px',
                    border: '1px solid rgba(239,68,68,0.3)',
                    marginTop: '4px',
                  }}
                >
                  <PhoneOff size={16} /> End Call
                </button>
              </div>
            )}
          </div>

          {!isCallActive && <DeviceTester />}
        </div>

        {/* Center Column: Transcript + Text input */}
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            minWidth: 0,
          }}
        >
          {errorMsg && (
            <div
              style={{
                background: 'rgba(239, 68, 68, 0.08)',
                border: '1px solid var(--accent-red)',
                color: 'var(--accent-red)',
                padding: '12px 16px',
                borderRadius: 'var(--radius-md)',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                fontSize: '14px',
              }}
            >
              <AlertCircle size={18} />
              {errorMsg}
            </div>
          )}

          <TranscriptFeed
            messages={messages}
            partialAgentText={partialAgentText}
            state={callState}
          />

          <form
            onSubmit={submitText}
            style={{
              display: 'flex',
              gap: '12px',
              background: 'var(--surface)',
              padding: '12px',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border)',
              flexShrink: 0,
            }}
          >
            <input
              type="text"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              placeholder={isCallActive ? 'Type a message…' : 'Start a call first'}
              disabled={!isCallActive}
              style={{
                flex: 1,
                background: 'var(--surface-hover)',
                border: 'none',
                color: 'var(--text-heading)',
                padding: '12px 20px',
                borderRadius: 'var(--radius-md)',
                outline: 'none',
                fontSize: '14px',
              }}
            />
            <button
              type="submit"
              disabled={!isCallActive || !textInput.trim()}
              style={{
                background: 'var(--text-heading)',
                color: 'var(--surface)',
                padding: '0 24px',
                borderRadius: 'var(--radius-md)',
                fontWeight: 700,
                fontSize: '14px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <Send size={14} /> Send
            </button>
          </form>
        </div>

        {/* Right Column: Lead Panel */}
        <div style={{ flexShrink: 0 }}>
          <LeadPanel data={leadData} />
        </div>
      </div>

      <DiagnosticsDrawer data={diagnostics} callState={callState} />
    </div>
  );
}

export default App;
