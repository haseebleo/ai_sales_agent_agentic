import { useState, useRef, useEffect } from 'react';
import { Terminal, Copy, ChevronDown, ChevronUp } from 'lucide-react';
import type { DiagnosticsData, CallState } from '../hooks/useVoiceClient';

export const DiagnosticsDrawer: React.FC<{
  data: DiagnosticsData;
  callState: CallState | string;
}> = ({ data, callState }) => {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [data.logs.length, open]);

  const copyLogs = () => {
    navigator.clipboard.writeText(data.logs.join('\n')).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          position: 'fixed',
          bottom: 12,
          right: 12,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          padding: '6px 14px',
          borderRadius: '14px',
          fontSize: '11px',
          color: 'var(--text-muted)',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          zIndex: 50,
        }}
      >
        <Terminal size={12} /> Diagnostics <ChevronUp size={12} />
      </button>
    );
  }

  const items: [string, string | number][] = [
    ['State', callState],
    ['WebSocket', data.connectionState],
    ['Mic', data.micPermission],
    ['Device', data.selectedDevice],
    ['Session', data.sessionId],
    ['Sent', data.chunksSent],
    ['Received', data.chunksReceived],
    ['Last Event', data.lastEvent || '—'],
  ];

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 12,
        right: 12,
        width: '420px',
        maxHeight: '440px',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--shadow-lg)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 100,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexShrink: 0,
        }}
      >
        <h4
          style={{
            fontSize: '13px',
            margin: 0,
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          <Terminal size={14} /> Diagnostics
        </h4>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={copyLogs}
            style={{
              color: 'var(--text-muted)',
              fontSize: '11px',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <Copy size={12} /> {copied ? 'Copied!' : 'Copy'}
          </button>
          <button
            onClick={() => setOpen(false)}
            style={{ color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}
          >
            <ChevronDown size={14} />
          </button>
        </div>
      </div>

      {/* Metrics grid */}
      <div
        style={{
          padding: '10px 14px',
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '6px',
          fontSize: '11px',
          fontFamily: 'monospace',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        {items.map(([k, v]) => (
          <div key={k} style={{ display: 'flex', gap: '4px' }}>
            <span style={{ color: 'var(--text-muted)' }}>{k}:</span>
            <span
              style={{
                color:
                  k === 'State'
                    ? 'var(--accent-main)'
                    : k === 'WebSocket' && v === 'open'
                      ? 'var(--accent-green)'
                      : 'var(--text-heading)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {v}
            </span>
          </div>
        ))}
      </div>

      {/* Log stream */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '8px 14px',
          fontSize: '10px',
          fontFamily: 'monospace',
          lineHeight: '1.6',
        }}
      >
        {data.logs.length === 0 && (
          <span style={{ color: 'var(--text-muted)' }}>No logs yet</span>
        )}
        {data.logs.map((line, i) => (
          <div
            key={i}
            style={{
              color: line.includes('[Error]')
                ? 'var(--accent-red)'
                : line.includes('[VAD]')
                  ? 'var(--accent-green)'
                  : 'var(--text-muted)',
            }}
          >
            {line}
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
    </div>
  );
};
