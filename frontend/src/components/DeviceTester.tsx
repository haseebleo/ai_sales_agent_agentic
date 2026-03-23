import { useState, useRef, useEffect } from 'react';
import { Mic, Volume2, Settings2, CheckCircle, XCircle } from 'lucide-react';

type TestStatus = 'idle' | 'testing' | 'success' | 'error';

export const DeviceTester: React.FC = () => {
  const [micStatus, setMicStatus] = useState<TestStatus>('idle');
  const [speakerStatus, setSpeakerStatus] = useState<TestStatus>('idle');
  const [audioLevel, setAudioLevel] = useState(0);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);

  const testMic = async () => {
    try {
      setMicStatus('testing');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.frequencyBinCount);

      const tick = () => {
        analyser.getByteFrequencyData(buf);
        const avg = buf.reduce((a, b) => a + b, 0) / buf.length;
        setAudioLevel(avg);
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();

      setTimeout(() => {
        if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
        stream.getTracks().forEach((t) => t.stop());
        setMicStatus('success');
        setAudioLevel(0);
      }, 3000);
    } catch {
      setMicStatus('error');
    }
  };

  const testSpeaker = () => {
    setSpeakerStatus('testing');
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain).connect(ctx.destination);
      osc.type = 'sine';
      osc.frequency.setValueAtTime(440, ctx.currentTime);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.8);
      osc.start();
      osc.stop(ctx.currentTime + 0.8);
      setTimeout(() => setSpeakerStatus('success'), 900);
    } catch {
      setSpeakerStatus('error');
    }
  };

  useEffect(
    () => () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      if (ctxRef.current?.state !== 'closed') ctxRef.current?.close();
    },
    []
  );

  const StatusIcon: React.FC<{ status: TestStatus }> = ({ status }) => {
    if (status === 'success')
      return <CheckCircle size={14} color="var(--accent-green)" />;
    if (status === 'error')
      return <XCircle size={14} color="var(--accent-red)" />;
    return null;
  };

  return (
    <div
      style={{
        background: 'var(--surface)',
        padding: '20px',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--border)',
      }}
    >
      <h3
        style={{
          fontSize: '14px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          margin: '0 0 16px',
        }}
      >
        <Settings2 size={16} color="var(--text-muted)" /> Device Setup
      </h3>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        {/* Mic */}
        <div
          style={{
            background: 'var(--surface-hover)',
            padding: '14px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border)',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: '10px',
            }}
          >
            <span
              style={{
                fontSize: '12px',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <Mic size={14} /> Mic
            </span>
            <StatusIcon status={micStatus} />
          </div>
          <div
            style={{
              height: '3px',
              background: 'var(--surface)',
              borderRadius: '2px',
              overflow: 'hidden',
              marginBottom: '12px',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${Math.min(100, (audioLevel / 128) * 100)}%`,
                background: 'var(--accent-green)',
                transition: 'width 0.1s linear',
              }}
            />
          </div>
          <button
            onClick={testMic}
            disabled={micStatus === 'testing'}
            style={{
              width: '100%',
              padding: '7px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-heading)',
              fontSize: '12px',
            }}
          >
            {micStatus === 'testing' ? 'Listening…' : 'Test Mic'}
          </button>
        </div>

        {/* Speaker */}
        <div
          style={{
            background: 'var(--surface-hover)',
            padding: '14px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border)',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: '10px',
            }}
          >
            <span
              style={{
                fontSize: '12px',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <Volume2 size={14} /> Speaker
            </span>
            <StatusIcon status={speakerStatus} />
          </div>
          <p
            style={{
              fontSize: '11px',
              color: 'var(--text-muted)',
              margin: '0 0 12px',
              minHeight: '14px',
            }}
          >
            Plays a test tone.
          </p>
          <button
            onClick={testSpeaker}
            disabled={speakerStatus === 'testing'}
            style={{
              width: '100%',
              padding: '7px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-heading)',
              fontSize: '12px',
            }}
          >
            {speakerStatus === 'testing' ? 'Playing…' : 'Test Speaker'}
          </button>
        </div>
      </div>
    </div>
  );
};
