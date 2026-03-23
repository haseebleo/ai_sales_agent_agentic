import { useEffect, useRef } from 'react';
import type { TranscriptMessage } from '../hooks/useVoiceClient';
import type { CallState } from '../hooks/useVoiceClient';
import { Bot, User } from 'lucide-react';

interface TranscriptFeedProps {
  messages: TranscriptMessage[];
  partialAgentText: string;
  state: CallState | string;
}

export const TranscriptFeed: React.FC<TranscriptFeedProps> = ({
  messages,
  partialAgentText,
  state,
}) => {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, partialAgentText]);

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        background: 'var(--surface)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--border)',
      }}
    >
      {messages.length === 0 && !partialAgentText && (
        <div
          style={{
            textAlign: 'center',
            color: 'var(--text-muted)',
            marginTop: 'auto',
            marginBottom: 'auto',
            padding: '40px 20px',
          }}
        >
          <Bot size={40} style={{ marginBottom: 12, opacity: 0.3 }} />
          <p style={{ fontSize: '15px', margin: 0 }}>
            Conversation will appear here
          </p>
          <p style={{ fontSize: '12px', margin: '6px 0 0', opacity: 0.6 }}>
            Start a call to begin talking with Alex
          </p>
        </div>
      )}

      {messages.map((m, i) => (
        <div
          key={i}
          style={{
            alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: '80%',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            animation: 'fadeInUp 0.2s ease-out',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '11px',
              color: 'var(--text-muted)',
              paddingLeft: m.role === 'user' ? '0' : '4px',
              paddingRight: m.role === 'user' ? '4px' : '0',
              justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
            }}
          >
            {m.role !== 'user' && <Bot size={12} />}
            {m.role === 'user' ? 'You' : 'Alex'}
            <span style={{ opacity: 0.5 }}>{formatTime(m.timestamp)}</span>
            {m.role === 'user' && <User size={12} />}
          </div>
          <div
            style={{
              padding: '10px 14px',
              borderRadius:
                m.role === 'user'
                  ? '14px 14px 4px 14px'
                  : '14px 14px 14px 4px',
              background:
                m.role === 'user' ? 'var(--accent-main)' : 'var(--surface-hover)',
              color: m.role === 'user' ? '#fff' : 'var(--text-heading)',
              fontSize: '14px',
              lineHeight: '1.5',
              border:
                m.role !== 'user' ? '1px solid var(--border)' : 'none',
            }}
          >
            {m.text}
          </div>
        </div>
      ))}

      {partialAgentText && (
        <div
          style={{
            alignSelf: 'flex-start',
            maxWidth: '80%',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '11px',
              color: 'var(--text-muted)',
              paddingLeft: '4px',
            }}
          >
            <Bot size={12} /> Alex
          </div>
          <div
            style={{
              padding: '10px 14px',
              borderRadius: '14px 14px 14px 4px',
              background: 'var(--surface-hover)',
              color: 'var(--text-heading)',
              fontSize: '14px',
              lineHeight: '1.5',
              border: '1px solid var(--border)',
            }}
          >
            {partialAgentText}
            <span
              style={{
                display: 'inline-block',
                width: 2,
                height: 14,
                background: 'var(--accent-main)',
                marginLeft: 2,
                animation: 'pulse-ring 0.8s infinite',
                verticalAlign: 'text-bottom',
              }}
            />
          </div>
        </div>
      )}

      {state === 'processing' && !partialAgentText && (
        <div
          style={{
            alignSelf: 'flex-start',
            color: 'var(--text-muted)',
            fontSize: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '4px',
          }}
        >
          <span
            style={{
              display: 'flex',
              gap: '4px',
            }}
          >
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                style={{
                  width: 6,
                  height: 6,
                  background: 'var(--accent-purple)',
                  borderRadius: '50%',
                  animation: `pulse-ring 1s infinite ${i * 0.2}s`,
                }}
              />
            ))}
          </span>
          Alex is thinking…
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
};
